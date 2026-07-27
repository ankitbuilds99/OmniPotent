"""Alerting Rules MCP server — manage alert rules and view firing alerts.

Provides tools for managing the alerting rules engine:
- Create, update, and delete alert rules
- View firing alerts
- Record metrics (for testing/manual input)
- Configure notification channels

Tools:
  - list_alert_rules:      List all configured rules
  - get_alert_rule:        Get details of a specific rule
  - create_alert_rule:     Create a new alert rule
  - update_alert_rule:     Update an existing rule
  - delete_alert_rule:     Delete a rule
  - enable_alert_rule:     Enable a disabled rule
  - disable_alert_rule:    Disable a rule
  - list_firing_alerts:    List currently firing alerts
  - record_metric:         Record a metric value
  - test_alert_rule:       Test a rule against current metrics

Run standalone:
    python -m app.mcp.servers.alerting.server
"""

from __future__ import annotations

from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("alerting")


@mcp.tool()
def list_alert_rules(
    service: str = "",
    enabled_only: bool = False,
) -> dict:
    """List all configured alert rules.

    Args:
        service: Filter by service name (empty for all)
        enabled_only: Only show enabled rules

    Returns:
        List of alert rules with their configuration
    """
    from app.alerting import get_engine

    engine = get_engine()
    rules = engine.list_rules(service=service or None)

    if enabled_only:
        rules = [r for r in rules if r.enabled]

    return {
        "rules": [r.to_dict() for r in rules],
        "total": len(rules),
        "firing_count": len(engine.get_firing_alerts()),
    }


@mcp.tool()
def get_alert_rule(rule_id: str) -> dict:
    """Get details of a specific alert rule.

    Args:
        rule_id: The rule identifier

    Returns:
        Rule configuration and current status
    """
    from app.alerting import get_engine

    engine = get_engine()
    rule = engine.get_rule(rule_id)

    if not rule:
        return {"error": f"Rule '{rule_id}' not found"}

    firing_for_rule = [
        a for a in engine.get_firing_alerts()
        if a.rule_id == rule_id
    ]

    return {
        "rule": rule.to_dict(),
        "firing_alerts": [a.to_dict() for a in firing_for_rule],
        "firing_count": len(firing_for_rule),
    }


@mcp.tool()
def create_alert_rule(
    rule_id: str,
    name: str,
    service: str,
    metric: str,
    condition: str,
    threshold: float,
    description: str = "",
    duration_seconds: int = 300,
    severity: str = "high",
    notification_channels: str = "slack",
    slo_target: float | None = None,
) -> dict:
    """Create a new alert rule.

    Args:
        rule_id: Unique identifier for the rule
        name: Human-readable name
        service: Service to monitor (* for all)
        metric: Metric name to evaluate
        condition: Condition type (threshold_above, threshold_below, rate_of_change, slo_burn_rate, anomaly, absence)
        threshold: Threshold value
        description: Rule description
        duration_seconds: How long condition must be true (default 300)
        severity: Alert severity (critical, high, medium, low, info)
        notification_channels: Comma-separated channels (slack, pagerduty)
        slo_target: SLO target for burn rate alerts (e.g., 0.999)

    Returns:
        Created rule details
    """
    from app.alerting import get_engine, AlertRule, AlertCondition, AlertSeverity

    engine = get_engine()

    if engine.get_rule(rule_id):
        return {"error": f"Rule '{rule_id}' already exists"}

    try:
        condition_enum = AlertCondition(condition)
    except ValueError:
        return {
            "error": f"Invalid condition '{condition}'",
            "valid_conditions": [c.value for c in AlertCondition],
        }

    try:
        severity_enum = AlertSeverity(severity)
    except ValueError:
        return {
            "error": f"Invalid severity '{severity}'",
            "valid_severities": [s.value for s in AlertSeverity],
        }

    channels = [c.strip() for c in notification_channels.split(",") if c.strip()]

    rule = AlertRule(
        id=rule_id,
        name=name,
        description=description,
        service=service,
        metric=metric,
        condition=condition_enum,
        threshold=threshold,
        duration_seconds=duration_seconds,
        severity=severity_enum,
        notification_channels=channels,
        slo_target=slo_target,
    )

    engine.add_rule(rule)

    return {
        "status": "created",
        "rule": rule.to_dict(),
    }


@mcp.tool()
def update_alert_rule(
    rule_id: str,
    threshold: float | None = None,
    duration_seconds: int | None = None,
    severity: str | None = None,
    notification_channels: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """Update an existing alert rule.

    Args:
        rule_id: Rule to update
        threshold: New threshold value
        duration_seconds: New duration
        severity: New severity level
        notification_channels: New channels (comma-separated)
        enabled: Enable/disable the rule

    Returns:
        Updated rule details
    """
    from app.alerting import get_engine, AlertSeverity

    engine = get_engine()
    rule = engine.get_rule(rule_id)

    if not rule:
        return {"error": f"Rule '{rule_id}' not found"}

    if threshold is not None:
        rule.threshold = threshold
    if duration_seconds is not None:
        rule.duration_seconds = duration_seconds
    if severity is not None:
        try:
            rule.severity = AlertSeverity(severity)
        except ValueError:
            return {"error": f"Invalid severity '{severity}'"}
    if notification_channels is not None:
        rule.notification_channels = [c.strip() for c in notification_channels.split(",") if c.strip()]
    if enabled is not None:
        rule.enabled = enabled

    return {
        "status": "updated",
        "rule": rule.to_dict(),
    }


@mcp.tool()
def delete_alert_rule(rule_id: str) -> dict:
    """Delete an alert rule.

    Args:
        rule_id: Rule to delete

    Returns:
        Deletion status
    """
    from app.alerting import get_engine

    engine = get_engine()

    if not engine.remove_rule(rule_id):
        return {"error": f"Rule '{rule_id}' not found"}

    return {
        "status": "deleted",
        "rule_id": rule_id,
    }


@mcp.tool()
def enable_alert_rule(rule_id: str) -> dict:
    """Enable a disabled alert rule.

    Args:
        rule_id: Rule to enable

    Returns:
        Updated rule status
    """
    from app.alerting import get_engine

    engine = get_engine()

    if not engine.enable_rule(rule_id):
        return {"error": f"Rule '{rule_id}' not found"}

    rule = engine.get_rule(rule_id)
    return {
        "status": "enabled",
        "rule": rule.to_dict() if rule else None,
    }


@mcp.tool()
def disable_alert_rule(rule_id: str) -> dict:
    """Disable an alert rule.

    Args:
        rule_id: Rule to disable

    Returns:
        Updated rule status
    """
    from app.alerting import get_engine

    engine = get_engine()

    if not engine.disable_rule(rule_id):
        return {"error": f"Rule '{rule_id}' not found"}

    rule = engine.get_rule(rule_id)
    return {
        "status": "disabled",
        "rule": rule.to_dict() if rule else None,
    }


@mcp.tool()
def list_firing_alerts(
    service: str = "",
    severity: str = "",
) -> dict:
    """List currently firing alerts.

    Args:
        service: Filter by service name
        severity: Filter by severity level

    Returns:
        List of firing alerts
    """
    from app.alerting import get_engine, AlertSeverity

    engine = get_engine()
    alerts = engine.get_firing_alerts()

    if service:
        alerts = [a for a in alerts if a.service == service]

    if severity:
        try:
            sev = AlertSeverity(severity)
            alerts = [a for a in alerts if a.severity == sev]
        except ValueError:
            pass

    alerts_sorted = sorted(
        alerts,
        key=lambda a: (
            ["critical", "high", "medium", "low", "info"].index(a.severity.value),
            a.started_at,
        ),
    )

    return {
        "alerts": [a.to_dict() for a in alerts_sorted],
        "total": len(alerts_sorted),
        "by_severity": {
            sev.value: len([a for a in alerts if a.severity == sev])
            for sev in AlertSeverity
        },
    }


@mcp.tool()
def record_metric(
    service: str,
    metric: str,
    value: float,
) -> dict:
    """Record a metric value for alert evaluation.

    Args:
        service: Service name
        metric: Metric name
        value: Metric value

    Returns:
        Recording confirmation
    """
    from app.alerting import get_engine

    engine = get_engine()
    engine.record_metric(service, metric, value)

    return {
        "status": "recorded",
        "service": service,
        "metric": metric,
        "value": value,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
def test_alert_rule(
    rule_id: str,
    service: str,
    metric_value: float,
) -> dict:
    """Test an alert rule with a specific metric value.

    Args:
        rule_id: Rule to test
        service: Service to test against
        metric_value: Value to evaluate

    Returns:
        Whether the rule would trigger
    """
    from app.alerting import get_engine

    engine = get_engine()
    rule = engine.get_rule(rule_id)

    if not rule:
        return {"error": f"Rule '{rule_id}' not found"}

    engine.record_metric(service, rule.metric, metric_value)

    would_trigger = False
    message = ""

    if rule.condition.value == "threshold_above":
        would_trigger = metric_value > rule.threshold
        message = f"{metric_value} {'>' if would_trigger else '<='} {rule.threshold}"
    elif rule.condition.value == "threshold_below":
        would_trigger = metric_value < rule.threshold
        message = f"{metric_value} {'<' if would_trigger else '>='} {rule.threshold}"

    return {
        "rule": rule.to_dict(),
        "test_value": metric_value,
        "would_trigger": would_trigger,
        "message": message,
        "note": f"Rule requires {rule.duration_seconds}s of sustained violation to actually fire",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
