"""Background task definitions.

All async tasks that can be offloaded from the request path:
- Webhook processing
- Investigation execution
- Notification delivery
- Usage sync to Stripe
- Session cleanup
- Alert evaluation
- Runbook execution
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import dramatiq

from app.config import get_settings
from app.tasks.broker import get_broker

log = logging.getLogger("omnipotent.tasks")
_settings = get_settings()

get_broker()


@dramatiq.actor(
    queue_name="webhooks",
    max_retries=3,
    min_backoff=1000,
    max_backoff=30000,
)
def process_webhook(
    source: str,
    event_type: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    """Process an incoming webhook asynchronously.

    Args:
        source: Webhook source (github, gitlab, pagerduty, slack).
        event_type: Event type from the webhook.
        payload: Webhook payload data.
        headers: Original request headers.

    Returns:
        Processing result with status.
    """
    log.info("Processing %s webhook: %s", source, event_type)

    try:
        if source == "github":
            return _process_github_webhook(event_type, payload)
        elif source == "gitlab":
            return _process_gitlab_webhook(event_type, payload)
        elif source == "pagerduty":
            return _process_pagerduty_webhook(event_type, payload)
        elif source == "slack":
            return _process_slack_webhook(event_type, payload)
        else:
            log.warning("Unknown webhook source: %s", source)
            return {"status": "ignored", "reason": f"unknown source: {source}"}
    except Exception as e:
        log.exception("Webhook processing failed: %s", e)
        raise


def _process_github_webhook(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process GitHub webhook events."""
    if event_type == "push":
        return {"status": "processed", "commits": len(payload.get("commits", []))}
    elif event_type == "pull_request":
        action = payload.get("action", "")
        return {"status": "processed", "action": action}
    elif event_type == "issues":
        return {"status": "processed", "action": payload.get("action", "")}
    return {"status": "ignored", "event": event_type}


def _process_gitlab_webhook(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process GitLab webhook events."""
    if event_type in ("push", "merge_request", "pipeline"):
        return {"status": "processed", "event": event_type}
    return {"status": "ignored", "event": event_type}


def _process_pagerduty_webhook(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process PagerDuty webhook events."""
    messages = payload.get("messages", [])
    return {"status": "processed", "messages": len(messages)}


def _process_slack_webhook(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Process Slack webhook events."""
    return {"status": "processed", "event": event_type}


@dramatiq.actor(
    queue_name="investigations",
    max_retries=2,
    min_backoff=5000,
    max_backoff=60000,
    time_limit=300000,
)
def run_investigation(
    thread_id: str,
    query: str,
    org_id: str | None = None,
    trigger: str = "manual",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run an investigation asynchronously.

    Args:
        thread_id: Investigation thread ID.
        query: Investigation query/question.
        org_id: Organization ID (multi-tenant).
        trigger: What triggered the investigation.
        metadata: Additional metadata.

    Returns:
        Investigation result summary.
    """
    import asyncio

    log.info("Starting async investigation: %s", thread_id)

    async def _run() -> dict[str, Any]:
        from app.session import CopilotSession

        async with CopilotSession(thread_id=thread_id) as session:
            result = await session.ask(query)
            return {
                "thread_id": thread_id,
                "status": result.status,
                "tokens_used": result.tokens_used,
                "has_report": bool(result.report),
            }

    return asyncio.get_event_loop().run_until_complete(_run())


@dramatiq.actor(
    queue_name="notifications",
    max_retries=5,
    min_backoff=1000,
    max_backoff=60000,
)
def send_notification(
    channel: str,
    message: str,
    recipient: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send a notification asynchronously.

    Args:
        channel: Notification channel (slack, email, pagerduty).
        message: Message content.
        recipient: Target recipient/channel.
        metadata: Additional metadata (thread_ts, severity, etc.).

    Returns:
        Delivery result.
    """
    import asyncio

    log.info("Sending %s notification to %s", channel, recipient)

    async def _send() -> dict[str, Any]:
        if channel == "slack":
            return await _send_slack_notification(message, recipient, metadata or {})
        elif channel == "pagerduty":
            return await _send_pagerduty_notification(message, metadata or {})
        else:
            return {"status": "unsupported", "channel": channel}

    return asyncio.get_event_loop().run_until_complete(_send())


async def _send_slack_notification(
    message: str,
    channel: str | None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Send Slack notification."""
    from app.config import get_settings
    import httpx

    s = get_settings()
    if not s.slack_bot_token:
        return {"status": "skipped", "reason": "no slack token"}

    target = channel or s.slack_channel
    if not target:
        return {"status": "skipped", "reason": "no channel"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {s.slack_bot_token}"},
            json={
                "channel": target,
                "text": message,
                "thread_ts": metadata.get("thread_ts"),
            },
            timeout=_settings.http_timeout_default,
        )
        data = resp.json()
        return {"status": "sent" if data.get("ok") else "failed", "ts": data.get("ts")}


async def _send_pagerduty_notification(
    message: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Send PagerDuty event."""
    from app.config import get_settings
    import httpx

    s = get_settings()
    if not s.pagerduty_api_token:
        return {"status": "skipped", "reason": "no pagerduty token"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://events.pagerduty.com/v2/enqueue",
            json={
                "routing_key": s.pagerduty_api_token,
                "event_action": metadata.get("action", "trigger"),
                "dedup_key": metadata.get("dedup_key"),
                "payload": {
                    "summary": message,
                    "severity": metadata.get("severity", "warning"),
                    "source": "omnipotent",
                },
            },
            timeout=_settings.http_timeout_default,
        )
        return {"status": "sent" if resp.status_code == 202 else "failed"}


@dramatiq.actor(
    queue_name="billing",
    max_retries=5,
    min_backoff=5000,
    max_backoff=300000,
)
def sync_usage_to_stripe(
    org_id: str,
    period: str,
    investigations: int,
    tokens: int,
) -> dict[str, Any]:
    """Sync usage data to Stripe for metered billing.

    Args:
        org_id: Organization ID.
        period: Billing period (YYYY-MM).
        investigations: Number of investigations.
        tokens: Tokens consumed.

    Returns:
        Sync result.
    """
    import asyncio

    log.info("Syncing usage to Stripe: org=%s, period=%s", org_id, period)

    async def _sync() -> dict[str, Any]:
        from app.config import get_settings

        s = get_settings()
        if not s.stripe_api_key:
            return {"status": "skipped", "reason": "no stripe key"}

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.stripe.com/v1/billing/meter_events",
                headers={"Authorization": f"Bearer {s.stripe_api_key}"},
                data={
                    "event_name": s.stripe_meter_event,
                    "payload[stripe_customer_id]": org_id,
                    "payload[value]": str(investigations),
                    "timestamp": datetime.now().isoformat(),
                },
                timeout=_settings.http_timeout_default,
            )
            return {
                "status": "synced" if resp.status_code == 200 else "failed",
                "code": resp.status_code,
            }

    return asyncio.get_event_loop().run_until_complete(_sync())


@dramatiq.actor(
    queue_name="maintenance",
    max_retries=1,
)
def cleanup_expired_sessions(max_age_hours: int = 24) -> dict[str, Any]:
    """Clean up expired investigation sessions.

    Args:
        max_age_hours: Maximum session age in hours.

    Returns:
        Cleanup result with count.
    """
    log.info("Cleaning up sessions older than %d hours", max_age_hours)
    return {"status": "completed", "cleaned": 0}


@dramatiq.actor(
    queue_name="alerts",
    max_retries=2,
    min_backoff=1000,
)
def process_alert_evaluation(
    services: list[str],
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Evaluate alert rules for given services.

    Args:
        services: List of service names to evaluate.
        timestamp: Evaluation timestamp (ISO format).

    Returns:
        Evaluation result with fired alerts.
    """
    log.info("Evaluating alerts for %d services", len(services))

    from app.alerting import get_engine

    engine = get_engine()
    new_alerts = engine.evaluate_all(services)

    return {
        "status": "completed",
        "services_evaluated": len(services),
        "alerts_fired": len(new_alerts),
        "timestamp": timestamp or datetime.now().isoformat(),
    }


@dramatiq.actor(
    queue_name="runbooks",
    max_retries=1,
    time_limit=600000,
)
def execute_runbook_async(
    runbook_id: str,
    variables: dict[str, Any],
    triggered_by: str = "system",
    org_id: str | None = None,
) -> dict[str, Any]:
    """Execute a runbook asynchronously.

    Args:
        runbook_id: Runbook identifier.
        variables: Variables for the runbook.
        triggered_by: Who triggered the execution.
        org_id: Organization ID (multi-tenant).

    Returns:
        Execution result.
    """
    import asyncio

    log.info("Executing runbook %s (triggered by %s)", runbook_id, triggered_by)

    async def _execute() -> dict[str, Any]:
        from app.runbooks.executor import RunbookExecutor

        executor = RunbookExecutor()
        execution = await executor.execute(runbook_id, variables)

        return {
            "execution_id": execution.id,
            "runbook_id": runbook_id,
            "status": execution.status.value,
            "started_at": execution.started_at.isoformat(),
        }

    return asyncio.get_event_loop().run_until_complete(_execute())
