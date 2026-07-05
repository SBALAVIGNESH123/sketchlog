"""Lightweight SketchLog Agent for Prometheus-to-SketchLog forwarding."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class AgentConfigError(ValueError):
    """Raised when agent configuration is invalid."""


class MappingKind(str, Enum):
    LATENCY = "latency"
    EVENT = "event"
    UNIQUE = "unique"


@dataclass(frozen=True)
class ServerConfig:
    endpoint: str
    namespace: str = "default"
    auth_token: Optional[str] = None
    auth_token_env: Optional[str] = None
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AgentConfigError("server.endpoint must be an http(s) URL")
        if not self.namespace or len(self.namespace) > 255 or "/" in self.namespace or self.namespace in {".", ".."}:
            raise AgentConfigError("server.namespace must be one safe path segment")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise AgentConfigError("server.timeout_seconds must be positive and finite")

    def resolved_auth_token(self) -> Optional[str]:
        if self.auth_token_env:
            value = os.environ.get(self.auth_token_env)
            if value:
                return value
        return self.auth_token


@dataclass(frozen=True)
class PrometheusTarget:
    name: str
    url: str
    timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.url)
        if not self.name:
            raise AgentConfigError("prometheus target name must not be empty")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AgentConfigError(f"prometheus target {self.name!r} url must be http(s)")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise AgentConfigError(f"prometheus target {self.name!r} timeout_seconds must be positive and finite")


@dataclass(frozen=True)
class StreamMapping:
    metric: str
    stream: str
    kind: MappingKind
    event_name: Optional[str] = None
    label_filters: Mapping[str, str] = field(default_factory=dict)
    value_scale: float = 1.0
    counter_mode: str = "delta"

    def __post_init__(self) -> None:
        if not self.metric:
            raise AgentConfigError("mapping.metric must not be empty")
        _validate_stream_path(self.stream)
        if not math.isfinite(self.value_scale) or self.value_scale <= 0:
            raise AgentConfigError("mapping.value_scale must be positive and finite")
        if self.kind is MappingKind.EVENT and not self.event_name:
            raise AgentConfigError("event mappings require event_name")
        if self.counter_mode not in {"delta", "absolute"}:
            raise AgentConfigError("mapping.counter_mode must be delta or absolute")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StreamMapping":
        try:
            kind = MappingKind(str(raw["kind"]))
        except KeyError as exc:
            raise AgentConfigError("mapping.kind is required") from exc
        except ValueError as exc:
            raise AgentConfigError("mapping.kind must be latency, event, or unique") from exc
        filters = raw.get("label_filters", {})
        if not isinstance(filters, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in filters.items()):
            raise AgentConfigError("mapping.label_filters must be an object of string keys/values")
        return cls(
            metric=str(raw.get("metric", "")),
            stream=str(raw.get("stream", "")),
            kind=kind,
            event_name=str(raw["event_name"]) if "event_name" in raw else None,
            label_filters=filters,
            value_scale=float(raw.get("value_scale", 1.0)),
            counter_mode=str(raw.get("counter_mode", "delta")),
        )


@dataclass(frozen=True)
class AgentConfig:
    server: ServerConfig
    prometheus_targets: Sequence[PrometheusTarget]
    mappings: Sequence[StreamMapping]
    interval_seconds: float = 15.0
    max_batch_items: int = 1000

    def __post_init__(self) -> None:
        if not math.isfinite(self.interval_seconds) or self.interval_seconds <= 0:
            raise AgentConfigError("interval_seconds must be positive and finite")
        if self.max_batch_items < 1:
            raise AgentConfigError("max_batch_items must be >= 1")
        if not self.prometheus_targets:
            raise AgentConfigError("at least one prometheus target is required")
        if not self.mappings:
            raise AgentConfigError("at least one mapping is required")


@dataclass(frozen=True)
class PrometheusSample:
    name: str
    labels: Mapping[str, str]
    value: float


@dataclass
class EventBatch:
    latencies: List[float] = field(default_factory=list)
    uniques: List[str] = field(default_factory=list)
    events: Dict[str, int] = field(default_factory=dict)

    def item_count(self) -> int:
        return len(self.latencies) + len(self.uniques) + len(self.events)

    def as_payload(self) -> Dict[str, Any]:
        return {"latencies": self.latencies, "uniques": self.uniques, "events": self.events}


@dataclass(frozen=True)
class AgentRunStats:
    scraped_targets: int
    matched_samples: int
    forwarded_batches: int
    forwarded_items: int
    skipped_samples: int


_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')
_SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[-+]?Inf|NaN)(?:\s+\d+)?$")


def load_config(path: str) -> AgentConfig:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise AgentConfigError("agent config must be a JSON object")
    server_raw = raw.get("server")
    if not isinstance(server_raw, dict):
        raise AgentConfigError("server config is required")
    prometheus_raw = raw.get("prometheus")
    targets_raw = prometheus_raw.get("targets") if isinstance(prometheus_raw, dict) else None
    mappings_raw = raw.get("mappings")
    if not isinstance(targets_raw, list):
        raise AgentConfigError("prometheus.targets must be a list")
    if not isinstance(mappings_raw, list):
        raise AgentConfigError("mappings must be a list")
    prometheus_targets: List[PrometheusTarget] = []
    for index, item in enumerate(targets_raw):
        if not isinstance(item, dict):
            raise AgentConfigError(f"prometheus.targets[{index}] must be an object")
        prometheus_targets.append(
            PrometheusTarget(
                str(item.get("name", "")),
                str(item.get("url", "")),
                float(item.get("timeout_seconds", 3.0)),
            )
        )

    mappings: List[StreamMapping] = []
    for index, item in enumerate(mappings_raw):
        if not isinstance(item, dict):
            raise AgentConfigError(f"mappings[{index}] must be an object")
        mappings.append(StreamMapping.from_dict(item))

    return AgentConfig(
        server=ServerConfig(
            endpoint=str(server_raw.get("endpoint", "")),
            namespace=str(server_raw.get("namespace", "default")),
            auth_token=str(server_raw["auth_token"]) if "auth_token" in server_raw else None,
            auth_token_env=str(server_raw["auth_token_env"]) if "auth_token_env" in server_raw else None,
            timeout_seconds=float(server_raw.get("timeout_seconds", 3.0)),
        ),
        prometheus_targets=prometheus_targets,
        mappings=mappings,
        interval_seconds=float(raw.get("interval_seconds", 15.0)),
        max_batch_items=int(raw.get("max_batch_items", 1000)),
    )


def parse_prometheus_text(text: str) -> List[PrometheusSample]:
    samples: List[PrometheusSample] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if not match:
            continue
        value = _parse_float(match.group(3))
        if value is None:
            continue
        samples.append(PrometheusSample(match.group(1), _parse_labels(match.group(2) or ""), value))
    return samples


def _parse_float(value: str) -> Optional[float]:
    normalized = value.replace("+Inf", "inf").replace("-Inf", "-inf").replace("Inf", "inf")
    try:
        parsed = float(normalized)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_labels(raw: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for match in _LABEL_RE.finditer(raw):
        labels[match.group(1)] = _unescape_label_value(match.group(2))
    return labels


def _unescape_label_value(value: str) -> str:
    result: List[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            result.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped == "n":
            result.append("\n")
        elif escaped in {"\\", '"'}:
            result.append(escaped)
        else:
            result.append("\\")
            result.append(escaped)
        index += 2
    return "".join(result)


class SketchLogAgent:
    """Scrapes configured Prometheus targets and forwards mapped samples."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._last_counter_values: Dict[Tuple[str, str, int, str, Tuple[Tuple[str, str], ...], str, str], float] = {}

    def run_once(self) -> AgentRunStats:
        batches: Dict[str, EventBatch] = {}
        matched = 0
        skipped = 0
        scraped = 0
        for target in self.config.prometheus_targets:
            text = self._scrape_target(target)
            scraped += 1
            for sample in parse_prometheus_text(text):
                any_match = False
                for mapping_index, mapping in enumerate(self.config.mappings):
                    if not _mapping_matches(mapping, sample):
                        continue
                    any_match = True
                    if self._apply_mapping(target, mapping_index, mapping, sample, batches):
                        matched += 1
                    else:
                        skipped += 1
                if not any_match:
                    skipped += 1
        batch_count, item_count = self._flush_batches(batches)
        return AgentRunStats(scraped, matched, batch_count, item_count, skipped)

    def run_forever(self) -> None:
        while True:
            try:
                stats = self.run_once()
            except (OSError, RuntimeError, urllib.error.URLError) as exc:
                print(json.dumps({"error": str(exc), "status": "error"}, sort_keys=True), file=sys.stderr, flush=True)
            else:
                print(json.dumps(stats.__dict__, sort_keys=True), flush=True)
            time.sleep(self.config.interval_seconds)

    def _scrape_target(self, target: PrometheusTarget) -> str:
        request = urllib.request.Request(target.url, headers={"User-Agent": "sketchlog-agent/1.0"})
        with urllib.request.urlopen(request, timeout=target.timeout_seconds) as response:  # nosec B310 - operator configured scrape URL
            body: bytes = response.read()
            return body.decode("utf-8", errors="replace")

    def _apply_mapping(self, target: PrometheusTarget, mapping_index: int, mapping: StreamMapping, sample: PrometheusSample, batches: Dict[str, EventBatch]) -> bool:
        value = sample.value * mapping.value_scale
        if not math.isfinite(value):
            return False
        batch = batches.setdefault(mapping.stream, EventBatch())
        if mapping.kind is MappingKind.LATENCY:
            if value < 0:
                return False
            batch.latencies.append(value)
            return True
        if mapping.kind is MappingKind.UNIQUE:
            batch.uniques.append(str(int(value)) if value.is_integer() else str(value))
            return True
        return self._apply_event_mapping(target, mapping_index, mapping, sample, batch, value)

    def _apply_event_mapping(self, target: PrometheusTarget, mapping_index: int, mapping: StreamMapping, sample: PrometheusSample, batch: EventBatch, value: float) -> bool:
        if value < 0 or mapping.event_name is None:
            return False
        if mapping.counter_mode == "delta":
            key = (target.name, target.url, mapping_index, mapping.metric, tuple(sorted(sample.labels.items())), mapping.stream, mapping.event_name or "")
            previous = self._last_counter_values.get(key)
            self._last_counter_values[key] = value
            if previous is None:
                return False
            value = max(0.0, value - previous)
        count = int(round(value))
        if count <= 0:
            return False
        batch.events[mapping.event_name] = batch.events.get(mapping.event_name, 0) + count
        return True

    def _flush_batches(self, batches: Mapping[str, EventBatch]) -> Tuple[int, int]:
        batch_count = 0
        item_count = 0
        for stream, batch in batches.items():
            if batch.item_count() == 0:
                continue
            for chunk in _chunk_batch(batch, self.config.max_batch_items):
                self._send_batch(stream, chunk)
                batch_count += 1
                item_count += chunk.item_count()
        return batch_count, item_count

    def _send_batch(self, stream: str, batch: EventBatch) -> None:
        url = _stream_events_url(self.config.server.endpoint, self.config.server.namespace, stream)
        body = json.dumps(batch.as_payload(), separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "sketchlog-agent/1.0"}
        token = self.config.server.resolved_auth_token()
        if token:
            headers["X-SketchLog-Auth-Token"] = token
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.config.server.timeout_seconds) as response:  # nosec B310 - configured SketchLog endpoint
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"SketchLog returned HTTP {response.status} for stream {stream!r}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"SketchLog returned HTTP {exc.code} for stream {stream!r}: {detail}") from exc


def _mapping_matches(mapping: StreamMapping, sample: PrometheusSample) -> bool:
    if sample.name != mapping.metric:
        return False
    return all(sample.labels.get(key) == value for key, value in mapping.label_filters.items())


def _chunk_batch(batch: EventBatch, max_items: int) -> Iterable[EventBatch]:
    current = EventBatch()
    for latency_value in batch.latencies:
        if current.item_count() >= max_items:
            yield current
            current = EventBatch()
        current.latencies.append(latency_value)
    for unique_value in batch.uniques:
        if current.item_count() >= max_items:
            yield current
            current = EventBatch()
        current.uniques.append(unique_value)
    for name, count in batch.events.items():
        if current.item_count() >= max_items:
            yield current
            current = EventBatch()
        current.events[name] = count
    if current.item_count() > 0:
        yield current


def _validate_stream_path(stream: str) -> None:
    if not stream or len(stream) > 255 or stream.startswith("/") or any(part in {"", ".", ".."} for part in stream.split("/")):
        raise AgentConfigError("stream names must be 1-255 characters and must not contain empty or dot path segments")


def _stream_events_url(endpoint: str, namespace: str, stream: str) -> str:
    _validate_stream_path(stream)
    parsed = urllib.parse.urlsplit(endpoint)
    base_path = parsed.path.rstrip("/")
    encoded_stream = "/".join(urllib.parse.quote(part, safe="") for part in stream.split("/"))
    path = f"{base_path}/v1/namespaces/{urllib.parse.quote(namespace, safe='')}/streams/{encoded_stream}/events"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SketchLog Agent: scrape Prometheus metrics and forward mapped streams.")
    parser.add_argument("--config", required=True, help="Path to agent JSON config")
    parser.add_argument("--once", action="store_true", help="Run one scrape/forward cycle and exit")
    args = parser.parse_args(argv)
    try:
        agent = SketchLogAgent(load_config(args.config))
        if args.once:
            print(json.dumps(agent.run_once().__dict__, sort_keys=True))
        else:
            agent.run_forever()
    except (AgentConfigError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"sketchlog-agent: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
