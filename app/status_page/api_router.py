"""Status Page API Router.

Public and admin endpoints for the status page system.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field, EmailStr
from starlette.requests import HTTPConnection

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
from app.status_page.store import get_live_status_store, get_status_store
from app.url_guard import BlockedURL, assert_public_url

log = logging.getLogger("omnipotent.status_page.api")

router = APIRouter(prefix="/api/status", tags=["Status Page"])

# The route the emailed verification link points at. Named once because both the
# link builder and the check that the link is reachable resolve it by name.
_VERIFY_ROUTE_NAME = "verify_subscription"

# What a subscriber is told when the verification link cannot be opened. The
# subscribe form is anonymous and app/api/main.py's auth gate no longer stands in
# front of it, so this string is read by strangers; frontend/src/pages/StatusPage.tsx
# renders `detail` verbatim into the form. It therefore says what happened to the
# address and nothing about how this deployment is built — the diagnosis, which
# names internal paths and symbols, goes to the server log for the operator.
_EMAIL_SUBSCRIPTIONS_UNAVAILABLE = (
    "Email subscriptions are temporarily unavailable on this deployment. Your "
    "address was not stored and nothing was sent to it. The reason has been "
    "recorded in the server log; please try again later."
)


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

    Forces an immediate sync of component status from real health checks,
    rather than waiting for the next poll.
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


def _verification_link(http_request: Request, subscriber_id: str, token: str) -> str:
    """Where the verification link in the email points.

    `status_page_url` when an operator has set one, in preference to the host
    the request arrived on: the Host header is client-controlled, and this link
    carries a bearer token into somebody's inbox. Unset — the default — it falls
    back to the request's own base URL, which is correct for a single-origin
    deployment and is at worst the host the subscriber just used themselves.
    """
    query = urlencode({"id": subscriber_id, "token": token})
    path = router.url_path_for(_VERIFY_ROUTE_NAME)
    configured = (get_settings().status_page_url or "").strip().rstrip("/")
    base = configured or str(http_request.base_url).rstrip("/")
    return f"{base}{path}?{query}"


def _verification_link_is_served_here(http_request: Request) -> bool:
    """Whether the link we are about to mail is answered by *this* app.

    `_verification_link` prefers `status_page_url` when an operator has set one,
    so a deployment can serve its status page from a separate public host or from
    behind an edge that injects a credential. There the local auth gate says
    nothing about whether the emailed link opens, and probing it would refuse a
    subscription loop that works. Only a link whose origin is this app's own is
    the local gate's business; an origin we cannot parse counts as elsewhere,
    because refusing on a guess is the failure mode being avoided.
    """
    configured = (get_settings().status_page_url or "").strip()
    if not configured:
        return True
    there = urlsplit(configured)
    here = urlsplit(str(http_request.base_url))
    return bool(there.netloc) and (there.scheme, there.netloc) == (here.scheme, here.netloc)


async def _verification_link_rejected(http_request: Request) -> str | None:
    """Ask the app's own auth gate whether the emailed link can be opened at all.

    The verification link is opened from a mail client, and a mail client sends
    no Authorization header — the single-use random token in the query string is
    the only credential that route has or needs. But this router is mounted on an
    app that gates *every* route with `require_auth`, whose exemption list lives
    with the app rather than with the router, and a router cannot opt one of its
    own paths out of it. So on any deployment that had a credential configured —
    which production always does — the emailed URL was answered 401 before
    `verify_subscription` ever ran. The subscriber stayed unverified, and
    `notify_subscribers` skips unverified subscribers, so the address was kept
    forever and told about nothing while the response said "check your email".

    app/api/main.py now exempts the anonymous half of this router, so on a stock
    deployment this returns None and the subscribe/verify loop runs. The probe
    stays because the exemption lives in another file and is one edit away from
    being lost again: rather than trusting it, this runs the real gate against a
    real credential-less GET of the real path and reports what actually happened.

    Returns an operator-facing diagnosis (for the log, not the response) only when
    the gate *refused* the anonymous click, i.e. raised 401/403. Any other outcome
    — the gate erroring on the synthetic connection, or refusing for a transient
    reason — is logged and treated as "unmeasured": a probe that could not reach a
    verdict must not be the thing that kills email subscriptions.
    """
    gate = getattr(sys.modules.get("app.api.main"), "require_auth", None)
    if gate is None:
        return None
    # Only relevant when that gate is actually installed on the app serving this
    # request; embedded/standalone mounts of this router are not affected by it.
    router_deps = getattr(getattr(http_request.app, "router", None), "dependencies", [])
    if not any(getattr(dep, "dependency", None) is gate for dep in router_deps):
        return None
    if not _verification_link_is_served_here(http_request):
        return None

    path = router.url_path_for(_VERIFY_ROUTE_NAME)
    # ASGI's own type for a scope: the values are deliberately heterogeneous
    # (str, bytes, tuple, None, list), so without this the inferred value type
    # collapses to something no `HTTPConnection` will accept.
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": http_request.url.scheme,
        "server": (http_request.url.hostname or "localhost", http_request.url.port or 80),
        "query_string": b"id=probe&token=probe",
        "headers": [],
        "client": None,
    }
    try:
        await gate(HTTPConnection(scope))
    except HTTPException as refusal:
        if refusal.status_code not in (401, 403):
            log.warning(
                "verification-link reachability probe of %s was inconclusive: the auth "
                "gate raised %s (%s); accepting the subscription anyway",
                path,
                refusal.status_code,
                refusal.detail,
            )
            return None
        return (
            f"an anonymous GET of {path} — which is what a mail client does with the "
            f"link — is refused by this deployment's API authentication gate "
            f"({refusal.status_code}: {refusal.detail}), so the address could never be "
            f"verified. To enable email subscriptions, exempt a GET of {path!r} from "
            "`require_auth` in app/api/main.py (`_is_public_status_page` / "
            "`_PUBLIC_PATHS` keep the per-IP rate limiter on it, `_OPEN_PATHS` does "
            "not); the single-use token in the link is that route's credential."
        )
    except Exception:
        # Not a refusal: the gate itself broke on a synthetic connection. That is a
        # bug in the probe or the gate, not evidence about the mail client's click,
        # so it is reported as itself rather than dressed up as an auth rejection.
        log.warning(
            "verification-link reachability probe of %s could not run; accepting the "
            "subscription anyway",
            path,
            exc_info=True,
        )
        return None
    return None


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
    two things, and each returns 503 when it is missing rather than accepting an
    address that could never be verified: a configured email provider to carry
    the link, and a verification route this deployment's auth gate lets an
    anonymous mail-client click reach. The second 503 keeps its reason out of the
    response — this endpoint is anonymous and its `detail` is rendered to whoever
    submitted the form — and writes the diagnosis to the server log instead.

    A webhook subscription is verified on creation: possession of the URL is the
    only credential a webhook has. The URL itself is checked first — see
    app/url_guard.py for why a subscriber must not be able to point this at the
    inside of the deployment's network.
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
        target = request.webhook_url or ""
        # Anything at all used to be accepted and stored as verified —
        # `file:///etc/passwd`, `not-a-url`, and `http://169.254.169.254/`, which
        # the server then really did POST to on the next incident.
        try:
            await assert_public_url(target, field="webhook_url")
        except BlockedURL as blocked:
            raise HTTPException(400, str(blocked)) from None

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

    # Same reasoning one step further along: a link that is sent but cannot be
    # opened leaves exactly the same dead row behind, so a link the deployment's
    # own auth gate refuses is refused here instead of being mailed. Loud in the
    # log, where the operator who can act on it reads; deliberately vague in the
    # response, which a stranger reads (see _EMAIL_SUBSCRIPTIONS_UNAVAILABLE).
    if not is_webhook:
        unreachable = await _verification_link_rejected(http_request)
        if unreachable:
            log.error("refusing email subscription: %s", unreachable)
            raise HTTPException(503, _EMAIL_SUBSCRIPTIONS_UNAVAILABLE)

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
async def create_incident(
    request: IncidentCreateRequest, background: BackgroundTasks
) -> IncidentResponse:
    """Create a new incident (admin only).

    Subscribers are notified after the response goes out. Awaiting delivery
    inline meant one unreachable subscriber held the request open for the
    webhook timeout plus its retry backoff — measured at ~11 seconds for a
    single dead URL, on the endpoint an operator reaches for mid-outage.
    """
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
    background.add_task(notifier.notify_incident_created, subscribers, incident)

    return IncidentResponse(**incident.to_dict())


@router.patch("/incidents/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: str,
    request: IncidentUpdateRequest,
    background: BackgroundTasks,
) -> IncidentResponse:
    """Update an incident (admin only). Delivery runs after the response —
    see create_incident."""
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
        background.add_task(
            notifier.notify_incident_resolved, subscribers, incident, request.message or ""
        )
    elif request.message:
        background.add_task(
            notifier.notify_incident_updated, subscribers, incident, request.message
        )

    return IncidentResponse(**incident.to_dict())


@router.post("/maintenances", response_model=MaintenanceResponse, status_code=201)
async def create_maintenance(
    request: MaintenanceCreateRequest, background: BackgroundTasks
) -> MaintenanceResponse:
    """Create a scheduled maintenance window (admin only). Delivery runs after
    the response — see create_incident."""
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
    background.add_task(notifier.notify_maintenance_scheduled, subscribers, maintenance)

    return MaintenanceResponse(**maintenance.to_dict())


@router.patch("/maintenances/{maintenance_id}", response_model=MaintenanceResponse)
async def update_maintenance(
    maintenance_id: str,
    request: MaintenanceUpdateRequest,
    background: BackgroundTasks,
) -> MaintenanceResponse:
    """Update a maintenance window (admin only). Delivery runs after the
    response — see create_incident."""
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
                background.add_task(
                    notifier.notify_maintenance_started, subscribers, maintenance
                )
        elif status == MaintenanceStatus.COMPLETED:
            maintenance = await store.complete_maintenance(maintenance_id)
            if maintenance:
                notifier = get_status_notifier()
                subscribers = await store.list_subscribers()
                background.add_task(
                    notifier.notify_maintenance_completed, subscribers, maintenance
                )
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
