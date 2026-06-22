import time
import json
import urllib.request
import urllib.error
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
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
        self.rules: List[AlertRule] = []
        self.states: Dict[str, AlertState] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Callbacks for metrics
        self.on_alert_fired: Optional[Callable[[], None]] = None
        self.on_webhook_failed: Optional[Callable[[], None]] = None

    def add_rule(self, rule: AlertRule) -> None:
        with self._lock:
            self.rules.append(rule)
            if rule.name not in self.states:
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
            min_thresh = min(r.min_drift_pct for r in rules_copy)

        drifts = self.ds.drift(threshold=min_thresh / 100.0)
        drift_map = {d["dimension"]: d for d in drifts}

        webhooks_to_send = []

        with self._lock:
            for rule in rules_copy:
                state = self.states[rule.name]
                result = drift_map.get(rule.dimension)

                is_violating = False
                if result:
                    current_samples = result.get("current_samples", 0)
                    if current_samples >= rule.min_samples:
                        dir_match = (not rule.direction_filter) or (result.get("direction") == rule.direction_filter)
                        if dir_match and result.get("drift_pct", 0) >= rule.min_drift_pct:
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

        for rule, result, is_recovery in webhooks_to_send:
            if not is_recovery and self.on_alert_fired:
                self.on_alert_fired()
            success = WebhookRouter.send_webhook(rule, result, is_recovery=is_recovery)
            if not success and rule.webhook_url and self.on_webhook_failed:
                self.on_webhook_failed()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.evaluate(time.time())
            self._stop_event.wait(self.poll_interval)
