import pytest
import time
from sketchlog.drift import DriftSketch
from sketchlog.alerts import AlertEngine, AlertRule, AlertState, AlertStatus, WebhookRouter, AutoPilotRule, CUSUMState
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
    ds.add_batch("api_latency", [100] * 10) # 10 -> 100 is 900% drift

    # First violation
    engine.evaluate(current_time=101.0)
    assert engine.states["test-alert"].status == AlertStatus.PENDING
    assert engine.states["test-alert"].violation_count == 1
    assert mock_webhook.call_count == 0 # Sustained windows is 2!

    # Second violation
    ds.rotate_all()
    ds.add_batch("api_latency", [1000] * 10) # 100 -> 1000 is 900% drift
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

    with patch("sketchlog.alerts._WEBHOOK_OPENER.open") as mock_open:
        mock_open.side_effect = Exception("HTTP 503")

        with patch("time.sleep") as mock_sleep:
            success = WebhookRouter.send_webhook(rule, {"data": 1})

            assert not success
            assert mock_open.call_count == 3
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@example.com/hook",
        "https://example.com/hook#secret",
        "relative/path",
    ],
)
def test_alert_rules_reject_unsafe_webhook_urls(url):
    with pytest.raises(ValueError, match="webhook_url"):
        AlertRule(
            name="unsafe",
            dimension="latency",
            min_drift_pct=10,
            webhook_url=url,
        )

def test_autopilot_alert(mock_webhook):
    ds = DriftSketch(window="1m")
    engine = AlertEngine(ds, poll_interval=1.0)

    rule = AutoPilotRule(
        name="test-autopilot",
        dimension="cpu_usage",
        sensitivity=3.0,
        min_samples=5,
        sustained_windows=1,
        webhook_url="http://dummy"
    )
    engine.add_rule(rule)

    # Initial baseline
    ds.add_batch("cpu_usage", [50, 52, 48, 51, 49, 50] * 2)

    # Stable perfectly flat baseline: mean ~50, p99 is exactly 52.0 every window
    for _ in range(5):
        ds.rotate_all()
        ds.add_batch("cpu_usage", [50, 52, 48, 51, 49, 50] * 2)
        engine.evaluate(current_time=time.time())

    assert engine.states["test-autopilot"]["cpu_usage"].status == AlertStatus.OK
    assert mock_webhook.call_count == 0

    # Step change to 90
    ds.rotate_all()
    ds.add_batch("cpu_usage", [90, 92, 88, 91, 89, 90] * 2)
    engine.evaluate(current_time=time.time())

    assert engine.states["test-autopilot"]["cpu_usage"].status == AlertStatus.FIRING
    assert mock_webhook.call_count == 1
