import pytest
import time
from sketchlog.drift import DriftSketch
from sketchlog.alerts import AlertEngine, AlertRule, AlertState, AlertStatus, WebhookRouter
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_webhook():
    with patch.object(WebhookRouter, 'send_webhook', return_value=True) as mock:
        yield mock

def test_alert_state_transitions(mock_webhook):
    ds = DriftSketch(window="1m")
    engine = AlertEngine(ds, poll_interval=1.0)

    rule = AlertRule(
        name="test-alert",
        dimension="api_latency",
        min_drift_pct=20.0,
        min_samples=10,
        sustained_windows=2,
        webhook_url="http://dummy"
    )
    engine.add_rule(rule)

    # Inject data that doesn't drift
    ds.add_batch("api_latency", [10, 10, 10])
    ds.rotate_all()
    ds.add_batch("api_latency", [10, 10, 10])

    engine.evaluate(current_time=100.0)
    assert engine.states["test-alert"].status == AlertStatus.OK
    assert mock_webhook.call_count == 0

    # Inject data that drifts > 20%
    ds.rotate_all()
    ds.add_batch("api_latency", [100, 100, 100]) # 10 -> 100 is 900% drift

    # First violation
    engine.evaluate(current_time=101.0)
    assert engine.states["test-alert"].status == AlertStatus.PENDING
    assert engine.states["test-alert"].violation_count == 1
    assert mock_webhook.call_count == 0 # Sustained windows is 2!

    # Second violation
    ds.rotate_all()
    ds.add_batch("api_latency", [1000, 1000, 1000]) # 100 -> 1000 is 900% drift
    engine.evaluate(current_time=102.0)
    assert engine.states["test-alert"].status == AlertStatus.FIRING
    assert engine.states["test-alert"].violation_count == 2
    assert mock_webhook.call_count == 1
    assert mock_webhook.call_args[0][0] == rule
    payload = mock_webhook.call_args[0][1]
    assert payload["dimension"] == "api_latency"
    assert pytest.approx(900.0, rel=1e-1) == payload["drift_pct"]

    # Recovery
    ds.rotate_all()
    ds.add_batch("api_latency", [1000, 1000, 1000]) # 1000 -> 1000 is 0% drift
    engine.evaluate(current_time=103.0)
    assert engine.states["test-alert"].status == AlertStatus.OK
    assert engine.states["test-alert"].violation_count == 0
    assert mock_webhook.call_count == 2 # Fired recovery webhook
    # Check that it was called with is_recovery=True
    assert mock_webhook.call_args_list[1][1].get("is_recovery") == True or mock_webhook.call_args_list[1][0][2] == True

def test_webhook_retry_backoff():
    rule = AlertRule(name="test", dimension="test", min_drift_pct=10, webhook_url="http://dummy")

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = Exception("HTTP 503")

        with patch("time.sleep") as mock_sleep:
            success = WebhookRouter.send_webhook(rule, {"data": 1})

            assert not success
            assert mock_urlopen.call_count == 3
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)
