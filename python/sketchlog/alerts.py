import time
import json
import urllib.request
import urllib.error
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
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
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None

@dataclass
class AlertState:
    status: AlertStatus = AlertStatus.OK
    violation_count: int = 0
    last_fired_at: float = 0.0

class WebhookRouter:
    @staticmethod
    def send_webhook(rule: AlertRule, payload: dict, is_recovery: bool = False):
        if not rule.webhook_url:
            return False

        data = json.dumps({
            "alert": rule.name,
            "dimension": rule.dimension,
            "status": "RESOLVED" if is_recovery else "FIRING",
            "message": "Statistical drift detected (Correlation does not imply causation)." if not is_recovery else "Statistical drift resolved.",
            "details": payload
        }).encode('utf-8')

        headers = {'Content-Type': 'application/json'}
        if rule.webhook_secret:
            signature = hmac.new(rule.webhook_secret.encode(), data, hashlib.sha256).hexdigest()
            headers['X-Signature'] = f"sha256={signature}"

        req = urllib.request.Request(rule.webhook_url, data=data, headers=headers, method='POST')

        # Simple exponential backoff retry
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status in (200, 201, 202, 204):
                        return True
            except Exception as e:
                logger.warning(f"Webhook delivery failed attempt {attempt+1}: {e}")
                time.sleep(2 ** attempt)
        return False

class AlertEngine:
    def __init__(self, drift_sketch: DriftSketch, poll_interval: float = 60.0):
        self.drift_sketch = drift_sketch
        self.rules: List[AlertRule] = []
        self.states: Dict[str, AlertState] = {}
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread = None
        self.metrics = {
            "alerts_fired": 0,
            "webhook_failures": 0
        }

    def add_rule(self, rule: AlertRule):
        self.rules.append(rule)
        self.states[rule.name] = AlertState()

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()

    def evaluate(self, current_time=None):
        if current_time is None:
            current_time = time.time()

        # Get drift results
        drifts = self.drift_sketch.drift(threshold=0.0) # Evaluate all, we will filter by rule
        drift_map = {d["dimension"]: d for d in drifts}

        for rule in self.rules:
            state = self.states[rule.name]
            d = drift_map.get(rule.dimension)

            # Check conditions
            is_violating = False
            if d:
                if abs(d["drift_pct"]) >= rule.min_drift_pct:
                    is_violating = True

            if is_violating:
                state.violation_count += 1
                if state.status != AlertStatus.FIRING:
                    if state.violation_count >= rule.sustained_windows:
                        state.status = AlertStatus.FIRING
                        state.last_fired_at = current_time
                        self.metrics["alerts_fired"] += 1
                        success = WebhookRouter.send_webhook(rule, d)
                        if not success and rule.webhook_url:
                            self.metrics["webhook_failures"] += 1
                    else:
                        state.status = AlertStatus.PENDING
            else:
                # Recovering
                if state.status == AlertStatus.FIRING:
                    success = WebhookRouter.send_webhook(rule, {}, is_recovery=True)
                    if not success and rule.webhook_url:
                        self.metrics["webhook_failures"] += 1
                state.status = AlertStatus.OK
                state.violation_count = 0

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self.evaluate()
            except Exception as e:
                logger.error(f"Alert engine error: {e}")
            # Wait with interrupt capability
            self._stop_event.wait(self.poll_interval)
