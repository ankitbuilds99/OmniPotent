"""Tests for alerting system."""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import patch, AsyncMock

from app.alerting.rules import (
    AlertRule,
    AlertCondition,
    AlertSeverity,
    AlertEvent,
    DEFAULT_RULES,
)
from app.alerting.engine import AlertEngine


class TestAlertRule:
    """Tests for AlertRule dataclass."""

    def test_from_dict(self):
        """Should create rule from dict."""
        data = {
            "id": "test-rule",
            "name": "Test Rule",
            "description": "A test rule",
            "service": "api",
            "metric": "error_rate",
            "condition": "threshold_above",
            "threshold": 0.05,
            "severity": "high",
        }

        rule = AlertRule.from_dict(data)

        assert rule.id == "test-rule"
        assert rule.name == "Test Rule"
        assert rule.condition == AlertCondition.THRESHOLD_ABOVE
        assert rule.severity == AlertSeverity.HIGH

    def test_to_dict(self):
        """Should serialize rule to dict."""
        rule = AlertRule(
            id="test",
            name="Test",
            description="Test rule",
            service="*",
            metric="latency_p99",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=500,
            severity=AlertSeverity.MEDIUM,
        )

        data = rule.to_dict()

        assert data["id"] == "test"
        assert data["condition"] == "threshold_above"
        assert data["severity"] == "medium"


class TestAlertEvent:
    """Tests for AlertEvent dataclass."""

    def test_to_dict(self):
        """Should serialize event to dict."""
        event = AlertEvent(
            id="evt-1",
            rule_id="rule-1",
            rule_name="Test Rule",
            service="api",
            severity=AlertSeverity.HIGH,
            status="firing",
            message="Error rate is 10%",
            value=0.10,
            threshold=0.05,
            started_at=datetime(2024, 1, 1, 12, 0, 0),
        )

        data = event.to_dict()

        assert data["id"] == "evt-1"
        assert data["severity"] == "high"
        assert data["status"] == "firing"


class TestAlertEngine:
    """Tests for AlertEngine class."""

    def test_loads_default_rules(self):
        """Should load default rules on init."""
        engine = AlertEngine()

        assert len(engine._rules) == len(DEFAULT_RULES)

    def test_add_rule(self):
        """Should add a new rule."""
        engine = AlertEngine()
        rule = AlertRule(
            id="custom-rule",
            name="Custom Rule",
            description="Custom test rule",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=100,
        )

        engine.add_rule(rule)

        assert engine.get_rule("custom-rule") is not None

    def test_remove_rule(self):
        """Should remove a rule."""
        engine = AlertEngine()
        engine.add_rule(AlertRule(
            id="to-remove",
            name="To Remove",
            description="Rule to remove",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=100,
        ))

        result = engine.remove_rule("to-remove")

        assert result is True
        assert engine.get_rule("to-remove") is None

    def test_remove_nonexistent_rule(self):
        """Should return False for nonexistent rule."""
        engine = AlertEngine()

        result = engine.remove_rule("nonexistent")

        assert result is False

    def test_enable_disable_rule(self):
        """Should enable and disable rules."""
        engine = AlertEngine()
        rule_id = list(engine._rules.keys())[0]

        engine.disable_rule(rule_id)
        assert engine.get_rule(rule_id).enabled is False

        engine.enable_rule(rule_id)
        assert engine.get_rule(rule_id).enabled is True

    def test_record_metric(self):
        """Should record metric values."""
        engine = AlertEngine()

        engine.record_metric("api", "error_rate", 0.05)
        engine.record_metric("api", "error_rate", 0.06)

        value = engine.get_metric_value("api", "error_rate")

        assert value is not None
        assert 0.05 <= value <= 0.06

    def test_get_metric_value_empty(self):
        """Should return None for no metrics."""
        engine = AlertEngine()

        value = engine.get_metric_value("unknown", "metric")

        assert value is None

    def test_evaluate_threshold_above(self):
        """Should trigger on threshold above."""
        engine = AlertEngine()
        rule = AlertRule(
            id="test-above",
            name="Test Above",
            description="Test threshold above",
            service="api",
            metric="error_rate",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=0.05,
            duration_seconds=0,
        )
        engine.add_rule(rule)
        engine.record_metric("api", "error_rate", 0.10)

        engine._condition_start["test-above:api"] = datetime.now()
        alert = engine.evaluate_rule(rule, "api")

        assert alert is not None
        assert alert.rule_id == "test-above"

    def test_evaluate_threshold_below(self):
        """Should trigger on threshold below."""
        engine = AlertEngine()
        rule = AlertRule(
            id="test-below",
            name="Test Below",
            description="Test threshold below",
            service="api",
            metric="success_rate",
            condition=AlertCondition.THRESHOLD_BELOW,
            threshold=0.99,
            duration_seconds=0,
        )
        engine.add_rule(rule)
        engine.record_metric("api", "success_rate", 0.95)

        engine._condition_start["test-below:api"] = datetime.now()
        alert = engine.evaluate_rule(rule, "api")

        assert alert is not None

    def test_evaluate_disabled_rule(self):
        """Should not trigger disabled rules."""
        engine = AlertEngine()
        rule = AlertRule(
            id="disabled",
            name="Disabled",
            description="Disabled rule",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=0,
            enabled=False,
        )
        engine.add_rule(rule)
        engine.record_metric("api", "test", 100)

        alert = engine.evaluate_rule(rule, "api")

        assert alert is None

    def test_evaluate_absence(self):
        """Should trigger on metric absence."""
        engine = AlertEngine()
        rule = AlertRule(
            id="absence",
            name="Absence",
            description="Metric absence detection",
            service="api",
            metric="heartbeat",
            condition=AlertCondition.ABSENCE,
            threshold=0,
        )
        engine.add_rule(rule)

        alert = engine.evaluate_rule(rule, "api")

        assert alert is not None
        assert "No metrics" in alert.message

    def test_evaluate_all(self):
        """Should evaluate all rules for services."""
        engine = AlertEngine()
        engine.record_metric("api", "error_rate", 0.10)

        new_alerts = engine.evaluate_all(["api"])

        assert isinstance(new_alerts, list)

    def test_get_firing_alerts(self):
        """Should return firing alerts."""
        engine = AlertEngine()

        alerts = engine.get_firing_alerts()

        assert isinstance(alerts, list)


class TestAlertNotifications:
    """Tests for alert notifications."""

    @pytest.mark.asyncio
    async def test_notify_slack_no_config(self):
        """Should skip Slack when not configured."""
        engine = AlertEngine()
        alert = AlertEvent(
            id="test",
            rule_id="rule-1",
            rule_name="Test",
            service="api",
            severity=AlertSeverity.HIGH,
            status="firing",
            message="Test alert",
            value=1.0,
            threshold=0.5,
            started_at=datetime.now(),
        )

        with patch("app.config.get_settings") as mock:
            mock.return_value.slack_bot_token = ""
            mock.return_value.slack_channel = ""
            result = await engine._notify_slack(alert)

            assert result is False

    @pytest.mark.asyncio
    async def test_notify_pagerduty_no_config(self):
        """Should skip PagerDuty when not configured."""
        engine = AlertEngine()
        alert = AlertEvent(
            id="test",
            rule_id="rule-1",
            rule_name="Test",
            service="api",
            severity=AlertSeverity.HIGH,
            status="firing",
            message="Test alert",
            value=1.0,
            threshold=0.5,
            started_at=datetime.now(),
        )

        with patch("app.config.get_settings") as mock:
            mock.return_value.pagerduty_api_token = ""
            result = await engine._notify_pagerduty(alert)

            assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_multiple_channels(self):
        """Should send to multiple channels."""
        engine = AlertEngine()
        alert = AlertEvent(
            id="test",
            rule_id="rule-1",
            rule_name="Test",
            service="api",
            severity=AlertSeverity.HIGH,
            status="firing",
            message="Test",
            value=1.0,
            threshold=0.5,
            started_at=datetime.now(),
        )

        with patch.object(engine, "_notify_slack", new_callable=AsyncMock) as slack_mock:
            with patch.object(engine, "_notify_pagerduty", new_callable=AsyncMock) as pd_mock:
                slack_mock.return_value = True
                pd_mock.return_value = True

                results = await engine.send_notification(alert, ["slack", "pagerduty"])

                assert results["slack"] is True
                assert results["pagerduty"] is True
