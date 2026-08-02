"""API Router for Verification Agent.

Exposes REST API for claim verification and action safety checks.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import get_settings
from app.verification.evidence_collector import EvidenceSource
from app.verification.verification_agent import (
    Claim,
    VerificationAgent,
    VerificationStatus,
    create_verification_agent,
)

log = logging.getLogger("devcopilot.verification.api")

router = APIRouter(prefix="/verification", tags=["verification"])

_agent: VerificationAgent | None = None


async def get_agent() -> VerificationAgent:
    """Get or create verification agent."""
    global _agent

    if _agent is None:
        settings = get_settings()

        _agent = create_verification_agent(
            prometheus_url=getattr(settings, "prometheus_url", None),
            loki_url=getattr(settings, "loki_url", None),
            elasticsearch_url=getattr(settings, "elasticsearch_url", None),
            k8s_client=None,
            github_token=getattr(settings, "github_token", None),
            knowledge_graph=None,
            redis_client=None,
            llm_client=None,
        )

    return _agent


class ClaimInput(BaseModel):
    """Input for verifying a claim."""
    text: str
    claim_type: str = "general"
    service_id: str | None = None
    incident_id: str | None = None
    stated_confidence: float | None = None
    cited_sources: list[str] = Field(default_factory=list)
    cited_values: dict[str, Any] = Field(default_factory=dict)
    time_window_minutes: int = 60


class HypothesisInput(BaseModel):
    """Input for verifying a hypothesis."""
    hypothesis: str
    service_id: str
    incident_id: str | None = None
    confidence: float = 0.5


class ActionCheckInput(BaseModel):
    """Input for checking action safety."""
    action_type: str
    target: str
    params: dict[str, Any] = Field(default_factory=dict)


class OutcomeInput(BaseModel):
    """Input for recording verification outcome."""
    claim_id: str
    was_correct: bool


@router.post("/claims/verify")
async def verify_claim(
    input: ClaimInput,
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Verify a claim with evidence checking."""
    import uuid

    claim = Claim(
        id=str(uuid.uuid4())[:8],
        text=input.text,
        claim_type=input.claim_type,
        service_id=input.service_id,
        incident_id=input.incident_id,
        stated_confidence=input.stated_confidence,
        cited_sources=input.cited_sources,
        cited_values=input.cited_values,
    )

    result = await agent.verify_claim(
        claim,
        time_window_minutes=input.time_window_minutes,
    )

    return result.to_dict()


@router.post("/hypotheses/verify")
async def verify_hypothesis(
    input: HypothesisInput,
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Verify a root cause hypothesis."""
    result = await agent.verify_hypothesis(
        hypothesis=input.hypothesis,
        service_id=input.service_id,
        incident_id=input.incident_id,
        confidence=input.confidence,
    )

    return result.to_dict()


@router.post("/claims/verify-batch")
async def verify_claims_batch(
    claims: list[ClaimInput],
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Verify multiple claims in parallel."""
    import uuid

    claim_objects = [
        Claim(
            id=str(uuid.uuid4())[:8],
            text=input.text,
            claim_type=input.claim_type,
            service_id=input.service_id,
            incident_id=input.incident_id,
            stated_confidence=input.stated_confidence,
            cited_sources=input.cited_sources,
            cited_values=input.cited_values,
        )
        for input in claims
    ]

    results = await agent.verify_multiple_claims(claim_objects)

    return {
        "results": [r.to_dict() for r in results],
        "total": len(results),
        "verified": sum(1 for r in results if r.status == VerificationStatus.VERIFIED),
        "refuted": sum(1 for r in results if r.status == VerificationStatus.REFUTED),
        "uncertain": sum(1 for r in results if r.status == VerificationStatus.UNCERTAIN),
    }


@router.post("/actions/check")
async def check_action_safety(
    input: ActionCheckInput,
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Check if an action is safe to execute."""
    result = await agent.verify_action(
        action_type=input.action_type,
        target=input.target,
        params=input.params,
    )

    return result.to_dict()


@router.post("/actions/{action_id}/validate")
async def validate_action_result(
    action_id: str,
    timeout_seconds: int = 60,
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Validate that an action completed successfully."""
    return {
        "action_id": action_id,
        "validation": "pending",
        "message": "Post-action validation requires stored action check state",
    }


@router.post("/outcomes")
async def record_outcome(
    input: OutcomeInput,
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Record the actual outcome of a verified claim."""
    await agent.record_verification_outcome(
        claim_id=input.claim_id,
        was_correct=input.was_correct,
    )

    return {
        "status": "recorded",
        "claim_id": input.claim_id,
        "was_correct": input.was_correct,
    }


@router.get("/calibration")
async def get_calibration_stats(
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Get calibration statistics."""
    return agent.get_calibration_stats()


@router.get("/evidence-sources")
async def list_evidence_sources() -> dict[str, Any]:
    """List available evidence sources."""
    return {
        "sources": [
            {
                "name": source.value,
                "description": _get_source_description(source),
            }
            for source in EvidenceSource
        ],
    }


@router.delete("/cache")
async def clear_cache(
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Clear the verification cache."""
    agent.clear_cache()
    return {"status": "cleared"}


def _get_source_description(source: EvidenceSource) -> str:
    """Get description for an evidence source."""
    descriptions = {
        EvidenceSource.METRICS: "Prometheus/metrics data",
        EvidenceSource.LOGS: "Loki/Elasticsearch logs",
        EvidenceSource.GIT: "Git/GitHub history",
        EvidenceSource.KUBERNETES: "Kubernetes cluster state",
        EvidenceSource.KNOWLEDGE_GRAPH: "Knowledge graph correlations",
        EvidenceSource.TRACES: "Distributed tracing data",
        EvidenceSource.EXTERNAL: "External service status",
    }
    return descriptions.get(source, "Unknown source")
