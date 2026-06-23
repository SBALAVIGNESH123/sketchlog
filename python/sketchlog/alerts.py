import time
import math
import json
import urllib.request
import urllib.error
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Tuple, Union
from enum import Enum
import hmac
import hashlib

from sketchlog.drift import DriftSketch

logger = logging.getLogger(__name__)

class AlertStatus(Enum):
    OK = "OK"
    PENDING = "PENDING"
    FIRING = "FIRING"

@dataclass
class AlertRule:
    name: str
    dimension: str
    min_drift_pct: float
    min_samples: int = 100
    sustained_windows: int = 1
    direction_filter: Optional[str] = "up"
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None

    def __post_init__(self) -> None:
        if self.sustained_windows <= 0:
            raise ValueError("sustained_windows must be > 0")
        if self.min_samples < 0:
            raise ValueError("min_samples must be >= 0")
        if self.min_drift_pct < 0:
            raise ValueError("min_drift_pct must be >= 0")

@dataclass
class AlertState:
    status: AlertStatus = AlertStatus.OK
    violation_count: int = 0
    last_fired_at: float = 0.0

@dataclass
class AutoPilotRule:
    name: str
    dimension: str = "*"
    sensitivity: float = 3.0
    min_samples: int = 100
    sustained_windows: int = 1
    direction_filter: Optional[str] = "up"
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None

@dataclass
class CUSUMState:
    n: int = 0
    mean: float = 0.0
    M2: float = 0.0
    S_hi: float = 0.0
    S_lo: float = 0.0

    status: AlertStatus = AlertStatus.OK
    violation_count: int = 0
    last_fired_at: float = 0.0

    def update(self, x: float, sensitivity: float) -> Tuple[float, float]:
        """Update running stats using Welford's algorithm and return (std_dev, CUSUM_score)."""
        variance = self.M2 / (self.n - 1) if self.n > 1 else 0.0
        std_dev = math.sqrt(variance) if variance > 0 else 0.0

        # Prevent division by zero on perfectly flat baselines and ignore microscopic FP changes
        safe_std_dev = max(std_dev, 1e-5)

        # CUSUM accumulation using PREVIOUS stats
        if self.n > 0:
            drift = sensitivity / 2.0  # allowable slack
            z = (x - self.mean) / safe_std_dev
            self.S_hi = max(0.0, self.S_hi + z - drift)
            self.S_lo = max(0.0, self.S_lo + (-z) - drift)
            score = self.S_hi if z > 0 else self.S_lo
        else:
            score = 0.0

        # Only update baseline if it's not a massive anomaly (prevents poisoning)
        if score < sensitivity * 2:
            self.n += 1
            delta = x - self.mean
            self.mean += delta / self.n
            delta2 = x - self.mean
            self.M2 += delta * delta2

        return std_dev, score

class WebhookRouter:
    @staticmethod
    def send_webhook(rule: AlertRule, payload: Any, is_recovery: bool = False) -> bool:
        if not rule.webhook_url:
            return False

        data = json.dumps({
            "alert": rule.name,
            "dimension": rule.dimension,
            "status": "RESOLVED" if is_recovery else "FIRING",
            "message": "Statistical drift detected (Correlation does not imply causation)." if not is_recovery else "Statistical drift resolved.",
            "details": payload
        }).encode('utf-8')

        req = urllib.request.Request(rule.webhook_url, data=data, headers={"Content-Type": "application/json"})

        if rule.webhook_secret:
            signature = hmac.new(rule.webhook_secret.encode('utf-8'), data, hashlib.sha256).hexdigest()
            req.add_header("X-Signature", f"sha256={signature}")

        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=5):
                    return True
            except Exception as e:
                logger.warning(f"Webhook delivery failed for {rule.name}: {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)

        return False

class AlertEngine:
    def __init__(self, drift_sketch: DriftSketch, poll_interval: float = 10.0) -> None:
        self.ds = drift_sketch
        self.poll_interval = poll_interval
        self.rules: List[Union[AlertRule, AutoPilotRule]] = []
        self.states: Dict[str, Union[AlertState, CUSUMState]] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Callbacks for metrics
        self.on_alert_fired: Optional[Callable[[], None]] = None
        self.on_webhook_failed: Optional[Callable[[], None]] = None

    def add_rule(self, rule: Union[AlertRule, AutoPilotRule]) -> None:
        with self._lock:
            self.rules.append(rule)
            if rule.name not in self.states:
                if isinstance(rule, AutoPilotRule):
                    self.states[rule.name] = {}  # type: ignore[assignment]
                else:
                    self.states[rule.name] = AlertState()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def evaluate(self, current_time: float) -> None:
        with self._lock:
            if not self.rules:
                return
            rules_copy = list(self.rules)

            has_autopilot = any(isinstance(r, AutoPilotRule) for r in rules_copy)
            min_thresh = 0.0 if has_autopilot else min([r.min_drift_pct for r in rules_copy if isinstance(r, AlertRule)], default=0.0)

        drifts = self.ds.drift(threshold=min_thresh / 100.0 if min_thresh > 0 else 0.0)
        drift_map = {d["dimension"]: d for d in drifts}

        webhooks_to_send: List[Tuple[Any, Any, bool]] = []

        with self._lock:
            for rule in rules_copy:
                if isinstance(rule, AutoPilotRule):
                    states_dict: Dict[str, CUSUMState] = self.states[rule.name]  # type: ignore[assignment]
                    dims_to_eval = list(drift_map.keys()) if rule.dimension == "*" else [rule.dimension]

                    for dim in dims_to_eval:
                        if dim not in states_dict:
                            states_dict[dim] = CUSUMState()
                        state = states_dict[dim]
                        result = drift_map.get(dim)

                        is_violating = False
                        if result:
                            current_samples = result.get("current_samples", 0)
                            if current_samples >= rule.min_samples:
                                current_p99 = result.get("current_p99", 0.0)
                                std_dev, score = state.update(current_p99, rule.sensitivity)

                                dir_match = (not rule.direction_filter) or (result.get("direction") == rule.direction_filter)
                                if dir_match and score > rule.sensitivity:
                                    is_violating = True

                        if is_violating:
                            state.violation_count += 1
                            if state.violation_count >= rule.sustained_windows:
                                if state.status != AlertStatus.FIRING:
                                    state.status = AlertStatus.FIRING
                                    state.last_fired_at = current_time
                                    webhooks_to_send.append((rule, result, False))
                                elif current_time - state.last_fired_at >= 3600:
                                    state.last_fired_at = current_time
                                    webhooks_to_send.append((rule, result, False))
                            else:
                                state.status = AlertStatus.PENDING
                        else:
                            if state.status == AlertStatus.FIRING:
                                webhooks_to_send.append((rule, result, True))
                            state.violation_count = 0
                            state.status = AlertStatus.OK

                else:
                    state_alert: AlertState = self.states[rule.name]  # type: ignore[assignment]
                    result = drift_map.get(rule.dimension)

                    is_violating = False
                    if result:
                        current_samples = result.get("current_samples", 0)
                        if current_samples >= rule.min_samples:
                            dir_match = (not rule.direction_filter) or (result.get("direction") == rule.direction_filter)
                            if dir_match and result.get("drift_pct", 0) >= rule.min_drift_pct:
                                is_violating = True

                    if is_violating:
                        state_alert.violation_count += 1
                        if state_alert.violation_count >= rule.sustained_windows:
                            if state_alert.status != AlertStatus.FIRING:
                                state_alert.status = AlertStatus.FIRING
                                state_alert.last_fired_at = current_time
                                webhooks_to_send.append((rule, result, False))
                            elif current_time - state_alert.last_fired_at >= 3600:
                                state_alert.last_fired_at = current_time
                                webhooks_to_send.append((rule, result, False))
                        else:
                            state_alert.status = AlertStatus.PENDING
                    else:
                        if state_alert.status == AlertStatus.FIRING:
                            webhooks_to_send.append((rule, result, True))
                        state_alert.violation_count = 0
                        state_alert.status = AlertStatus.OK

        for w_rule, w_result, is_recovery in webhooks_to_send:
            if not is_recovery and self.on_alert_fired:
                self.on_alert_fired()
            success = WebhookRouter.send_webhook(w_rule, w_result, is_recovery=is_recovery)
            if not success and w_rule.webhook_url and self.on_webhook_failed:
                self.on_webhook_failed()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.evaluate(time.time())
            self._stop_event.wait(self.poll_interval)
