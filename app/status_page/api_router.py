"""Status Page API Router.

Public and admin endpoints for the status page system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, EmailStr

from app.status_page.models import (
    ComponentStatus,
    IncidentSeverity,
    IncidentStatus,
    MaintenanceStatus,
    SubscriptionType,
)
from app.status_page.notifications import get_status_notifier
from app.status_page.store import get_status_store, get_live_status_store, LiveStatusStore

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


@router.post("/refresh", response_model=dict)
async def refresh_status() -> dict:
    """Manually trigger a health check refresh.

    Forces an immediate sync of component status from real health checks.
    Only works when using LiveStatusStore.
    """
    live_store = get_live_status_store()
    if live_store is None:
        raise HTTPException(400, "Live status store not initialized")

    await live_store.refresh()
    return {"status": "refreshed", "message": "Health checks synced successfully"}


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


@router.post("/subscribe", response_model=SubscribeResponse, status_code=201)
async def subscribe_to_updates(request: SubscribeRequest) -> SubscribeResponse:
    """Subscribe to status page updates.

    Provide either an email address or a webhook URL. For email subscriptions,
    a verification email will be sent. For webhooks, a verification POST will
    be sent with a challenge parameter.
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
        return SubscribeResponse(
            id=existing.id,
            subscription_type=existing.subscription_type.value,
            target=existing._mask_target(),
            components=existing.components,
            verified=existing.verified,
            created_at=existing.created_at,
            message="Already subscribed. Check your email for verification if not yet verified.",
        )

    # Validate component IDs if provided
    if request.components:
        for comp_id in request.components:
            comp = await store.get_component(comp_id)
            if not comp:
                raise HTTPException(400, f"Invalid component ID: {comp_id}")

    # Create subscription
    subscriber = await store.create_subscriber(
        subscription_type=subscription_type,
        target=target,
        components=request.components,
    )

    # Update preferences
    subscriber.notify_on_incidents = request.notify_on_incidents
    subscriber.notify_on_maintenance = request.notify_on_maintenance
    subscriber.notify_on_resolved = request.notify_on_resolved

    # TODO: Send verification email/webhook
    # For now, auto-verify webhooks and mark emails as pending
    if subscription_type == SubscriptionType.WEBHOOK:
        subscriber.verified = True
        message = "Webhook subscription created and verified."
    else:
        message = "Subscription created. Please check your email to verify."

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
                severity="warning" if request.status == "degraded_performance" else "critical",
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
