"""Incidents API Router."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.incidents.models import (
    Incident,
    IncidentSeverity,
    IncidentStatus,
    CreateIncidentRequest,
    UpdateIncidentRequest,
    IncidentResponse,
    IncidentDetailResponse,
    AgentStatus,
    AgentFinding,
)
from app.incidents.store import get_incident_store

log = logging.getLogger("devcopilot.incidents.api")

router = APIRouter(prefix="/incidents", tags=["incidents"])


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
    incidents = store.list_incidents(
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
    incident = store.get_incident(incident_id)
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
    incident = store.create_incident(
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
    incident = store.update_incident(incident_id, updates)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Updated incident %s", incident_id)
    return _to_response(incident)


@router.post("/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: UUID,
    user: str = Query(...),
) -> dict:
    """Acknowledge an incident."""
    store = get_incident_store()
    incident = store.acknowledge_incident(incident_id, user)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Incident %s acknowledged by %s", incident_id, user)
    return {"id": incident.id, "status": incident.status}


@router.post("/{incident_id}/resolve")
async def resolve_incident(
    incident_id: UUID,
    user: str = Query(...),
    notes: str | None = None,
) -> dict:
    """Resolve an incident."""
    store = get_incident_store()
    incident = store.resolve_incident(incident_id, user, notes)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Incident %s resolved by %s", incident_id, user)
    return {"id": incident.id, "status": incident.status}


@router.post("/{incident_id}/investigate")
async def start_investigation(incident_id: UUID) -> dict:
    """Start AI investigation for an incident."""
    store = get_incident_store()
    investigation = store.start_investigation(incident_id)
    if not investigation:
        raise HTTPException(status_code=404, detail="Incident not found")
    log.info("Started investigation %s for incident %s", investigation.id, incident_id)
    return {
        "investigation_id": investigation.id,
        "incident_id": incident_id,
        "agents": list(investigation.agents.keys()),
    }


@router.get("/{incident_id}/investigation")
async def get_investigation(incident_id: UUID) -> dict:
    """Get investigation status for an incident."""
    store = get_incident_store()
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident.investigation:
        raise HTTPException(status_code=404, detail="No investigation for this incident")

    inv = incident.investigation
    return {
        "id": inv.id,
        "incident_id": inv.incident_id,
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


@router.get("/{incident_id}/timeline")
async def get_timeline(incident_id: UUID) -> list[dict]:
    """Get incident timeline."""
    store = get_incident_store()
    incident = store.get_incident(incident_id)
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
