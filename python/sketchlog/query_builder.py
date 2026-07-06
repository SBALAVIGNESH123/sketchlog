"""
sketchlog.query_builder — Streaming SQL query builder for SketchLog.

Provides:
- Stream/function autocomplete
- Query validation (syntax + semantic)
- Saved queries and query history (JSON persistence)
- Example query templates
- Explain-plan output
- Copyable API request generation
- CLI entry point: sketchlog-query-builder
"""

from __future__ import annotations

import argparse
import dataclasses
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
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "1.0.0"

_KEYWORDS = frozenset(
    [
        "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "LIMIT", "OFFSET",
        "HAVING", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON", "AS",
        "AND", "OR", "NOT", "IN", "BETWEEN", "LIKE", "IS", "NULL", "TRUE",
        "FALSE", "DISTINCT", "WITH", "UNION", "ALL", "EXCEPT", "INTERSECT",
        "WINDOW", "OVER", "PARTITION", "TUMBLE", "HOP", "SESSION", "INTERVAL",
        "EMIT", "CHANGES", "FINAL",
    ]
)

_AGGREGATE_FUNCTIONS = frozenset(
    [
        "COUNT", "SUM", "AVG", "MIN", "MAX",
        "APPROX_QUANTILE", "APPROX_COUNT_DISTINCT", "APPROX_FREQUENCY",
        "SKETCH_MERGE", "SKETCH_QUANTILE", "SKETCH_COUNT",
        "PERCENTILE_APPROX", "STDDEV", "VARIANCE",
    ]
)

_SCALAR_FUNCTIONS = frozenset(
    [
        "ABS", "CEIL", "FLOOR", "ROUND", "SQRT", "LOG", "EXP", "POW",
        "COALESCE", "NULLIF", "CAST", "TRY_CAST", "IF", "CASE",
        "CONCAT", "SUBSTR", "TRIM", "UPPER", "LOWER", "LENGTH", "REPLACE",
        "NOW", "CURRENT_TIMESTAMP", "DATE_TRUNC", "DATE_DIFF", "TO_TIMESTAMP",
        "UNIX_TIMESTAMP", "FROM_UNIXTIME",
    ]
)

ALL_FUNCTIONS = _AGGREGATE_FUNCTIONS | _SCALAR_FUNCTIONS

_EXAMPLE_TEMPLATES: List[Dict[str, str]] = [
    {
        "name": "p99 latency per service (5 min tumble)",
        "description": "Approximate 99th-percentile request latency per service, bucketed into 5-minute windows.",
        "sql": (
            "SELECT\n"
            "  service,\n"
            "  TUMBLE_START(ts, INTERVAL '5' MINUTE) AS window_start,\n"
            "  APPROX_QUANTILE(latency_ms, 0.99)     AS p99_ms\n"
            "FROM request_latency\n"
            "GROUP BY service, TUMBLE(ts, INTERVAL '5' MINUTE)\n"
            "EMIT FINAL;"
        ),
    },
    {
        "name": "top-10 error events per minute",
        "description": "Count error events per endpoint, emit the top 10 each minute.",
        "sql": (
            "SELECT\n"
            "  endpoint,\n"
            "  TUMBLE_START(ts, INTERVAL '1' MINUTE) AS window_start,\n"
            "  COUNT(*) AS error_count\n"
            "FROM error_events\n"
            "WHERE status_code >= 500\n"
            "GROUP BY endpoint, TUMBLE(ts, INTERVAL '1' MINUTE)\n"
            "ORDER BY error_count DESC\n"
            "LIMIT 10\n"
            "EMIT FINAL;"
        ),
    },
    {
        "name": "unique active users (HLL)",
        "description": "Approximate distinct active users per namespace per hour.",
        "sql": (
            "SELECT\n"
            "  namespace,\n"
            "  TUMBLE_START(ts, INTERVAL '1' HOUR) AS hour,\n"
            "  APPROX_COUNT_DISTINCT(user_id)       AS dau\n"
            "FROM user_activity\n"
            "GROUP BY namespace, TUMBLE(ts, INTERVAL '1' HOUR)\n"
            "EMIT FINAL;"
        ),
    },
    {
        "name": "sketch merge across shards",
        "description": "Merge DDSketch shards and query the merged percentiles.",
        "sql": (
            "SELECT\n"
            "  SKETCH_QUANTILE(SKETCH_MERGE(latency_sketch), 0.50) AS p50_ms,\n"
            "  SKETCH_QUANTILE(SKETCH_MERGE(latency_sketch), 0.95) AS p95_ms,\n"
            "  SKETCH_QUANTILE(SKETCH_MERGE(latency_sketch), 0.99) AS p99_ms\n"
            "FROM shard_sketches\n"
            "WHERE shard_id IN ('shard-0','shard-1','shard-2');"
        ),
    },
    {
        "name": "frequency top-K items",
        "description": "Most frequent item values in a stream over a 10-minute window.",
        "sql": (
            "SELECT\n"
            "  item_key,\n"
            "  APPROX_FREQUENCY(item_key, 0.001) AS freq\n"
            "FROM item_events\n"
            "GROUP BY item_key, TUMBLE(ts, INTERVAL '10' MINUTE)\n"
            "ORDER BY freq DESC\n"
            "LIMIT 20\n"
            "EMIT FINAL;"
        ),
    },
]


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str
    position: Optional[int] = None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: List[ValidationError]
    warnings: List[ValidationError]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [dataclasses.asdict(e) for e in self.errors],
            "warnings": [dataclasses.asdict(w) for w in self.warnings],
        }


# ---------------------------------------------------------------------------
# Autocomplete
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompletionItem:
    label: str
    kind: str          # "keyword" | "function" | "stream" | "column"
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def autocomplete(
    prefix: str,
    streams: Optional[List[str]] = None,
    columns: Optional[Dict[str, List[str]]] = None,
) -> List[CompletionItem]:
    """Return completion items whose label starts with *prefix* (case-insensitive)."""
    prefix_upper = prefix.upper()
    items: List[CompletionItem] = []

    for kw in sorted(_KEYWORDS):
        if kw.startswith(prefix_upper):
            items.append(CompletionItem(label=kw, kind="keyword"))

    for fn in sorted(ALL_FUNCTIONS):
        if fn.startswith(prefix_upper):
            kind = "aggregate_function" if fn in _AGGREGATE_FUNCTIONS else "scalar_function"
            items.append(CompletionItem(label=fn + "()", kind=kind))

    for s in sorted(streams or []):
        if s.upper().startswith(prefix_upper):
            items.append(CompletionItem(label=s, kind="stream", detail="stream"))

    for table, cols in (columns or {}).items():
        for col in sorted(cols):
            label = f"{table}.{col}"
            if label.upper().startswith(prefix_upper) or col.upper().startswith(prefix_upper):
                items.append(CompletionItem(label=label, kind="column", detail=f"column of {table}"))

    return items


# ---------------------------------------------------------------------------
# Query validation
# ---------------------------------------------------------------------------

_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_FROM_RE = re.compile(r"\bFROM\b", re.IGNORECASE)
_UNBALANCED_PAREN_RE = re.compile(r"[()]")
_SEMICOLON_RE = re.compile(r";")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_FUNCTION_CALL_RE = re.compile(r"\b([A-Z_][A-Z0-9_]*)\s*\(", re.IGNORECASE)
_WINDOW_FUNC_RE = re.compile(r"\b(TUMBLE|HOP|SESSION)\s*\(", re.IGNORECASE)
_EMIT_RE = re.compile(r"\bEMIT\s+(FINAL|CHANGES)\b", re.IGNORECASE)
_STAR_FROM_RE = re.compile(r"SELECT\s+\*\s+FROM", re.IGNORECASE)


def validate_query(sql: str) -> ValidationResult:
    """Validate a SketchLog Streaming SQL query string."""
    errors: List[ValidationError] = []
    warnings: List[ValidationError] = []

    stripped = _COMMENT_RE.sub("", sql).strip()

    if not stripped:
        errors.append(ValidationError(code="EMPTY_QUERY", message="Query is empty."))
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # Must have SELECT
    if not _SELECT_RE.search(stripped):
        errors.append(ValidationError(
            code="MISSING_SELECT",
            message="Query must contain a SELECT clause.",
        ))

    # Must have FROM
    if not _FROM_RE.search(stripped):
        errors.append(ValidationError(
            code="MISSING_FROM",
            message="Query must contain a FROM clause.",
        ))

    # Balanced parentheses
    depth = 0
    for i, ch in enumerate(stripped):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                errors.append(ValidationError(
                    code="UNBALANCED_PAREN",
                    message="Unmatched closing parenthesis.",
                    position=i,
                ))
                break
    if depth > 0:
        errors.append(ValidationError(
            code="UNBALANCED_PAREN",
            message=f"Unclosed parenthesis ({depth} opening(s) not closed).",
        ))

    # Unknown functions
    known_upper = {fn.upper() for fn in ALL_FUNCTIONS}
    sql_upper = stripped.upper()
    for m in _FUNCTION_CALL_RE.finditer(stripped):
        fn_name = m.group(1).upper()
        if fn_name in _KEYWORDS:
            continue
        if fn_name not in known_upper:
            warnings.append(ValidationError(
                code="UNKNOWN_FUNCTION",
                message=f"Unknown function '{fn_name}'. Verify it is supported by the SketchLog SQL engine.",
                position=m.start(),
            ))

    # Windowed aggregate without EMIT
    if _WINDOW_FUNC_RE.search(stripped) and not _EMIT_RE.search(stripped):
        warnings.append(ValidationError(
            code="MISSING_EMIT",
            message="Windowed queries should include EMIT FINAL or EMIT CHANGES.",
        ))

    # SELECT * warning
    if _STAR_SELECT := _STAR_FROM_RE.search(stripped):
        warnings.append(ValidationError(
            code="SELECT_STAR",
            message="SELECT * fetches all columns. Prefer explicit column names for production queries.",
            position=_STAR_SELECT.start(),
        ))

    # No semicolon at end
    if not _SEMICOLON_RE.search(stripped):
        warnings.append(ValidationError(
            code="MISSING_SEMICOLON",
            message="Query does not end with a semicolon.",
        ))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Explain plan (offline stub — real plan comes from the server)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExplainPlan:
    query: str
    steps: List[str]
    estimated_streams: List[str]
    uses_sketch: bool
    uses_window: bool
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def render_text(self) -> str:
        lines = [
            "Query Explain Plan",
            "=" * 40,
        ]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"  {i}. {step}")
        lines.append("")
        lines.append(f"  Streams referenced : {', '.join(self.estimated_streams) or 'unknown'}")
        lines.append(f"  Uses sketch ops    : {'yes' if self.uses_sketch else 'no'}")
        lines.append(f"  Uses window        : {'yes' if self.uses_window else 'no'}")
        if self.note:
            lines.append(f"  Note               : {self.note}")
        return "\n".join(lines)


def explain_query(sql: str) -> ExplainPlan:
    """Produce a best-effort offline explain plan for *sql*."""
    stripped = _COMMENT_RE.sub("", sql).strip()

    # Extract stream names from FROM / JOIN clauses (simple regex heuristic)
    from_matches = re.findall(
        r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)",
        stripped,
        re.IGNORECASE,
    )
    streams = list(dict.fromkeys(from_matches))  # deduplicate, preserve order

    uses_sketch = bool(re.search(
        r"\b(APPROX_QUANTILE|APPROX_COUNT_DISTINCT|APPROX_FREQUENCY|SKETCH_MERGE|SKETCH_QUANTILE|SKETCH_COUNT)\b",
        stripped, re.IGNORECASE,
    ))
    uses_window = bool(_WINDOW_FUNC_RE.search(stripped))

    steps: List[str] = []
    if streams:
        steps.append(f"Scan stream(s): {', '.join(streams)}")
    where_m = re.search(r"\bWHERE\b", stripped, re.IGNORECASE)
    if where_m:
        steps.append("Apply WHERE filter")
    if uses_window:
        win_m = _WINDOW_FUNC_RE.search(stripped)
        fn = win_m.group(1).upper() if win_m else "WINDOW"
        steps.append(f"Assign {fn} window")
    if uses_sketch:
        steps.append("Accumulate sketch(es)")
    group_m = re.search(r"\bGROUP\s+BY\b", stripped, re.IGNORECASE)
    if group_m:
        steps.append("Group by key(s)")
    having_m = re.search(r"\bHAVING\b", stripped, re.IGNORECASE)
    if having_m:
        steps.append("Apply HAVING filter")
    order_m = re.search(r"\bORDER\s+BY\b", stripped, re.IGNORECASE)
    if order_m:
        steps.append("Sort results")
    limit_m = re.search(r"\bLIMIT\s+(\d+)\b", stripped, re.IGNORECASE)
    if limit_m:
        steps.append(f"Limit to {limit_m.group(1)} rows")
    emit_m = _EMIT_RE.search(stripped)
    if emit_m:
        steps.append(f"Emit {emit_m.group(1).upper()} results")

    if not steps:
        steps.append("Execute query")

    note = ""
    if uses_sketch:
        note = "Sketch functions use approximate algorithms; results are probabilistic."

    return ExplainPlan(
        query=sql,
        steps=steps,
        estimated_streams=streams,
        uses_sketch=uses_sketch,
        uses_window=uses_window,
        note=note,
    )


# ---------------------------------------------------------------------------
# API request builder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApiRequest:
    method: str
    url: str
    headers: Dict[str, str]
    body: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "headers": self.headers,
            "body": self.body,
        }

    def to_curl(self) -> str:
        parts = [f"curl -s -X {self.method} '{self.url}'"]
        for k, v in self.headers.items():
            parts.append(f"  -H '{k}: {v}'")
        if self.body:
            body_str = json.dumps(self.body, indent=2).replace("'", "'\\''")
            parts.append(f"  -d '{body_str}'")
        return " \\\n".join(parts)

    def to_python(self) -> str:
        return (
            "import urllib.request, json\n\n"
            f"url = {self.url!r}\n"
            f"headers = {self.headers!r}\n"
            f"body = {json.dumps(self.body, indent=2)}\n\n"
            "req = urllib.request.Request(\n"
            "    url, data=json.dumps(body).encode(), headers=headers, method='POST'\n"
            ")\n"
            "with urllib.request.urlopen(req) as resp:\n"
            "    result = json.load(resp)\n"
            "print(json.dumps(result, indent=2))\n"
        )


def build_api_request(
    sql: str,
    server_url: str,
    namespace: str = "default",
    token: Optional[str] = None,
    timeout_ms: int = 30_000,
) -> ApiRequest:
    """Build the HTTP request object that would execute *sql* against SketchLog."""
    base = server_url.rstrip("/")
    url = f"{base}/api/v1/namespaces/{urllib.parse.quote(namespace, safe='')}/query"
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    resolved_token = token or os.environ.get("SKETCHLOG_TOKEN", "")
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"
    body = {
        "sql": sql,
        "timeout_ms": timeout_ms,
    }
    return ApiRequest(method="POST", url=url, headers=headers, body=body)


# ---------------------------------------------------------------------------
# Saved queries + history (JSON persistence)
# ---------------------------------------------------------------------------

@dataclass
class SavedQuery:
    name: str
    sql: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SavedQuery":
        return cls(
            name=str(d.get("name", "")),
            sql=str(d.get("sql", "")),
            description=str(d.get("description", "")),
            tags=list(d.get("tags", [])),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )


@dataclass
class HistoryEntry:
    sql: str
    executed_at: float = field(default_factory=time.time)
    valid: bool = True
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HistoryEntry":
        return cls(
            sql=str(d.get("sql", "")),
            executed_at=float(d.get("executed_at", 0.0)),
            valid=bool(d.get("valid", True)),
            note=str(d.get("note", "")),
        )


class QueryStore:
    """
    Thread-*unsafe* in-process store for saved queries and history.
    Persists to a JSON file when *persist_path* is set.
    """

    _MAX_HISTORY = 200

    def __init__(self, persist_path: Optional[str] = None) -> None:
        self._persist_path = persist_path
        self._saved: Dict[str, SavedQuery] = {}
        self._history: List[HistoryEntry] = []
        if persist_path and os.path.exists(persist_path):
            self._load()

    # --- saved queries ---------------------------------------------------

    def save(self, query: SavedQuery) -> None:
        if not query.name:
            raise ValueError("Query name must not be empty.")
        if not query.sql.strip():
            raise ValueError("Query SQL must not be empty.")
        query.updated_at = time.time()
        self._saved[query.name] = query
        self._persist()

    def delete(self, name: str) -> bool:
        if name in self._saved:
            del self._saved[name]
            self._persist()
            return True
        return False

    def get(self, name: str) -> Optional[SavedQuery]:
        return self._saved.get(name)

    def list_saved(self, tag: Optional[str] = None) -> List[SavedQuery]:
        queries = list(self._saved.values())
        if tag:
            queries = [q for q in queries if tag in q.tags]
        return sorted(queries, key=lambda q: q.updated_at, reverse=True)

    # --- history ---------------------------------------------------------

    def push_history(self, entry: HistoryEntry) -> None:
        self._history.append(entry)
        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[-self._MAX_HISTORY:]
        self._persist()

    def list_history(self, limit: int = 20) -> List[HistoryEntry]:
        return list(reversed(self._history[-limit:]))

    def clear_history(self) -> None:
        self._history.clear()
        self._persist()

    # --- persistence -----------------------------------------------------

    def _persist(self) -> None:
        if not self._persist_path:
            return
        data = {
            "saved": {n: q.to_dict() for n, q in self._saved.items()},
            "history": [e.to_dict() for e in self._history],
        }
        tmp = self._persist_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, self._persist_path)
        except OSError:
            pass  # best-effort; do not crash the caller

    def _load(self) -> None:
        try:
            with open(self._persist_path, encoding="utf-8") as fh:  # type: ignore[arg-type]
                raw = fh.read().strip()
                if not raw:
                    return
                data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for n, q in (data.get("saved") or {}).items():
            if isinstance(q, dict):
                try:
                    self._saved[n] = SavedQuery.from_dict(q)
                except (TypeError, ValueError):
                    pass
        for e in (data.get("history") or []):
            if isinstance(e, dict):
                try:
                    self._history.append(HistoryEntry.from_dict(e))
                except (TypeError, ValueError):
                    pass


# ---------------------------------------------------------------------------
# QueryBuilderConfig
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueryBuilderConfig:
    server_url: str = ""
    namespace: str = "default"
    persist_path: str = ""
    token: str = ""
    timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        errors: List[str] = []
        if self.server_url and not self.server_url.startswith("https://"):
            errors.append("server_url must start with https://")
        if isinstance(self.namespace, bool) or not isinstance(self.namespace, str):
            errors.append("namespace must be a string")
        elif not self.namespace.strip():
            errors.append("namespace must not be empty")
        if isinstance(self.timeout_ms, bool) or not isinstance(self.timeout_ms, int):
            errors.append("timeout_ms must be an int")
        elif not (100 <= self.timeout_ms <= 300_000):
            errors.append("timeout_ms must be in [100, 300000]")
        if errors:
            raise ValueError("QueryBuilderConfig errors: " + "; ".join(errors))

    def resolved_token(self) -> str:
        return os.environ.get("SKETCHLOG_TOKEN", self.token)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_completions(items: List[CompletionItem], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps([i.to_dict() for i in items], indent=2))
    else:
        for item in items:
            print(f"  [{item.kind:<22}]  {item.label}")


def _print_validation(result: ValidationResult, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        status = "VALID" if result.valid else "INVALID"
        print(f"  Status : {status}")
        for e in result.errors:
            pos = f" (pos {e.position})" if e.position is not None else ""
            print(f"  ERROR  [{e.code}]{pos}: {e.message}")
        for w in result.warnings:
            pos = f" (pos {w.position})" if w.position is not None else ""
            print(f"  WARN   [{w.code}]{pos}: {w.message}")


def main(argv: Optional[List[str]] = None) -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        prog="sketchlog-query-builder",
        description="SketchLog Streaming SQL query builder CLI",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")

    sub = parser.add_subparsers(dest="command")

    # autocomplete
    p_complete = sub.add_parser("complete", help="Autocomplete a prefix")
    p_complete.add_argument("prefix", help="Text prefix to complete")
    p_complete.add_argument("--streams", nargs="*", default=[], metavar="STREAM")

    # validate
    p_validate = sub.add_parser("validate", help="Validate a SQL query")
    p_validate.add_argument("sql", help="SQL string or @file path")

    # explain
    p_explain = sub.add_parser("explain", help="Show explain plan for a query")
    p_explain.add_argument("sql", help="SQL string or @file path")

    # api-request
    p_api = sub.add_parser("api-request", help="Generate copyable API request")
    p_api.add_argument("sql", help="SQL string or @file path")
    p_api.add_argument("--server-url", required=True)
    p_api.add_argument("--namespace", default="default")
    p_api.add_argument("--output", choices=["json", "curl", "python"], default="curl")

    # templates
    sub.add_parser("templates", help="List example query templates")

    # save
    p_save = sub.add_parser("save", help="Save a query")
    p_save.add_argument("name")
    p_save.add_argument("sql", help="SQL string or @file path")
    p_save.add_argument("--description", default="")
    p_save.add_argument("--tags", nargs="*", default=[])
    p_save.add_argument("--store", default="sketchlog_queries.json")

    # list-saved
    p_ls = sub.add_parser("list-saved", help="List saved queries")
    p_ls.add_argument("--tag", default="")
    p_ls.add_argument("--store", default="sketchlog_queries.json")

    # history
    p_hist = sub.add_parser("history", help="Show query history")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--store", default="sketchlog_queries.json")

    args = parser.parse_args(argv)

    def _read_sql(raw: str) -> str:
        if raw.startswith("@"):
            path = raw[1:]
            try:
                with open(path, encoding="utf-8") as fh:
                    return fh.read()
            except OSError as exc:
                print(f"ERROR: Cannot read SQL file {path}: {exc}", file=sys.stderr)
                sys.exit(2)
        return raw

    fmt = args.format

    if args.command == "complete":
        items = autocomplete(args.prefix, streams=args.streams)
        _print_completions(items, fmt)

    elif args.command == "validate":
        sql = _read_sql(args.sql)
        result = validate_query(sql)
        _print_validation(result, fmt)
        sys.exit(0 if result.valid else 1)

    elif args.command == "explain":
        sql = _read_sql(args.sql)
        vr = validate_query(sql)
        if not vr.valid:
            print("ERROR: Query is invalid — fix errors before explaining.", file=sys.stderr)
            _print_validation(vr, fmt)
            sys.exit(1)
        plan = explain_query(sql)
        if fmt == "json":
            print(json.dumps(plan.to_dict(), indent=2))
        else:
            print(plan.render_text())

    elif args.command == "api-request":
        sql = _read_sql(args.sql)
        try:
            cfg = QueryBuilderConfig(
                server_url=args.server_url,
                namespace=args.namespace,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        req = build_api_request(sql, cfg.server_url, cfg.namespace, cfg.resolved_token())
        if args.output == "json":
            print(json.dumps(req.to_dict(), indent=2))
        elif args.output == "python":
            print(req.to_python())
        else:
            print(req.to_curl())

    elif args.command == "templates":
        if fmt == "json":
            print(json.dumps(_EXAMPLE_TEMPLATES, indent=2))
        else:
            for t in _EXAMPLE_TEMPLATES:
                print(f"\n{'='*60}")
                print(f"  {t['name']}")
                print(f"  {t['description']}")
                print(f"{'='*60}")
                print(t["sql"])

    elif args.command == "save":
        sql = _read_sql(args.sql)
        store = QueryStore(persist_path=args.store)
        try:
            store.save(SavedQuery(
                name=args.name,
                sql=sql,
                description=args.description,
                tags=args.tags,
            ))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        print(f"Saved query '{args.name}'.")

    elif args.command == "list-saved":
        store = QueryStore(persist_path=args.store)
        queries = store.list_saved(tag=args.tag or None)
        if fmt == "json":
            print(json.dumps([q.to_dict() for q in queries], indent=2))
        else:
            if not queries:
                print("  (no saved queries)")
            for q in queries:
                tags = ", ".join(q.tags) if q.tags else ""
                print(f"  {q.name:<30}  {tags:<20}  {q.description[:50]}")

    elif args.command == "history":
        store = QueryStore(persist_path=args.store)
        entries = store.list_history(limit=args.limit)
        if fmt == "json":
            print(json.dumps([e.to_dict() for e in entries], indent=2))
        else:
            if not entries:
                print("  (no history)")
            for e in entries:
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e.executed_at))
                status = "ok" if e.valid else "invalid"
                print(f"  [{ts}] [{status}] {e.sql[:80]}")

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
