"""Background task definitions.

All async tasks that can be offloaded from the request path:
- Investigation execution
- Notification delivery
- Usage sync to Stripe
- Alert evaluation
- Runbook execution

Actors that need to await something call ``asyncio.run``. They used to call
``asyncio.get_event_loop().run_until_complete``, which raises
``RuntimeError: There is no current event loop in thread`` on Python 3.12+ —
Dramatiq runs actors in worker threads, and a thread has no loop unless one is
made for it. Four of the seven actors would have failed on their first execution,
which is how this package reached production-shaped code without ever running.
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
    """Acknowledge and summarise a webhook. Does NOT act on it.

    The per-source helpers below count what arrived and return "processed"; none of
    them create an incident, start an investigation, or notify anyone. The API's own
    webhook routes do that work synchronously. Nothing enqueues this actor, and the
    API routes were deliberately left alone rather than pointed here — routing a
    live webhook path through this would turn real handling into a counter that
    reports success.

    Kept because the actor, queue and retry configuration are the useful skeleton
    for moving webhook handling off the request path later; the handlers below are
    what needs writing first.

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

    Runs correctly in a worker — ``CopilotSession`` builds its own state — but the
    report it produces stays in the worker. No result backend is configured, so the
    returned summary is discarded, and the session holding the report is in the
    worker's memory, so the API's ``/investigations/{thread_id}`` cannot find it.
    Enqueue this only once investigation state is in a shared store; until then the
    API's own job queue (:mod:`app.jobqueue`) is the path that returns a result.

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

    return asyncio.run(_run())


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

    return asyncio.run(_send())


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

    return asyncio.run(_sync())


@dramatiq.actor(
    queue_name="maintenance",
    max_retries=1,
)
def cleanup_expired_sessions(max_age_hours: int = 24) -> dict[str, Any]:
    """Report that session cleanup cannot run from a worker process.

    Investigation sessions live in ``app.api.main._SESSIONS``, a dict in the API
    process. A Dramatiq worker is a separate process and cannot see or evict them,
    so there is nothing this can clean.

    It previously returned ``{"status": "completed", "cleaned": 0}``, which reads in
    a dashboard as "cleanup ran, nothing was due" rather than "cleanup is not
    wired". It is not on the scheduler for that reason. This becomes implementable
    once sessions move to a shared store; the API evicts idle sessions itself in the
    meantime (see the ``copilot_max_sessions`` cap).

    Args:
        max_age_hours: Maximum session age in hours.

    Returns:
        A not_applicable status naming the reason.
    """
    log.warning(
        "cleanup_expired_sessions called (max_age_hours=%d) but sessions are held "
        "in the API process; a worker cannot evict them",
        max_age_hours,
    )
    return {
        "status": "not_applicable",
        "cleaned": 0,
        "reason": "sessions are in-process in the API; no shared store to clean",
    }


@dramatiq.actor(
    queue_name="alerts",
    max_retries=2,
    min_backoff=1000,
)
def process_alert_evaluation(
    services: list[str],
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Evaluate alert rules for given services, in a process that has the metrics.

    Only meaningful in the process metrics are recorded into. The alert engine keeps
    its rules and metric history in a per-process singleton, and metrics arrive
    through the MCP alerting server inside the API — so a worker's engine is empty.
    Evaluating from an empty engine is not merely useless: ``get_metric_value``
    returns None for every service, and an ABSENCE rule treats None as "No metrics
    received" and fires against a service that is perfectly healthy.

    This declines rather than reporting zero, for the same reason: "evaluated 12
    services, 0 alerts" from a process holding no metrics is an all-clear nobody
    checked. This is why the actor is not on the scheduler — see
    :func:`app.tasks.worker.run_scheduler`.

    Args:
        services: Service names to evaluate. Empty means every service the engine
            has data for; passing a name it has no data for is what fires the false
            absence alert described above.
        timestamp: Evaluation timestamp (ISO format).

    Returns:
        Evaluation result with fired alerts, or a no_metrics status.
    """
    from app.alerting import get_engine

    engine = get_engine()

    if not engine.has_metrics():
        log.warning(
            "Alert evaluation requested for %d service(s) but this process's alert "
            "engine holds no metrics; declining so ABSENCE rules do not fire against "
            "healthy services",
            len(services),
        )
        return {
            "status": "no_metrics",
            "services_evaluated": 0,
            "alerts_fired": 0,
            "reason": "metric history is per-process and empty here; evaluate in the "
                      "process that records metrics",
            "timestamp": timestamp or datetime.now().isoformat(),
        }

    known = engine.known_services()
    targets = [s for s in services if s in known] if services else known
    skipped = sorted(set(services) - set(known))
    if skipped:
        log.warning("Skipping alert evaluation for services with no metrics: %s", ", ".join(skipped))

    log.info("Evaluating alerts for %d services", len(targets))
    new_alerts = engine.evaluate_all(targets)

    return {
        "status": "completed",
        "services_evaluated": len(targets),
        "services_skipped": skipped,
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

    Reads the definition from the shared store and writes progress back to it, so
    the ``execution_id`` returned here resolves through
    ``GET /api/runbooks/executions/{id}`` on the API. Both of the limits this
    docstring used to record — the worker seeing only DEFAULT_RUNBOOKS, and its
    execution record living in a process-local dict — were consequences of
    runbook state being per-process, and cleared when it moved to Postgres
    (migration ``005_runbooks``).

    The worker has no request context, so ``org_id`` must be passed by the
    enqueuer; it falls back to the same default the API files unattributed rows
    under rather than guessing.

    Args:
        runbook_id: Runbook identifier.
        variables: Variables for the runbook.
        triggered_by: Who triggered the execution.
        org_id: Organization ID (multi-tenant).

    Returns:
        Execution result.
    """
    import asyncio
    import uuid

    log.info("Executing runbook %s (triggered by %s)", runbook_id, triggered_by)

    async def _execute() -> dict[str, Any]:
        from app.runbooks import store
        from app.runbooks.executor import RunbookExecutor
        from app.runbooks.models import RunbookExecution
        from app.tenant_context import DEFAULT_STORAGE_ORG

        scope = org_id or DEFAULT_STORAGE_ORG
        await store.ensure_builtins(scope)
        runbook = await store.load_runbook(scope, runbook_id)
        if runbook is None:
            raise ValueError(f"Runbook '{runbook_id}' not found for org '{scope}'")

        execution_id = f"exec-{uuid.uuid4().hex[:8]}"

        async def persist(current: RunbookExecution) -> None:
            await store.save_execution(scope, current)

        executor = RunbookExecutor()
        execution = await executor.execute(
            runbook_id,
            variables,
            triggered_by=triggered_by,
            runbook=runbook,
            execution_id=execution_id,
            on_progress=persist,
        )

        return {
            "execution_id": execution.id,
            "runbook_id": runbook_id,
            "org_id": scope,
            "status": execution.status.value,
            "started_at": execution.started_at.isoformat(),
        }

    return asyncio.run(_execute())
