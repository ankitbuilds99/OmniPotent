"""Webhook receivers for various alerting platforms."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.database.orgs import org_uuid_for
from app.incidents.store import get_incident_store
from app.incidents.models import Incident, IncidentSeverity
from app.integrations import pagerduty as pd_webhook
from app.tenant_context import tenant_id

log = logging.getLogger("omnipotent.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# The graph reports its confidence as a bucket, not a number, and
# `Investigation.overall_confidence` is a float. These are the bucket values the
# UI renders — a mapping, not a measurement.
_CONFIDENCE_SCORE = {"high": 0.9, "medium": 0.6, "low": 0.3}


class WebhookResponse(BaseModel):
    status: str
    incident_id: str | None = None
    message: str


def _normalize_tags(raw: Any) -> dict[str, str]:
    """Coerce a provider's `tags` field into a `{key: value}` mapping.

    Datadog sends `$TAGS` as the comma-separated string `"service:web,env:prod"`,
    some payload templates send it as a list of `"k:v"` strings, and a
    hand-rolled payload may send an object. The handlers used to call
    `data.get("tags", {}).get("service")` directly, so anything but an object
    was an `AttributeError` — a 500 back to the alerting platform, which then
    retried into the same crash.
    """
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = [str(t) for t in raw]
    else:
        return {}
    tags: dict[str, str] = {}
    for item in items:
        item = item.strip()
        if not item:
            continue
        key, sep, value = item.partition(":")
        tags[key.strip()] = value.strip() if sep else ""
    return tags


def _basic_auth_password(header: str) -> str:
    """The password out of an `Authorization: Basic` header, or ""."""
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1].strip(), validate=True).decode()
    except (IndexError, binascii.Error, UnicodeDecodeError):
        return ""
    return decoded.partition(":")[2]


def _verify_shared_secret(request: Request, x_webhook_secret: str | None, source: str) -> None:
    """Authenticate a receiver that has no provider signature of its own.

    Alertmanager, Datadog and Grafana do not sign their deliveries — there is no
    HMAC to check, so the only credential available is a shared secret the sender
    is configured to present. Until now these three had no check at all; they were
    reachable only because `require_auth` demanded the app bearer token in front of
    them, and that same gate is what made every real alert delivery 401 (the bypass
    listed `/webhooks/` while the router is mounted at `/api/webhooks/`). Opening
    the prefix without this would have turned a "no alert ever arrives" bug into an
    unauthenticated incident-creation endpoint, so the credential check moves here
    rather than disappearing.

    Accepted, in the order a sender is likely to manage:
      - `X-Webhook-Secret: <secret>`   (Datadog custom headers, curl)
      - `Authorization: Bearer <secret>`  (Grafana contact points, Alertmanager
        `http_config.authorization`)
      - `Authorization: Basic <base64 user:secret>`  (Alertmanager `basic_auth`,
        the only option in older Grafana notifiers — the username is ignored)

    Either COPILOT_WEBHOOK_SECRET or COPILOT_API_TOKEN satisfies it, so a
    deployment that already hands its token to a sender does not need a second
    one. When neither is configured the deployment has no authentication at all
    (the local-dev default, where `require_auth` is likewise a no-op) and the
    receiver stays open — this gate follows the app's auth posture, it does not
    invent a stricter one.
    """
    settings = get_settings()
    expected = [s for s in (settings.copilot_webhook_secret, settings.copilot_api_token) if s]
    if not expected:
        return

    auth = request.headers.get("authorization", "")
    scheme, _, credential = auth.partition(" ")
    presented = [x_webhook_secret or ""]
    if scheme.lower() == "bearer":
        presented.append(credential.strip())
    elif scheme.lower() == "basic":
        presented.append(_basic_auth_password(auth))

    for candidate in presented:
        if candidate and any(hmac.compare_digest(candidate, e) for e in expected):
            return

    log.warning("Rejected unauthenticated %s webhook delivery", source)
    raise HTTPException(
        status_code=401,
        detail=(
            f"Unauthenticated {source} delivery. Configure the sender to send the "
            "shared secret as X-Webhook-Secret, Authorization: Bearer, or basic-auth "
            "password (COPILOT_WEBHOOK_SECRET, or the app's COPILOT_API_TOKEN)."
        ),
    )


def _schedule_investigation(background_tasks: BackgroundTasks, incident: Incident) -> None:
    """Queue the real investigation for a freshly-ingested alert, if enabled.

    The org is captured here, from the request, and passed down explicitly: the
    background task runs after the response, where the request-scoped tenant
    contextvar is not guaranteed to still be set.
    """
    if not get_settings().copilot_auto_investigate:
        return
    background_tasks.add_task(
        auto_investigate,
        str(incident.id),
        incident.title,
        incident.service,
        tenant_id(),
    )


async def auto_investigate(
    incident_id: str, title: str = "", service: str = "", org: str = ""
) -> None:
    """Run an investigation for an incident an alert webhook just created.

    This used to call `IncidentStore.start_investigation()` and stop. That wrote
    a row with six agents parked at `idle` and a timeline entry claiming a
    "6-agent parallel investigation initiated", and then nothing ran — no LLM
    call, no task, no agent ever left `idle`. The record looked like an
    investigation without being one.

    It now goes through the same queue the PagerDuty receiver and the SLO poller
    use (`_enqueue_investigation` -> the agent graph), and when there is no LLM
    provider key it says so in the log and creates nothing, rather than leaving a
    placeholder investigation behind.
    """
    from app.api.main import _enqueue_investigation
    from app.llm import active_api_key

    try:
        if not active_api_key():
            log.warning(
                "auto-investigate skipped for incident %s: no LLM provider key configured "
                "(set the API key for COPILOT_PROVIDER, e.g. ANTHROPIC_API_KEY)",
                incident_id,
            )
            return
        store = get_incident_store()
        started = await store.start_investigation(UUID(incident_id), org=org_uuid_for(org))
        if started is None:
            log.error("auto-investigate: incident %s not found", incident_id)
            return
        await _enqueue_investigation(
            {
                "id": incident_id,
                "source": "incident",
                "title": title,
                "service": service,
                "incident_id": incident_id,
                "org": org,
            }
        )
        log.info("Auto-investigation queued for incident %s", incident_id)
    except Exception:
        log.exception("Failed to start auto-investigation for incident %s", incident_id)


async def record_incident_outcome(
    incident: dict, result: Any | None, error: str | None = None
) -> None:
    """Write a triggered investigation's real outcome back onto its incident row.

    Called by `app.api.main._run_triggered_investigation` once the agent run
    finishes, for the jobs `auto_investigate()` enqueues (they carry
    `incident_id`). Without it the incident's investigation would keep the
    values it was created with — `completed_at` null, `root_cause` null —
    whatever the run actually concluded.
    """
    incident_id = incident.get("incident_id")
    if not incident_id:
        return  # not an alert-ingested incident (e.g. the PagerDuty/SLO paths)
    store = get_incident_store()
    org = org_uuid_for(str(incident.get("org") or ""))
    try:
        if error is not None or result is None:
            await store.record_investigation_failure(
                UUID(incident_id), error or "investigation did not produce a result", org=org
            )
            return
        if getattr(result, "status", "") != "completed":
            await store.record_investigation_failure(
                UUID(incident_id),
                f"investigation paused ({getattr(result, 'status', 'unknown')})",
                org=org,
            )
            return
        report = getattr(result, "report", None) or {}
        confidence = str(report.get("calibrated_confidence") or report.get("confidence") or "").lower()
        await store.record_investigation_result(
            UUID(incident_id),
            root_cause=report.get("root_cause") or None,
            recommended_actions=[str(a) for a in (report.get("recommended_actions") or [])],
            confidence=_CONFIDENCE_SCORE.get(confidence, 0.0),
            org=org,
        )
    except Exception:
        log.exception("Failed to record investigation outcome for incident %s", incident_id)


def _pagerduty_triggered(payload: dict) -> list[dict[str, Any]]:
    """The triggered incidents in a PagerDuty delivery, v3 or legacy v2.

    v3 (current) nests a single event under `event.data`; v2 sent a list under
    `messages`. This handler only understood v2, so a real v3 delivery parsed to
    "Processed 0 incidents" and was dropped.
    """
    triggered: list[dict[str, Any]] = []
    parsed = pd_webhook.parse_incident(payload)
    if parsed is not None:  # v3
        data = (payload.get("event") or {}).get("data") or {}
        if parsed.get("id") and parsed["type"].endswith(".triggered"):
            triggered.append(
                {
                    "id": parsed["id"],
                    "title": parsed["title"],
                    "description": data.get("description"),
                    "service": parsed.get("service") or "unknown",
                    "urgency": data.get("urgency", "low"),
                }
            )
        return triggered
    for msg in payload.get("messages") or []:  # v2 / legacy
        if not str(msg.get("event", "")).startswith("incident.trigger"):
            continue
        data = msg.get("incident") or {}
        triggered.append(
            {
                "id": data.get("id", "unknown"),
                "title": data.get("title") or data.get("summary") or "PagerDuty Alert",
                "description": data.get("description"),
                "service": (data.get("service") or {}).get("name") or "unknown",
                "urgency": data.get("urgency", "low"),
            }
        )
    return triggered


@router.post("/pagerduty", response_model=WebhookResponse)
async def pagerduty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_pagerduty_signature: str | None = Header(None),
) -> WebhookResponse:
    """Receive PagerDuty webhook events and open an incident for each trigger.

    There are two PagerDuty receivers; point PagerDuty at one of them. This one
    records an incident in the incident store (so it shows up in the incidents
    UI) and then investigates it; `/webhooks/pagerduty` only starts the
    investigation. They verify deliveries identically.

    Verification is `app.integrations.pagerduty.verify_signature` — the same
    implementation `/webhooks/pagerduty` uses — so the two receivers accept and
    reject exactly the same deliveries. This route used to disagree with it on
    every point that matters: it built a `sha256=<hex>` header (PagerDuty sends
    `v1=<hex>`, so genuine deliveries 401'd), and it skipped verification
    entirely when the header was simply absent, so an unsigned body from anyone
    created incidents. It now fails closed: no configured secret, or no valid
    signature, is a rejection.
    """
    body = await request.body()
    settings = get_settings()

    if not settings.pagerduty_webhook_secret:
        raise HTTPException(
            status_code=401,
            detail="PAGERDUTY_WEBHOOK_SECRET is not configured; refusing unverified delivery",
        )
    if not pd_webhook.verify_signature(
        settings.pagerduty_webhook_secret, body, x_pagerduty_signature or ""
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    created_incidents = []
    store = get_incident_store()

    for event in _pagerduty_triggered(data):
        severity = (
            IncidentSeverity.CRITICAL if event["urgency"] == "high"
            else IncidentSeverity.MEDIUM
        )
        incident = await store.create_incident(
            title=str(event["title"]),
            description=event["description"],
            severity=severity,
            service=str(event["service"]),
            tags=["pagerduty", f"pd-{event['id']}"],
        )
        created_incidents.append(str(incident.id))
        _schedule_investigation(background_tasks, incident)
        log.info("Created incident from PagerDuty: %s", incident.id)

    return WebhookResponse(
        status="ok",
        incident_id=created_incidents[0] if created_incidents else None,
        message=f"Processed {len(created_incidents)} incidents",
    )


@router.post("/alertmanager", response_model=WebhookResponse)
async def alertmanager_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(None),
) -> WebhookResponse:
    """Receive Prometheus Alertmanager webhook events."""
    _verify_shared_secret(request, x_webhook_secret, "Alertmanager")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    alerts = data.get("alerts", [])
    created_incidents = []

    severity_map = {
        "critical": IncidentSeverity.CRITICAL,
        "warning": IncidentSeverity.HIGH,
        "info": IncidentSeverity.LOW,
    }

    for alert in alerts:
        if alert.get("status") != "firing":
            continue

        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        severity = severity_map.get(
            labels.get("severity", "warning"),
            IncidentSeverity.MEDIUM,
        )

        store = get_incident_store()
        incident = await store.create_incident(
            title=labels.get("alertname", "Prometheus Alert"),
            description=annotations.get("description") or annotations.get("summary"),
            severity=severity,
            service=labels.get("service", labels.get("job", "unknown")),
            tags=["alertmanager", labels.get("alertname", "unknown")],
        )
        created_incidents.append(str(incident.id))

        _schedule_investigation(background_tasks, incident)

        log.info("Created incident from Alertmanager: %s", incident.id)

    return WebhookResponse(
        status="ok",
        incident_id=created_incidents[0] if created_incidents else None,
        message=f"Processed {len(created_incidents)} alerts",
    )


@router.post("/datadog", response_model=WebhookResponse)
async def datadog_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(None),
) -> WebhookResponse:
    """Receive Datadog webhook events."""
    _verify_shared_secret(request, x_webhook_secret, "Datadog")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # `$ALERT_TYPE` in a custom payload; Datadog's stock webhook template ships
    # `event_type` instead, so a default-configured webhook was silently ignored.
    alert_type = str(data.get("alert_type") or data.get("event_type") or "").strip().lower()
    severity_map = {
        "error": IncidentSeverity.CRITICAL,
        "warning": IncidentSeverity.HIGH,
    }
    if alert_type not in severity_map:
        # success / info / recovery, or a non-alert event type.
        return WebhookResponse(
            status="ignored", message=f"Not an alert event (alert_type={alert_type or 'missing'})"
        )
    severity = severity_map[alert_type]

    tags = _normalize_tags(data.get("tags"))
    store = get_incident_store()
    incident = await store.create_incident(
        title=str(data.get("title") or "Datadog Alert"),
        description=data.get("body"),
        severity=severity,
        service=tags.get("service") or tags.get("host") or "unknown",
        # str(): Datadog sends a numeric alert id, and a non-string tag is
        # written to the row happily and then fails validation on every
        # subsequent read — one such alert made GET /api/incidents 422 for the
        # whole org.
        tags=["datadog", str(data.get("alert_id", "unknown"))],
    )

    _schedule_investigation(background_tasks, incident)

    log.info("Created incident from Datadog: %s", incident.id)

    return WebhookResponse(
        status="ok",
        incident_id=str(incident.id),
        message="Incident created",
    )


@router.post("/grafana", response_model=WebhookResponse)
async def grafana_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(None),
) -> WebhookResponse:
    """Receive Grafana alerting webhook events."""
    _verify_shared_secret(request, x_webhook_secret, "Grafana")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    state = data.get("state", "")
    if state != "alerting":
        return WebhookResponse(status="ignored", message="Not alerting state")

    severity_map = {
        "critical": IncidentSeverity.CRITICAL,
        "high": IncidentSeverity.HIGH,
        "warning": IncidentSeverity.MEDIUM,
        "info": IncidentSeverity.LOW,
    }

    # Same coercion as Datadog: Grafana's tag field is an object in the legacy
    # alerting payload but a "k=v" list in other integrations, and `.get()` on a
    # list is a 500.
    tags = _normalize_tags(data.get("tags"))
    severity = severity_map.get(
        tags.get("severity", "warning"),
        IncidentSeverity.MEDIUM,
    )

    store = get_incident_store()
    incident = await store.create_incident(
        title=str(data.get("title") or data.get("ruleName") or "Grafana Alert"),
        description=data.get("message"),
        severity=severity,
        service=tags.get("service", "unknown"),
        tags=["grafana", str(data.get("ruleId", "unknown"))],
    )

    _schedule_investigation(background_tasks, incident)

    log.info("Created incident from Grafana: %s", incident.id)

    return WebhookResponse(
        status="ok",
        incident_id=str(incident.id),
        message="Incident created",
    )


@router.post("/generic", response_model=WebhookResponse)
async def generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(None),
) -> WebhookResponse:
    """Generic webhook receiver for custom integrations."""
    _verify_shared_secret(request, x_webhook_secret, "generic webhook")

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    title = data.get("title") or data.get("name") or data.get("alert", "Alert")
    description = data.get("description") or data.get("message") or data.get("body")
    service = data.get("service") or data.get("source") or "unknown"

    severity_str = str(data.get("severity", "medium")).lower()
    severity_map = {
        "critical": IncidentSeverity.CRITICAL,
        "high": IncidentSeverity.HIGH,
        "medium": IncidentSeverity.MEDIUM,
        "low": IncidentSeverity.LOW,
        "p1": IncidentSeverity.CRITICAL,
        "p2": IncidentSeverity.HIGH,
        "p3": IncidentSeverity.MEDIUM,
        "p4": IncidentSeverity.LOW,
    }
    severity = severity_map.get(severity_str, IncidentSeverity.MEDIUM)

    raw_tags = data.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = [str(t) for t in raw_tags] if isinstance(raw_tags, (list, tuple)) else []
    tags.append("generic-webhook")

    store = get_incident_store()
    incident = await store.create_incident(
        title=str(title),
        description=description,
        severity=severity,
        service=str(service),
        tags=tags,
    )

    _schedule_investigation(background_tasks, incident)

    log.info("Created incident from generic webhook: %s", incident.id)

    return WebhookResponse(
        status="ok",
        incident_id=str(incident.id),
        message="Incident created",
    )
