"""Incidents API Router."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.billing.feature_gate import require_feature
from app.incidents.models import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    CreateIncidentRequest,
    UpdateIncidentRequest,
    IncidentResponse,
    IncidentDetailResponse,
)
from app.incidents.store import get_incident_store
from app.postmortems import (
    SECTION_IDS,
    PostmortemGenerationFailed,
    PostmortemUnavailable,
    generate_postmortem,
)
from app.tenant_context import get_actor, tenant_id

log = logging.getLogger("omnipotent.incidents.api")

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _acting_user(user: str | None) -> str:
    """Who to attribute an acknowledge/resolve to.

    `user` used to be a required query parameter, which nothing in the UI sent —
    every Acknowledge/Resolve click was a 422 the page swallowed. It is optional
    now and falls back to the principal the request authenticated as. When the
    deployment has no identity at all (single-tenant with auth disabled) the
    entry says so rather than inventing a name.
    """
    if user and user.strip():
        return user.strip()
    actor = get_actor()
    return actor if actor and actor != "-" else "unknown"


def _to_response(incident: Incident) -> IncidentResponse:
    return IncidentResponse(
        id=incident.id,
        title=incident.title,
        description=incident.description,
        severity=incident.severity,
        status=incident.status,
        service=incident.service,
        triggered_at=incident.triggered_at,
        acknowledged_at=incident.acknowledged_at,
        resolved_at=incident.resolved_at,
        assignee=incident.assignee,
        tags=incident.tags,
        has_investigation=incident.investigation is not None,
        investigation_confidence=incident.investigation.overall_confidence
        if incident.investigation
        else None,
    )


@router.get("", response_model=list[IncidentResponse])
async def list_incidents(
    status: IncidentStatus | None = None,
    severity: IncidentSeverity | None = None,
    service: str | None = None,
    limit: int = Query(default=50, le=200),
) -> list[IncidentResponse]:
    """List incidents with optional filters."""
    store = get_incident_store()
    incidents = await store.list_incidents(
        status=status,
        severity=severity,
        service=service,
        limit=limit,
    )
    return [_to_response(i) for i in incidents]


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
async def get_incident(incident_id: UUID) -> IncidentDetailResponse:
    """Get incident details including investigation."""
    store = get_incident_store()
    incident = await store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentDetailResponse(
        incident=incident,
        investigation=incident.investigation,
    )


@router.post("", response_model=IncidentResponse)
async def create_incident(request: CreateIncidentRequest) -> IncidentResponse:
    """Create a new incident."""
    store = get_incident_store()
    incident = await store.create_incident(
        title=request.title,
        description=request.description,
        severity=request.severity,
        service=request.service,
        tags=request.tags,
        impact=request.impact,
        customer_impact=request.customer_impact,
    )
    log.info("Created incident %s: %s", incident.id, incident.title)
    return _to_response(incident)


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: UUID,
    request: UpdateIncidentRequest,
) -> IncidentResponse:
    """Update incident fields."""
    store = get_incident_store()
    updates = request.model_dump(exclude_none=True)
    incident = await store.update_incident(incident_id, updates)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Updated incident %s", incident_id)
    return _to_response(incident)


@router.post("/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: UUID,
    user: str | None = Query(default=None),
) -> dict:
    """Acknowledge an incident, as `user` or as the authenticated principal."""
    store = get_incident_store()
    actor = _acting_user(user)
    incident = await store.acknowledge_incident(incident_id, actor)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Incident %s acknowledged by %s", incident_id, actor)
    return {"id": incident.id, "status": incident.status, "acknowledged_by": actor}


@router.post("/{incident_id}/resolve")
async def resolve_incident(
    incident_id: UUID,
    user: str | None = Query(default=None),
    notes: str | None = None,
) -> dict:
    """Resolve an incident, as `user` or as the authenticated principal."""
    store = get_incident_store()
    actor = _acting_user(user)
    incident = await store.resolve_incident(incident_id, actor, notes)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Incident %s resolved by %s", incident_id, actor)
    return {"id": incident.id, "status": incident.status, "resolved_by": actor}


@router.post(
    "/{incident_id}/investigate",
    summary="Investigate an incident with the agent",
    description=(
        "Queues the same agent run the alert webhooks and the SLO poller use: the "
        "graph investigates the incident from logs, metrics, code and recent changes, "
        "and its verdict is written back onto the incident (root cause, recommended "
        "actions, confidence) — poll `GET /api/incidents/{id}/investigation` for it. "
        "503 when no LLM provider key is configured; nothing is recorded in that case, "
        "because an investigation row that cannot run is a lie."
    ),
)
async def start_investigation(incident_id: UUID) -> dict:
    """Queue the real agent investigation for an incident.

    This used to write a row holding six `idle` agents and a timeline entry
    claiming a "6-agent parallel investigation initiated", then return. Nothing
    ran: no task was scheduled, no LLM was called, and the six agents did not
    exist anywhere in the codebase. It now goes onto the job queue that actually
    drives the agent graph.
    """
    from app.llm import active_api_key

    store = get_incident_store()
    incident = await store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if incident.investigation is not None:
        # One investigation per incident (the FK is unique). Report the existing
        # one rather than queueing a duplicate run against the same incident.
        existing = incident.investigation
        return {
            "investigation_id": existing.id,
            "incident_id": incident_id,
            "status": "completed" if existing.completed_at else "running",
            "root_cause": existing.root_cause,
            "recommended_actions": existing.recommended_actions,
            "confidence": existing.overall_confidence,
        }

    if not active_api_key():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "llm_not_configured",
                "message": (
                    "No LLM provider key is configured, so no investigation can run. "
                    "Set the API key for COPILOT_PROVIDER (e.g. ANTHROPIC_API_KEY)."
                ),
                "missing_configuration": ["COPILOT_PROVIDER API key"],
            },
        )

    investigation = await store.start_investigation(incident_id)
    if not investigation:  # deleted between the read and the write
        raise HTTPException(status_code=404, detail="Incident not found")

    from app.api.main import _enqueue_investigation

    await _enqueue_investigation(
        {
            "id": str(incident_id),
            "source": "incident",
            "title": incident.title,
            "service": incident.service,
            "incident_id": str(incident_id),
            "org": tenant_id(),
        }
    )
    log.info("Queued investigation %s for incident %s", investigation.id, incident_id)
    return {
        "investigation_id": investigation.id,
        "incident_id": incident_id,
        "status": "running",
    }


@router.get("/{incident_id}/investigation")
async def get_investigation(incident_id: UUID) -> dict:
    """Get investigation status for an incident."""
    store = get_incident_store()
    incident = await store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident.investigation:
        raise HTTPException(status_code=404, detail="No investigation for this incident")

    inv = incident.investigation
    return {
        "id": inv.id,
        "incident_id": inv.incident_id,
        "status": "completed" if inv.completed_at else "running",
        "started_at": inv.started_at,
        "completed_at": inv.completed_at,
        "overall_confidence": inv.overall_confidence,
        "root_cause": inv.root_cause,
        "recommended_actions": inv.recommended_actions,
        "agents": {
            agent_type: {
                "status": agent.status.value,
                "progress": agent.progress,
                "current_action": agent.current_action,
                "findings": [
                    {
                        "finding": f.finding,
                        "confidence": f.confidence,
                        "timestamp": f.timestamp,
                    }
                    for f in agent.findings
                ],
            }
            for agent_type, agent in inv.agents.items()
        },
        "safety_gates": [
            {
                "gate_type": g.gate_type,
                "status": g.status.value,
                "threshold": g.threshold,
                "current_value": g.current_value,
                "message": g.message,
            }
            for g in inv.safety_gates
        ],
    }


class PostmortemRequest(BaseModel):
    """Which sections to (re)generate. Empty means the whole document."""

    sections: list[str] = Field(default_factory=list)


@router.post(
    "/{incident_id}/postmortem",
    summary="Generate a postmortem for an incident",
    description=(
        "Builds the postmortem from the stored incident: the timeline, durations "
        "and affected service are read off the record, and the prose sections and "
        "action items are written by the configured LLM from exactly those facts. "
        "Pass `sections` to regenerate a subset. 503 when no LLM key is configured "
        "— there is no template fallback, because a canned paragraph presented as "
        "analysis is worse than no paragraph. 402 when the org's plan does not "
        "include AI postmortems (multi-tenant deployments only)."
    ),
    # `ai_postmortem` is Team-and-up in FEATURE_TO_MIN_TIER and the pricing page
    # sells it that way; until this dependency existed nothing on the server
    # enforced any of it. No-op in single-tenant mode.
    dependencies=[Depends(require_feature("ai_postmortem"))],
)
async def create_postmortem(incident_id: UUID, request: PostmortemRequest | None = None) -> dict:
    store = get_incident_store()
    incident = await store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    sections = tuple(request.sections) if request and request.sections else None
    if sections:
        unknown = [s for s in sections if s not in SECTION_IDS]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"unknown section(s): {', '.join(unknown)}; "
                    f"supported: {', '.join(SECTION_IDS)}"
                ),
            )
    try:
        return await generate_postmortem(incident, sections)
    except PostmortemUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "llm_not_configured",
                "message": str(exc),
                "missing_configuration": exc.missing,
            },
        ) from exc
    except PostmortemGenerationFailed as exc:
        log.warning("postmortem generation failed for %s: %s", incident_id, exc)
        raise HTTPException(
            status_code=502,
            detail={"error": "generation_failed", "message": str(exc)},
        ) from exc


@router.get("/{incident_id}/timeline")
async def get_timeline(incident_id: UUID) -> list[dict]:
    """Get incident timeline."""
    store = get_incident_store()
    incident = await store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return [
        {
            "timestamp": entry.timestamp,
            "type": entry.type,
            "message": entry.message,
            "user": entry.user,
        }
        for entry in incident.timeline
    ]
