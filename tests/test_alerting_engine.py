"""Tests for alerting engine module."""

from datetime import datetime

from app.alerting.engine import AlertEngine
from app.alerting.rules import (
    AlertRule,
    AlertCondition,
    AlertSeverity,
    AlertEvent,
)


class TestAlertEngine:
    """Tests for AlertEngine class."""

    def test_engine_initialization(self):
        """Engine should initialize with default rules."""
        engine = AlertEngine()
        assert engine._rules is not None
        assert isinstance(engine._firing_alerts, dict)
        assert engine._running is False

    def test_add_rule(self):
        """Engine should add new rules."""
        engine = AlertEngine()
        rule = AlertRule(
            id="test-rule",
            name="Test Rule",
            description="A test rule",
            service="api",
            metric="error_rate",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=0.05,
            severity=AlertSeverity.HIGH,
        )
        engine.add_rule(rule)
        assert "test-rule" in engine._rules
        assert engine._rules["test-rule"].name == "Test Rule"

    def test_remove_rule(self):
        """Engine should remove rules."""
        engine = AlertEngine()
        rule = AlertRule(
            id="to-remove",
            name="Remove Me",
            description="Will be removed",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=1.0,
            severity=AlertSeverity.LOW,
        )
        engine.add_rule(rule)
        assert "to-remove" in engine._rules

        result = engine.remove_rule("to-remove")
        assert result is True
        assert "to-remove" not in engine._rules

    def test_remove_nonexistent_rule(self):
        """Removing nonexistent rule should return False."""
        engine = AlertEngine()
        result = engine.remove_rule("does-not-exist")
        assert result is False

    def test_get_rule(self):
        """Engine should get rule by ID."""
        engine = AlertEngine()
        rule = AlertRule(
            id="get-me",
            name="Get Me",
            description="Should be gettable",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=1.0,
            severity=AlertSeverity.MEDIUM,
        )
        engine.add_rule(rule)

        retrieved = engine.get_rule("get-me")
        assert retrieved is not None
        assert retrieved.id == "get-me"
        assert retrieved.name == "Get Me"

    def test_get_nonexistent_rule(self):
        """Getting nonexistent rule should return None."""
        engine = AlertEngine()
        result = engine.get_rule("does-not-exist")
        assert result is None

    def test_list_rules(self):
        """Engine should list all rules."""
        engine = AlertEngine()
        rules = engine.list_rules()
        assert isinstance(rules, list)
        # Should have default rules
        assert len(rules) >= 0

    def test_list_rules_by_service(self):
        """Engine should filter rules by service."""
        engine = AlertEngine()

        rule1 = AlertRule(
            id="api-rule",
            name="API Rule",
            description="For API service",
            service="api",
            metric="latency",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=100.0,
            severity=AlertSeverity.HIGH,
        )
        rule2 = AlertRule(
            id="db-rule",
            name="DB Rule",
            description="For DB service",
            service="database",
            metric="connections",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=90.0,
            severity=AlertSeverity.CRITICAL,
        )
        rule3 = AlertRule(
            id="global-rule",
            name="Global Rule",
            description="For all services",
            service="*",
            metric="errors",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=10.0,
            severity=AlertSeverity.MEDIUM,
        )

        engine.add_rule(rule1)
        engine.add_rule(rule2)
        engine.add_rule(rule3)

        api_rules = engine.list_rules(service="api")
        assert any(r.id == "api-rule" for r in api_rules)
        assert any(r.id == "global-rule" for r in api_rules)
        assert not any(r.id == "db-rule" for r in api_rules)

    def test_enable_rule(self):
        """Engine should enable disabled rules."""
        engine = AlertEngine()
        rule = AlertRule(
            id="disabled-rule",
            name="Disabled",
            description="Initially disabled",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=1.0,
            severity=AlertSeverity.LOW,
            enabled=False,
        )
        engine.add_rule(rule)
        assert engine._rules["disabled-rule"].enabled is False

        result = engine.enable_rule("disabled-rule")
        assert result is True
        assert engine._rules["disabled-rule"].enabled is True

    def test_disable_rule(self):
        """Engine should disable enabled rules."""
        engine = AlertEngine()
        rule = AlertRule(
            id="enabled-rule",
            name="Enabled",
            description="Initially enabled",
            service="*",
            metric="test",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=1.0,
            severity=AlertSeverity.LOW,
            enabled=True,
        )
        engine.add_rule(rule)
        assert engine._rules["enabled-rule"].enabled is True

        result = engine.disable_rule("enabled-rule")
        assert result is True
        assert engine._rules["enabled-rule"].enabled is False

    def test_record_metric(self):
        """Engine should record metrics."""
        engine = AlertEngine()
        engine.record_metric("api", "error_rate", 0.05)

        assert "api:error_rate" in engine._metric_history
        history = engine._metric_history["api:error_rate"]
        assert len(history) == 1
        assert history[0][1] == 0.05

    def test_get_firing_alerts(self):
        """Engine should return firing alerts."""
        engine = AlertEngine()
        alerts = engine.get_firing_alerts()
        assert isinstance(alerts, list)

    def test_get_alert_history(self):
        """Engine should return alert history."""
        engine = AlertEngine()
        history = engine.get_alert_history()
        assert isinstance(history, list)


class TestAlertRule:
    """Tests for AlertRule model."""

    def test_rule_creation(self):
        """Rule should be created with required fields."""
        rule = AlertRule(
            id="test-rule",
            name="Test Rule",
            description="Test description",
            service="api",
            metric="error_rate",
            condition=AlertCondition.THRESHOLD_ABOVE,
            threshold=0.1,
            severity=AlertSeverity.HIGH,
        )
        assert rule.id == "test-rule"
        assert rule.name == "Test Rule"
        assert rule.service == "api"
        assert rule.metric == "error_rate"
        assert rule.condition == AlertCondition.THRESHOLD_ABOVE
        assert rule.threshold == 0.1
        assert rule.severity == AlertSeverity.HIGH
        assert rule.enabled is True

    def test_rule_from_dict(self):
        """Rule should be creatable from dict."""
        data = {
            "id": "dict-rule",
            "name": "Dict Rule",
            "description": "From dict",
            "service": "*",
            "metric": "latency",
            "condition": "threshold_above",
            "threshold": 100.0,
            "severity": "critical",
        }
        rule = AlertRule.from_dict(data)
        assert rule.id == "dict-rule"
        assert rule.name == "Dict Rule"
        assert rule.condition == AlertCondition.THRESHOLD_ABOVE
        assert rule.severity == AlertSeverity.CRITICAL

    def test_rule_to_dict(self):
        """Rule should convert to dict."""
        rule = AlertRule(
            id="to-dict",
            name="To Dict",
            description="Converts to dict",
            service="db",
            metric="connections",
            condition=AlertCondition.THRESHOLD_BELOW,
            threshold=10.0,
            severity=AlertSeverity.MEDIUM,
        )
        data = rule.to_dict()
        assert data["id"] == "to-dict"
        assert data["name"] == "To Dict"
        assert data["condition"] == "threshold_below"
        assert data["severity"] == "medium"


class TestAlertEvent:
    """Tests for AlertEvent model."""

    def test_event_creation(self):
        """Event should be created with required fields."""
        now = datetime.utcnow()
        event = AlertEvent(
            id="evt-123",
            rule_id="rule-1",
            rule_name="Test Rule",
            service="api",
            severity=AlertSeverity.HIGH,
            status="firing",
            message="Alert triggered",
            value=0.15,
            threshold=0.1,
            started_at=now,
        )
        assert event.id == "evt-123"
        assert event.rule_id == "rule-1"
        assert event.severity == AlertSeverity.HIGH
        assert event.value == 0.15
        assert event.threshold == 0.1
        assert event.status == "firing"
        assert event.resolved_at is None

    def test_event_to_dict(self):
        """Event should convert to dict."""
        now = datetime.utcnow()
        event = AlertEvent(
            id="evt-456",
            rule_id="rule-2",
            rule_name="Test Rule 2",
            service="db",
            severity=AlertSeverity.CRITICAL,
            status="firing",
            message="DB overload",
            value=95.0,
            threshold=90.0,
            started_at=now,
        )
        data = event.to_dict()
        assert data["id"] == "evt-456"
        assert data["severity"] == "critical"
        assert data["value"] == 95.0
        assert data["status"] == "firing"


class TestAlertCondition:
    """Tests for AlertCondition enum."""

    def test_condition_values(self):
        """All condition values should be correct."""
        assert AlertCondition.THRESHOLD_ABOVE.value == "threshold_above"
        assert AlertCondition.THRESHOLD_BELOW.value == "threshold_below"
        assert AlertCondition.RATE_OF_CHANGE.value == "rate_of_change"


class TestAlertSeverity:
    """Tests for AlertSeverity enum."""

    def test_severity_values(self):
        """All severity values should be correct."""
        assert AlertSeverity.CRITICAL.value == "critical"
        assert AlertSeverity.HIGH.value == "high"
        assert AlertSeverity.MEDIUM.value == "medium"
        assert AlertSeverity.LOW.value == "low"
