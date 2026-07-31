"""Webhook receivers for various alerting platforms."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.incidents.store import get_incident_store
from app.incidents.models import IncidentSeverity

log = logging.getLogger("devcopilot.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookResponse(BaseModel):
    status: str
    incident_id: str | None = None
    message: str


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC signature."""
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def auto_investigate(incident_id: str) -> None:
    """Start automatic investigation for an incident."""
    from uuid import UUID
    try:
        store = get_incident_store()
        store.start_investigation(UUID(incident_id))
        log.info("Auto-investigation started for incident %s", incident_id)
    except Exception as e:
        log.error("Failed to start auto-investigation: %s", e)


@router.post("/pagerduty", response_model=WebhookResponse)
async def pagerduty_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_pagerduty_signature: str | None = Header(None),
) -> WebhookResponse:
    """Receive PagerDuty webhook events."""
    body = await request.body()
    settings = get_settings()

    if settings.pagerduty_webhook_secret and x_pagerduty_signature:
        if not verify_signature(body, x_pagerduty_signature, settings.pagerduty_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    messages = data.get("messages", [])
    created_incidents = []

    for msg in messages:
        event = msg.get("event")
        incident_data = msg.get("incident", {})

        if event == "incident.triggered":
            urgency = incident_data.get("urgency", "low")
            severity = (
                IncidentSeverity.CRITICAL if urgency == "high"
                else IncidentSeverity.MEDIUM
            )

            store = get_incident_store()
            incident = store.create_incident(
                title=incident_data.get("title", "PagerDuty Alert"),
                description=incident_data.get("description"),
                severity=severity,
                service=incident_data.get("service", {}).get("name", "unknown"),
                tags=["pagerduty", f"pd-{incident_data.get('id', 'unknown')}"],
            )
            created_incidents.append(str(incident.id))

            if settings.copilot_auto_investigate:
                background_tasks.add_task(auto_investigate, str(incident.id))

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
) -> WebhookResponse:
    """Receive Prometheus Alertmanager webhook events."""
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
        incident = store.create_incident(
            title=labels.get("alertname", "Prometheus Alert"),
            description=annotations.get("description") or annotations.get("summary"),
            severity=severity,
            service=labels.get("service", labels.get("job", "unknown")),
            tags=["alertmanager", labels.get("alertname", "unknown")],
        )
        created_incidents.append(str(incident.id))

        settings = get_settings()
        if settings.copilot_auto_investigate:
            background_tasks.add_task(auto_investigate, str(incident.id))

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
) -> WebhookResponse:
    """Receive Datadog webhook events."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    alert_type = data.get("alert_type", "")
    if alert_type not in ("error", "warning"):
        return WebhookResponse(status="ignored", message="Not an alert event")

    severity = (
        IncidentSeverity.CRITICAL if alert_type == "error"
        else IncidentSeverity.HIGH
    )

    store = get_incident_store()
    incident = store.create_incident(
        title=data.get("title", "Datadog Alert"),
        description=data.get("body"),
        severity=severity,
        service=data.get("tags", {}).get("service", "unknown"),
        tags=["datadog", data.get("alert_id", "unknown")],
    )

    settings = get_settings()
    if settings.copilot_auto_investigate:
        background_tasks.add_task(auto_investigate, str(incident.id))

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
) -> WebhookResponse:
    """Receive Grafana alerting webhook events."""
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

    tags = data.get("tags", {})
    severity = severity_map.get(
        tags.get("severity", "warning"),
        IncidentSeverity.MEDIUM,
    )

    store = get_incident_store()
    incident = store.create_incident(
        title=data.get("title", data.get("ruleName", "Grafana Alert")),
        description=data.get("message"),
        severity=severity,
        service=tags.get("service", "unknown"),
        tags=["grafana", str(data.get("ruleId", "unknown"))],
    )

    settings = get_settings()
    if settings.copilot_auto_investigate:
        background_tasks.add_task(auto_investigate, str(incident.id))

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
    settings = get_settings()

    if settings.copilot_webhook_secret:
        if x_webhook_secret != settings.copilot_webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid secret")

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

    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    tags.append("generic-webhook")

    store = get_incident_store()
    incident = store.create_incident(
        title=title,
        description=description,
        severity=severity,
        service=service,
        tags=tags,
    )

    if settings.copilot_auto_investigate:
        background_tasks.add_task(auto_investigate, str(incident.id))

    log.info("Created incident from generic webhook: %s", incident.id)

    return WebhookResponse(
        status="ok",
        incident_id=str(incident.id),
        message="Incident created",
    )
