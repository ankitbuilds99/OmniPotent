"""Alert evaluation engine.

The engine periodically evaluates alert rules against metrics
and triggers notifications when conditions are met.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from app.alerting.rules import (
    AlertCondition,
    AlertEvent,
    AlertRule,
    AlertSeverity,
    DEFAULT_RULES,
)
from app.config import get_settings

log = logging.getLogger("omnipotent.alerting")

_settings = get_settings()


class AlertEngine:
    """Alert evaluation and notification engine.

    Evaluates rules against metrics, manages alert state,
    and triggers notifications through configured channels.
    """

    def __init__(self) -> None:
        self._rules: dict[str, AlertRule] = {}
        self._firing_alerts: dict[str, AlertEvent] = {}
        self._condition_start: dict[str, datetime] = {}
        self._metric_history: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
        self._running = False
        self._eval_task: asyncio.Task[None] | None = None
        self._load_default_rules()

    def _load_default_rules(self) -> None:
        """Load default alert rules."""
        for rule_data in DEFAULT_RULES:
            rule = AlertRule.from_dict(rule_data)
            self._rules[rule.id] = rule

    def add_rule(self, rule: AlertRule) -> None:
        """Add or update an alert rule."""
        self._rules[rule.id] = rule
        log.info("Added alert rule: %s", rule.name)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove an alert rule."""
        if rule_id in self._rules:
            del self._rules[rule_id]
            self._condition_start.pop(rule_id, None)
            log.info("Removed alert rule: %s", rule_id)
            return True
        return False

    def get_rule(self, rule_id: str) -> AlertRule | None:
        """Get a rule by ID."""
        return self._rules.get(rule_id)

    def list_rules(self, service: str | None = None) -> list[AlertRule]:
        """List all rules, optionally filtered by service."""
        rules = list(self._rules.values())
        if service:
            rules = [r for r in rules if r.service == "*" or r.service == service]
        return rules

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a rule."""
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = True
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule."""
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = False
            self._condition_start.pop(rule_id, None)
            return True
        return False

    def has_metrics(self) -> bool:
        """Whether this engine has ever been fed a metric.

        Metric history is per-process and in memory, so an engine in a process that
        no one calls :meth:`record_metric` on is empty rather than quiet. Callers
        that did not feed it should check this before evaluating: with no history,
        :meth:`get_metric_value` returns None for every service, and an ABSENCE rule
        reads that as "No metrics received" and fires against a healthy service.
        """
        return any(self._metric_history.values())

    def known_services(self) -> list[str]:
        """Services this engine has recorded at least one metric for.

        The correct argument for :meth:`evaluate_all` — evaluating a service the
        engine has no data on can only produce a false ABSENCE alert.
        """
        return sorted({key.split(":", 1)[0] for key, hist in self._metric_history.items() if hist})

    def record_metric(
        self,
        service: str,
        metric: str,
        value: float,
        timestamp: datetime | None = None,
    ) -> None:
        """Record a metric value for evaluation."""
        ts = timestamp or datetime.now()
        key = f"{service}:{metric}"
        history = self._metric_history[key]
        history.append((ts, value))

        cutoff = ts - timedelta(hours=1)
        self._metric_history[key] = [(t, v) for t, v in history if t > cutoff]

    def get_metric_value(
        self,
        service: str,
        metric: str,
        window_seconds: int = 60,
    ) -> float | None:
        """Get the average metric value over a time window."""
        key = f"{service}:{metric}"
        history = self._metric_history.get(key, [])

        if not history:
            return None

        cutoff = datetime.now() - timedelta(seconds=window_seconds)
        recent = [v for t, v in history if t > cutoff]

        if not recent:
            return None

        return sum(recent) / len(recent)

    def evaluate_rule(self, rule: AlertRule, service: str) -> AlertEvent | None:
        """Evaluate a single rule against current metrics.

        Returns an AlertEvent if the rule triggers, None otherwise.
        """
        if not rule.enabled:
            return None

        value = self.get_metric_value(service, rule.metric)
        if value is None:
            if rule.condition == AlertCondition.ABSENCE:
                # "Absent" means a metric that WAS arriving stopped, which is not the
                # same as one that was never sent. get_metric_value returns None for
                # both: no history at all, and history with nothing inside the recent
                # window. Only the second is an outage.
                #
                # Without this check the shipped `metrics-absence` default rule fires
                # for every service on its first evaluation — it watches `heartbeat`
                # on service "*", nothing in the codebase records a metric by that
                # name, and it pages Slack and PagerDuty at high severity. Firing on
                # never-observed metrics makes every rule of this kind a standing
                # false page, which is worse than not having the rule.
                if not self._metric_history.get(f"{service}:{rule.metric}"):
                    return None
                return self._create_alert(rule, service, 0, "No metrics received")
            return None

        triggered = False
        message = ""

        if rule.condition == AlertCondition.THRESHOLD_ABOVE:
            if value > rule.threshold:
                triggered = True
                message = f"{rule.metric} is {value:.2f}, above threshold {rule.threshold}"

        elif rule.condition == AlertCondition.THRESHOLD_BELOW:
            if value < rule.threshold:
                triggered = True
                message = f"{rule.metric} is {value:.2f}, below threshold {rule.threshold}"

        elif rule.condition == AlertCondition.RATE_OF_CHANGE:
            key = f"{service}:{rule.metric}"
            history = self._metric_history.get(key, [])
            if len(history) >= 2:
                cutoff = datetime.now() - timedelta(seconds=rule.duration_seconds)
                old_values = [v for t, v in history if t < cutoff]
                new_values = [v for t, v in history if t >= cutoff]
                if old_values and new_values:
                    old_avg = sum(old_values) / len(old_values)
                    new_avg = sum(new_values) / len(new_values)
                    change = new_avg - old_avg
                    if abs(change) > rule.threshold:
                        triggered = True
                        message = f"{rule.metric} changed by {change:.2f}, exceeds threshold {rule.threshold}"

        elif rule.condition == AlertCondition.SLO_BURN_RATE:
            if rule.slo_target:
                error_budget = 1 - rule.slo_target
                burn_rate = value / error_budget if error_budget > 0 else 0
                if burn_rate > rule.threshold:
                    triggered = True
                    message = f"SLO burn rate is {burn_rate:.1f}x, exceeds {rule.threshold}x threshold"

        elif rule.condition == AlertCondition.ANOMALY:
            key = f"{service}:{rule.metric}"
            history = self._metric_history.get(key, [])
            if len(history) >= 30:
                values = [v for _, v in history]
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                std_dev = variance ** 0.5
                if std_dev > 0 and abs(value - mean) > rule.threshold * std_dev:
                    triggered = True
                    message = f"{rule.metric} is {value:.2f}, {abs(value - mean) / std_dev:.1f} std devs from mean"

        if triggered:
            rule_key = f"{rule.id}:{service}"
            now = datetime.now()

            if rule_key not in self._condition_start:
                self._condition_start[rule_key] = now
                return None

            if (now - self._condition_start[rule_key]).total_seconds() >= rule.duration_seconds:
                return self._create_alert(rule, service, value, message)
        else:
            rule_key = f"{rule.id}:{service}"
            self._condition_start.pop(rule_key, None)

        return None

    def _create_alert(
        self,
        rule: AlertRule,
        service: str,
        value: float,
        message: str,
    ) -> AlertEvent:
        """Create an alert event."""
        return AlertEvent(
            id=str(uuid.uuid4()),
            rule_id=rule.id,
            rule_name=rule.name,
            service=service,
            severity=rule.severity,
            status="firing",
            message=message,
            value=value,
            threshold=rule.threshold,
            started_at=datetime.now(),
            labels=rule.labels,
            annotations=rule.annotations,
        )

    def evaluate_all(self, services: list[str]) -> list[AlertEvent]:
        """Evaluate all rules for the given services.

        Returns a list of newly triggered alerts.
        """
        new_alerts: list[AlertEvent] = []

        for rule in self._rules.values():
            for service in services:
                if rule.service != "*" and rule.service != service:
                    continue

                alert = self.evaluate_rule(rule, service)
                if alert:
                    alert_key = f"{rule.id}:{service}"
                    if alert_key not in self._firing_alerts:
                        self._firing_alerts[alert_key] = alert
                        new_alerts.append(alert)
                        log.warning(
                            "Alert firing: %s on %s - %s",
                            rule.name, service, alert.message
                        )

        resolved: list[str] = []
        for alert_key, alert in list(self._firing_alerts.items()):
            rule_or_none = self._rules.get(alert.rule_id)
            if not rule_or_none or not rule_or_none.enabled:
                resolved.append(alert_key)
                continue
            rule = rule_or_none

            value = self.get_metric_value(alert.service, rule.metric)
            if value is not None:
                still_firing = False
                if rule.condition == AlertCondition.THRESHOLD_ABOVE:
                    still_firing = value > rule.threshold
                elif rule.condition == AlertCondition.THRESHOLD_BELOW:
                    still_firing = value < rule.threshold

                if not still_firing:
                    resolved.append(alert_key)

        for alert_key in resolved:
            alert = self._firing_alerts.pop(alert_key, None)
            if alert:
                alert.status = "resolved"
                alert.resolved_at = datetime.now()
                log.info("Alert resolved: %s on %s", alert.rule_name, alert.service)

        return new_alerts

    def get_firing_alerts(self) -> list[AlertEvent]:
        """Get all currently firing alerts."""
        return list(self._firing_alerts.values())

    def get_alert_history(
        self,
        service: str | None = None,
        severity: AlertSeverity | None = None,
        limit: int = 100,
    ) -> list[AlertEvent]:
        """Get alert history (firing alerts only for now)."""
        alerts = self.get_firing_alerts()

        if service:
            alerts = [a for a in alerts if a.service == service]
        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        return alerts[:limit]

    async def send_notification(
        self,
        alert: AlertEvent,
        channels: list[str],
    ) -> dict[str, bool]:
        """Send alert notifications to configured channels.

        Supported channels:
        - slack: Slack webhook/bot notification
        - pagerduty: PagerDuty Events API v2
        - opsgenie: Atlassian OpsGenie alerts

        Returns dict of channel -> success status.
        """
        results: dict[str, bool] = {}

        for channel in channels:
            try:
                if channel == "slack":
                    results["slack"] = await self._notify_slack(alert)
                elif channel == "pagerduty":
                    results["pagerduty"] = await self._notify_pagerduty(alert)
                elif channel == "opsgenie":
                    results["opsgenie"] = await self._notify_opsgenie(alert)
                else:
                    log.warning("Unknown notification channel: %s", channel)
                    results[channel] = False
            except Exception as e:
                log.error("Failed to send %s notification: %s", channel, e)
                results[channel] = False

        return results

    async def _notify_slack(self, alert: AlertEvent) -> bool:
        """Send alert to Slack."""
        from app.config import get_settings

        s = get_settings()
        if not s.slack_bot_token or not s.slack_channel:
            log.debug("Slack not configured, skipping notification")
            return False

        severity_emoji = {
            AlertSeverity.CRITICAL: ":red_circle:",
            AlertSeverity.HIGH: ":large_orange_circle:",
            AlertSeverity.MEDIUM: ":large_yellow_circle:",
            AlertSeverity.LOW: ":large_green_circle:",
            AlertSeverity.INFO: ":information_source:",
        }

        emoji = severity_emoji.get(alert.severity, ":warning:")
        status_text = "FIRING" if alert.status == "firing" else "RESOLVED"

        text = (
            f"{emoji} *[{status_text}] {alert.rule_name}*\n"
            f"Service: `{alert.service}`\n"
            f"Severity: {alert.severity.value.upper()}\n"
            f"Message: {alert.message}\n"
            f"Value: {alert.value:.2f} (threshold: {alert.threshold})"
        )

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {s.slack_bot_token}"},
                json={"channel": s.slack_channel, "text": text},
                timeout=_settings.http_timeout_default,
            )
            return resp.json().get("ok", False)

    async def _notify_pagerduty(self, alert: AlertEvent) -> bool:
        """Send alert to PagerDuty."""
        from app.config import get_settings

        s = get_settings()
        if not s.pagerduty_api_token:
            log.debug("PagerDuty not configured, skipping notification")
            return False

        severity_map = {
            AlertSeverity.CRITICAL: "critical",
            AlertSeverity.HIGH: "error",
            AlertSeverity.MEDIUM: "warning",
            AlertSeverity.LOW: "info",
            AlertSeverity.INFO: "info",
        }

        event_action = "trigger" if alert.status == "firing" else "resolve"

        payload = {
            "routing_key": s.pagerduty_api_token,
            "event_action": event_action,
            "dedup_key": f"{alert.rule_id}:{alert.service}",
            "payload": {
                "summary": f"{alert.rule_name} on {alert.service}",
                "severity": severity_map.get(alert.severity, "error"),
                "source": "omnipotent",
                "custom_details": {
                    "message": alert.message,
                    "value": alert.value,
                    "threshold": alert.threshold,
                },
            },
        }

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://events.pagerduty.com/v2/enqueue",
                json=payload,
                timeout=_settings.http_timeout_default,
            )
            return resp.status_code == 202

    async def _notify_opsgenie(self, alert: AlertEvent) -> bool:
        """Send alert to OpsGenie.

        Uses the OpsGenie REST API v2 to create/close alerts with full metadata.
        """
        from app.integrations.opsgenie_client import (
            OpsGenieClient,
            OpsGenieConfig,
            AlertPriority,
        )

        api_key = get_settings().opsgenie_api_key
        if not api_key:
            log.debug("OpsGenie not configured (OPSGENIE_API_KEY not set), skipping notification")
            return False

        severity_map = {
            AlertSeverity.CRITICAL: AlertPriority.P1,
            AlertSeverity.HIGH: AlertPriority.P2,
            AlertSeverity.MEDIUM: AlertPriority.P3,
            AlertSeverity.LOW: AlertPriority.P4,
            AlertSeverity.INFO: AlertPriority.P5,
        }

        priority = severity_map.get(alert.severity, AlertPriority.P3)
        alias = f"omnipotent-{alert.rule_id}-{alert.service}"

        config = OpsGenieConfig(api_key=api_key)
        client = OpsGenieClient(config)

        try:
            await client.connect()

            if alert.status == "firing":
                rule = self._rules.get(alert.rule_id)
                runbook_url = rule.annotations.get("runbook_url") if rule else None

                description = (
                    f"Alert: {alert.rule_name}\n"
                    f"Service: {alert.service}\n"
                    f"Message: {alert.message}\n"
                    f"Value: {alert.value:.2f} (threshold: {alert.threshold})\n"
                    f"Started: {alert.started_at.isoformat()}"
                )

                if runbook_url:
                    description += f"\n\nRunbook: {runbook_url}"

                await client.create_alert(
                    message=f"[{alert.severity.value.upper()}] {alert.rule_name} on {alert.service}",
                    priority=priority,
                    alias=alias,
                    description=description,
                    tags=[alert.severity.value, alert.service, "omnipotent"],
                    details={
                        "rule_id": alert.rule_id,
                        "service": alert.service,
                        "value": str(alert.value),
                        "threshold": str(alert.threshold),
                        "runbook_url": runbook_url or "",
                    },
                    source="omnipotent",
                )
                log.info("OpsGenie alert created: %s", alias)
            else:
                await client.close_alert(
                    identifier=alias,
                    identifier_type="alias",
                    note=f"Alert resolved: {alert.message}",
                )
                log.info("OpsGenie alert closed: %s", alias)

            return True

        except Exception as e:
            log.error("OpsGenie notification failed: %s", e)
            return False
        finally:
            await client.close()

    async def start(self, eval_interval: int = 60) -> None:
        """Start the alert evaluation loop."""
        if self._running:
            return

        self._running = True
        log.info("Starting alert engine with %d rules", len(self._rules))

        async def eval_loop() -> None:
            while self._running:
                try:
                    services = list(set(
                        k.split(":")[0]
                        for k in self._metric_history.keys()
                    ))
                    if services:
                        new_alerts = self.evaluate_all(services)
                        for alert in new_alerts:
                            rule = self._rules.get(alert.rule_id)
                            if rule:
                                await self.send_notification(
                                    alert, rule.notification_channels
                                )
                except Exception as e:
                    log.error("Error in alert evaluation: %s", e)

                await asyncio.sleep(eval_interval)

        self._eval_task = asyncio.create_task(eval_loop())

    async def stop(self) -> None:
        """Stop the alert evaluation loop."""
        self._running = False
        if self._eval_task:
            self._eval_task.cancel()
            try:
                await self._eval_task
            except asyncio.CancelledError:
                pass
        log.info("Alert engine stopped")


_engine: AlertEngine | None = None


def get_engine() -> AlertEngine:
    """Get the singleton alert engine instance."""
    global _engine
    if _engine is None:
        _engine = AlertEngine()
    return _engine
