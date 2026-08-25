"""Status Page API Router.

Public and admin endpoints for the status page system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, EmailStr

from app.config import get_settings
from app.status_page.models import (
    ComponentStatus,
    IncidentSeverity,
    IncidentStatus,
    MaintenanceStatus,
    SubscriptionType,
)
from app.status_page.notifications import (
    EMAIL_DELIVERY_REQUIREMENTS,
    email_delivery_configured,
    get_status_notifier,
    send_verification_email,
)
from app.status_page.store import get_status_store

router = APIRouter(prefix="/api/status", tags=["Status Page"])


# --- Request/Response Models ---

class ComponentCreateRequest(BaseModel):
    """Request to create a component."""

    name: str = Field(..., min_length=1, max_length=100, description="Component name")
    description: str = Field("", max_length=500, description="Component description")
    group: str | None = Field(None, max_length=50, description="Component group")
    display_order: int = Field(100, ge=0, description="Display order (lower = first)")


class ComponentUpdateRequest(BaseModel):
    """Request to update a component."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    status: str | None = Field(None, description="Status: operational, degraded, partial_outage, major_outage, maintenance")
    group: str | None = None
    display_order: int | None = Field(None, ge=0)


class ComponentResponse(BaseModel):
    """Component response model."""

    id: str
    name: str
    description: str
    status: str
    status_display: str
    group: str | None
    display_order: int
    created_at: datetime
    updated_at: datetime
    uptime_30d: float | None = None
    uptime_90d: float | None = None


class IncidentCreateRequest(BaseModel):
    """Request to create an incident."""

    title: str = Field(..., min_length=1, max_length=200, description="Incident title")
    message: str = Field("", max_length=2000, description="Initial message/description")
    severity: str = Field("minor", description="Severity: minor, major, critical")
    affected_components: list[str] = Field(default_factory=list, description="IDs of affected components")


class IncidentUpdateRequest(BaseModel):
    """Request to update an incident."""

    status: str | None = Field(None, description="Status: investigating, identified, monitoring, resolved")
    message: str | None = Field(None, max_length=2000, description="Update message")


class IncidentResponse(BaseModel):
    """Incident response model."""

    id: str
    title: str
    status: str
    severity: str
    message: str
    affected_components: list[str]
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    is_active: bool
    duration_minutes: float | None
    updates: list[dict[str, Any]] = []


class MaintenanceCreateRequest(BaseModel):
    """Request to create a maintenance window."""

    title: str = Field(..., min_length=1, max_length=200, description="Maintenance title")
    description: str = Field("", max_length=2000, description="Maintenance description")
    scheduled_start: datetime = Field(..., description="Scheduled start time")
    scheduled_end: datetime = Field(..., description="Scheduled end time")
    affected_components: list[str] = Field(default_factory=list, description="IDs of affected components")


class MaintenanceUpdateRequest(BaseModel):
    """Request to update a maintenance window."""

    title: str | None = None
    description: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    status: str | None = Field(None, description="Status: scheduled, in_progress, completed, cancelled")


class MaintenanceResponse(BaseModel):
    """Maintenance response model."""

    id: str
    title: str
    description: str
    status: str
    affected_components: list[str]
    scheduled_start: datetime
    scheduled_end: datetime
    actual_start: datetime | None
    actual_end: datetime | None
    is_upcoming: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SubscribeRequest(BaseModel):
    """Request to subscribe to updates."""

    email: EmailStr | None = Field(None, description="Email address for notifications")
    webhook_url: str | None = Field(None, description="Webhook URL for notifications")
    components: list[str] = Field(default_factory=list, description="Component IDs to subscribe to (empty = all)")
    notify_on_incidents: bool = Field(True, description="Notify on incidents")
    notify_on_maintenance: bool = Field(True, description="Notify on maintenance")
    notify_on_resolved: bool = Field(True, description="Notify when incidents are resolved")


class SubscribeResponse(BaseModel):
    """Subscribe response model."""

    id: str
    subscription_type: str
    target: str
    components: list[str]
    verified: bool
    created_at: datetime
    message: str


class StatusSummaryResponse(BaseModel):
    """Full status page summary."""

    overall_status: str
    overall_status_display: str
    components: list[dict[str, Any]]
    groups: dict[str | None, list[dict[str, Any]]]
    uptimes_30d: dict[str, float]
    uptimes_90d: dict[str, float]
    active_incidents: list[dict[str, Any]]
    recent_incidents: list[dict[str, Any]]
    upcoming_maintenances: list[dict[str, Any]]
    updated_at: datetime


# --- Public Endpoints ---

@router.get("", response_model=StatusSummaryResponse)
async def get_status_summary() -> StatusSummaryResponse:
    """Get the full public status page summary.

    This is the main public endpoint for the status page. Returns:
    - Overall system status
    - All components with their status and uptime
    - Active and recent incidents
    - Upcoming maintenance windows
    """
    store = get_status_store()
    summary = await store.get_status_summary()
    return StatusSummaryResponse(**summary)


@router.get("/components", response_model=list[ComponentResponse])
async def list_components(
    group: str | None = Query(None, description="Filter by component group"),
    status: str | None = Query(None, description="Filter by status"),
) -> list[ComponentResponse]:
    """List all components."""
    store = get_status_store()

    status_filter = None
    if status:
        try:
            status_filter = ComponentStatus(status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {status}")

    components = await store.list_components(group=group, status=status_filter)

    result = []
    for comp in components:
        uptime_30d = await store.calculate_uptime(comp.id, days=30)
        uptime_90d = await store.calculate_uptime(comp.id, days=90)
        result.append(ComponentResponse(
            **comp.to_dict(),
            uptime_30d=uptime_30d,
            uptime_90d=uptime_90d,
        ))

    return result


@router.get("/components/{component_id}", response_model=ComponentResponse)
async def get_component(component_id: str) -> ComponentResponse:
    """Get a specific component."""
    store = get_status_store()
    component = await store.get_component(component_id)

    if not component:
        raise HTTPException(404, f"Component not found: {component_id}")

    uptime_30d = await store.calculate_uptime(component.id, days=30)
    uptime_90d = await store.calculate_uptime(component.id, days=90)

    return ComponentResponse(
        **component.to_dict(),
        uptime_30d=uptime_30d,
        uptime_90d=uptime_90d,
    )


@router.get("/incidents", response_model=list[IncidentResponse])
async def list_incidents(
    active_only: bool = Query(False, description="Only return active incidents"),
    limit: int = Query(50, ge=1, le=100, description="Maximum incidents to return"),
    days: int | None = Query(None, ge=1, le=365, description="Filter incidents from last N days"),
) -> list[IncidentResponse]:
    """List incidents."""
    store = get_status_store()
    incidents = await store.list_incidents(active_only=active_only, limit=limit, days=days)
    return [IncidentResponse(**inc.to_dict()) for inc in incidents]


@router.get("/incidents/{incident_id}", response_model=IncidentResponse)
async def get_incident(incident_id: str) -> IncidentResponse:
    """Get a specific incident with its timeline."""
    store = get_status_store()
    incident = await store.get_incident(incident_id)

    if not incident:
        raise HTTPException(404, f"Incident not found: {incident_id}")

    return IncidentResponse(**incident.to_dict())


@router.get("/maintenances", response_model=list[MaintenanceResponse])
async def list_maintenances(
    upcoming_only: bool = Query(False, description="Only return upcoming maintenances"),
    limit: int = Query(50, ge=1, le=100, description="Maximum maintenances to return"),
) -> list[MaintenanceResponse]:
    """List maintenance windows."""
    store = get_status_store()
    maintenances = await store.list_maintenances(upcoming_only=upcoming_only, limit=limit)
    return [MaintenanceResponse(**m.to_dict()) for m in maintenances]


def _verification_link(http_request: Request, subscriber_id: str, token: str) -> str:
    """Where the verification link in the email points.

    `status_page_url` when an operator has set one, in preference to the host
    the request arrived on: the Host header is client-controlled, and this link
    carries a bearer token into somebody's inbox. Unset — the default — it falls
    back to the request's own base URL, which is correct for a single-origin
    deployment and is at worst the host the subscriber just used themselves.
    """
    query = urlencode({"id": subscriber_id, "token": token})
    path = router.url_path_for("verify_subscription")
    configured = (get_settings().status_page_url or "").strip().rstrip("/")
    base = configured or str(http_request.base_url).rstrip("/")
    return f"{base}{path}?{query}"


@router.post("/subscribe", response_model=SubscribeResponse, status_code=201)
async def subscribe_to_updates(
    request: SubscribeRequest,
    http_request: Request,
) -> SubscribeResponse:
    """Subscribe to status page updates.

    Provide either an email address or a webhook URL.

    An email subscription is stored unverified and the verification link is
    mailed to the address; only a verified subscription is ever notified, so an
    address that never opens the link never hears from us. Email therefore needs
    a configured email provider — without one this returns 503 rather than
    accepting an address it could never verify.

    A webhook subscription is verified on creation: possession of the URL is the
    only credential a webhook has.
    """
    store = get_status_store()

    # Validate input
    if not request.email and not request.webhook_url:
        raise HTTPException(400, "Either email or webhook_url must be provided")

    if request.email and request.webhook_url:
        raise HTTPException(400, "Provide only one of email or webhook_url")

    # Determine subscription type and target
    if request.email:
        subscription_type = SubscriptionType.EMAIL
        target = request.email
    else:
        subscription_type = SubscriptionType.WEBHOOK
        target = request.webhook_url  # type: ignore

    # Check for existing subscription
    existing = await store.get_subscriber_by_target(target)
    if existing:
        if existing.verified:
            existing_message = "Already subscribed."
        else:
            # No second email: this form is public and unauthenticated, so
            # re-sending on every POST would turn it into a way to mail an
            # address repeatedly. The link already sent still works, and
            # DELETE /api/status/subscribe/{id} clears the way for a new one.
            existing_message = (
                "Already subscribed, but not verified yet. Open the verification "
                "link already emailed to this address."
            )
        return SubscribeResponse(
            id=existing.id,
            subscription_type=existing.subscription_type.value,
            target=existing._mask_target(),
            components=existing.components,
            verified=existing.verified,
            created_at=existing.created_at,
            message=existing_message,
        )

    # Validate component IDs if provided
    if request.components:
        for comp_id in request.components:
            comp = await store.get_component(comp_id)
            if not comp:
                raise HTTPException(400, f"Invalid component ID: {comp_id}")

    is_webhook = subscription_type == SubscriptionType.WEBHOOK

    # Asked before the address is stored. An unverified subscriber is never
    # notified, and the only way to verify one is the emailed link, so with no
    # transport the row would be a customer's email address kept forever for
    # nothing while the response told them to check their inbox.
    if not is_webhook and not email_delivery_configured():
        raise HTTPException(
            503,
            "Email subscriptions are unavailable: no email provider is configured, "
            "so the verification link cannot be sent. To enable them, "
            f"{EMAIL_DELIVERY_REQUIREMENTS}.",
        )

    # The preferences and the auto-verify go in with the row. They used to be
    # assigned to the returned object afterwards, which only stored anything
    # because that object was the one in the dict.
    subscriber = await store.create_subscriber(
        subscription_type=subscription_type,
        target=target,
        components=request.components,
        verified=is_webhook,
        notify_on_incidents=request.notify_on_incidents,
        notify_on_maintenance=request.notify_on_maintenance,
        notify_on_resolved=request.notify_on_resolved,
    )

    if is_webhook:
        message = "Webhook subscription created and verified."
    elif subscriber.verified or not subscriber.verification_token:
        # A concurrent request for the same address won the insert and that
        # subscription is already verified; there is nothing left to mail.
        message = "Already subscribed."
    else:
        verify_url = _verification_link(
            http_request, subscriber.id, subscriber.verification_token
        )
        if not await send_verification_email(target, verify_url):
            # The row would be unverifiable: the token only ever leaves the
            # server in that email. Rolled back so the address is not kept and
            # so a retry is a fresh attempt rather than "already subscribed".
            await store.delete_subscriber(subscriber.id)
            raise HTTPException(
                502,
                f"Subscription not created: the verification email to {target} "
                "could not be delivered. Check the email provider configuration "
                "and try again.",
            )
        message = "Subscription created. Check your email for the verification link."

    return SubscribeResponse(
        id=subscriber.id,
        subscription_type=subscriber.subscription_type.value,
        target=subscriber._mask_target(),
        components=subscriber.components,
        verified=subscriber.verified,
        created_at=subscriber.created_at,
        message=message,
    )


@router.get("/subscribe/verify")
async def verify_subscription(
    id: str = Query(..., description="Subscriber ID"),
    token: str = Query(..., description="Verification token"),
) -> dict[str, str]:
    """Verify a subscription."""
    store = get_status_store()

    if await store.verify_subscriber(id, token):
        return {"status": "verified", "message": "Your subscription has been verified."}

    raise HTTPException(400, "Invalid verification link")


@router.delete("/subscribe/{subscriber_id}", status_code=204)
async def unsubscribe(subscriber_id: str) -> None:
    """Unsubscribe from updates."""
    store = get_status_store()

    if not await store.delete_subscriber(subscriber_id):
        raise HTTPException(404, "Subscription not found")


# --- Admin Endpoints ---

@router.post("/components", response_model=ComponentResponse, status_code=201)
async def create_component(request: ComponentCreateRequest) -> ComponentResponse:
    """Create a new component (admin only)."""
    store = get_status_store()

    component = await store.create_component(
        name=request.name,
        description=request.description,
        group=request.group,
        display_order=request.display_order,
    )

    uptime_30d = await store.calculate_uptime(component.id, days=30)
    uptime_90d = await store.calculate_uptime(component.id, days=90)

    return ComponentResponse(
        **component.to_dict(),
        uptime_30d=uptime_30d,
        uptime_90d=uptime_90d,
    )


@router.patch("/components/{component_id}", response_model=ComponentResponse)
async def update_component(
    component_id: str,
    request: ComponentUpdateRequest,
) -> ComponentResponse:
    """Update a component (admin only)."""
    store = get_status_store()

    # Validate status if provided
    if request.status:
        try:
            ComponentStatus(request.status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {request.status}")

    updates = request.model_dump(exclude_none=True)
    component = await store.update_component(component_id, **updates)

    if not component:
        raise HTTPException(404, f"Component not found: {component_id}")

    # If status changed to non-operational, notify subscribers
    if request.status and request.status != "operational":
        from app.status_page.notifications import NotificationPayload
        notifier = get_status_notifier()
        subscribers = await store.list_subscribers(component_id=component_id)
        if subscribers:
            payload = NotificationPayload(
                event_type="component_status_change",
                title=f"Component Status Change: {component.name}",
                message=f"{component.name} status changed to {request.status}",
                # ComponentStatus.DEGRADED is "degraded"; this read
                # "degraded_performance", which no status ever equals, so a
                # component dropping to degraded notified subscribers as
                # critical.
                severity="warning" if request.status == ComponentStatus.DEGRADED.value else "critical",
                affected_components=[component.name],
            )
            await notifier.notify_subscribers(subscribers, payload)

    uptime_30d = await store.calculate_uptime(component.id, days=30)
    uptime_90d = await store.calculate_uptime(component.id, days=90)

    return ComponentResponse(
        **component.to_dict(),
        uptime_30d=uptime_30d,
        uptime_90d=uptime_90d,
    )


@router.delete("/components/{component_id}", status_code=204)
async def delete_component(component_id: str) -> None:
    """Delete a component (admin only)."""
    store = get_status_store()

    if not await store.delete_component(component_id):
        raise HTTPException(404, f"Component not found: {component_id}")


@router.post("/incidents", response_model=IncidentResponse, status_code=201)
async def create_incident(request: IncidentCreateRequest) -> IncidentResponse:
    """Create a new incident (admin only)."""
    store = get_status_store()

    # Validate severity
    try:
        severity = IncidentSeverity(request.severity)
    except ValueError:
        raise HTTPException(400, f"Invalid severity: {request.severity}")

    # Validate component IDs
    for comp_id in request.affected_components:
        comp = await store.get_component(comp_id)
        if not comp:
            raise HTTPException(400, f"Invalid component ID: {comp_id}")

    incident = await store.create_incident(
        title=request.title,
        message=request.message,
        severity=severity,
        affected_components=request.affected_components,
    )

    # Notify subscribers
    notifier = get_status_notifier()
    subscribers = await store.list_subscribers()
    await notifier.notify_incident_created(subscribers, incident)

    return IncidentResponse(**incident.to_dict())


@router.patch("/incidents/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: str,
    request: IncidentUpdateRequest,
) -> IncidentResponse:
    """Update an incident (admin only)."""
    store = get_status_store()

    # Validate status if provided
    status = None
    if request.status:
        try:
            status = IncidentStatus(request.status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {request.status}")

    # Handle resolution
    if status == IncidentStatus.RESOLVED:
        incident = await store.resolve_incident(
            incident_id,
            message=request.message or "",
        )
    else:
        incident = await store.update_incident(
            incident_id,
            status=status,
            message=request.message,
        )

    if not incident:
        raise HTTPException(404, f"Incident not found: {incident_id}")

    # Notify subscribers
    notifier = get_status_notifier()
    subscribers = await store.list_subscribers()

    if status == IncidentStatus.RESOLVED:
        await notifier.notify_incident_resolved(subscribers, incident, request.message or "")
    elif request.message:
        await notifier.notify_incident_updated(subscribers, incident, request.message)

    return IncidentResponse(**incident.to_dict())


@router.post("/maintenances", response_model=MaintenanceResponse, status_code=201)
async def create_maintenance(request: MaintenanceCreateRequest) -> MaintenanceResponse:
    """Create a scheduled maintenance window (admin only)."""
    store = get_status_store()

    # Validate times
    if request.scheduled_end <= request.scheduled_start:
        raise HTTPException(400, "scheduled_end must be after scheduled_start")

    # Validate component IDs
    for comp_id in request.affected_components:
        comp = await store.get_component(comp_id)
        if not comp:
            raise HTTPException(400, f"Invalid component ID: {comp_id}")

    maintenance = await store.create_maintenance(
        title=request.title,
        description=request.description,
        scheduled_start=request.scheduled_start,
        scheduled_end=request.scheduled_end,
        affected_components=request.affected_components,
    )

    # Notify subscribers
    notifier = get_status_notifier()
    subscribers = await store.list_subscribers()
    await notifier.notify_maintenance_scheduled(subscribers, maintenance)

    return MaintenanceResponse(**maintenance.to_dict())


@router.patch("/maintenances/{maintenance_id}", response_model=MaintenanceResponse)
async def update_maintenance(
    maintenance_id: str,
    request: MaintenanceUpdateRequest,
) -> MaintenanceResponse:
    """Update a maintenance window (admin only)."""
    store = get_status_store()

    # Handle status transitions
    if request.status:
        try:
            status = MaintenanceStatus(request.status)
        except ValueError:
            raise HTTPException(400, f"Invalid status: {request.status}")

        if status == MaintenanceStatus.IN_PROGRESS:
            maintenance = await store.start_maintenance(maintenance_id)
            if maintenance:
                notifier = get_status_notifier()
                subscribers = await store.list_subscribers()
                await notifier.notify_maintenance_started(subscribers, maintenance)
        elif status == MaintenanceStatus.COMPLETED:
            maintenance = await store.complete_maintenance(maintenance_id)
            if maintenance:
                notifier = get_status_notifier()
                subscribers = await store.list_subscribers()
                await notifier.notify_maintenance_completed(subscribers, maintenance)
        else:
            updates = request.model_dump(exclude_none=True)
            maintenance = await store.update_maintenance(maintenance_id, **updates)
    else:
        updates = request.model_dump(exclude_none=True)
        maintenance = await store.update_maintenance(maintenance_id, **updates)

    if not maintenance:
        raise HTTPException(404, f"Maintenance not found: {maintenance_id}")

    return MaintenanceResponse(**maintenance.to_dict())


@router.delete("/maintenances/{maintenance_id}", status_code=204)
async def cancel_maintenance(maintenance_id: str) -> None:
    """Cancel a maintenance window (admin only)."""
    store = get_status_store()

    maintenance = await store.get_maintenance(maintenance_id)
    if not maintenance:
        raise HTTPException(404, f"Maintenance not found: {maintenance_id}")

    await store.update_maintenance(maintenance_id, status=MaintenanceStatus.CANCELLED)
