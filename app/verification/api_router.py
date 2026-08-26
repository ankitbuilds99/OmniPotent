"""API Router for Verification Agent.

Exposes REST API for claim verification and action safety checks.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.verification.evidence_collector import EvidenceSource
from app.verification.verification_agent import (
    Claim,
    NoEvidenceSourcesConfigured,
    VerificationAgent,
    VerificationStatus,
    create_verification_agent,
)
from app.tenancy.scoping import scoped

log = logging.getLogger("omnipotent.verification.api")

router = APIRouter(prefix="/verification", tags=["verification"])


def _calibration_redis() -> Any:
    """A real Redis client for calibration history, or None if not configured.

    None means calibration counts live in process memory only; the endpoint says
    so rather than implying the numbers are durable. A stub client is never
    substituted - "the data was saved" has to be true or reported as false.
    """
    url = (get_settings().copilot_redis_url or "").strip()
    if not url:
        return None
    try:
        import redis.asyncio as redis_asyncio
    except Exception as e:  # pragma: no cover - redis is a hard dependency
        log.warning("COPILOT_REDIS_URL is set but redis is not importable: %s", e)
        return None
    try:
        return redis_asyncio.from_url(url)
    except Exception as e:
        log.warning("COPILOT_REDIS_URL is set but a client could not be built: %s", e)
        return None


def _build_agent() -> VerificationAgent:
    settings = get_settings()
    return create_verification_agent(
        prometheus_url=(settings.prometheus_url or "").strip() or None,
        loki_url=(settings.loki_url or "").strip() or None,
        elasticsearch_url=(settings.elasticsearch_url or "").strip() or None,
        # No Kubernetes client is wired up anywhere in this process. Passing
        # None is the honest value: the consistency checker then reports that
        # preconditions could not be verified instead of inventing cluster state.
        k8s_client=None,
        github_token=(settings.github_token or "").strip() or None,
        github_repo=(settings.github_repo or "").strip() or None,
        knowledge_graph=None,
        redis_client=_calibration_redis(),
        llm_client=None,
    )


def _no_sources_detail(exc: NoEvidenceSourcesConfigured) -> dict[str, Any]:
    return {
        "error": "no_evidence_sources_configured",
        "message": str(exc),
        "missing_configuration": exc.missing,
        "hint": (
            "Verification compares a claim against real telemetry. With no "
            "backend reachable there is nothing to compare it to, so no verdict "
            "is returned."
        ),
    }


# One agent per org. It caches collected evidence and calibration history keyed
# by claim, so a shared instance served one tenant's metric and log excerpts as
# the evidence behind another tenant's verification.
_agents = scoped(_build_agent)


async def get_agent() -> VerificationAgent:
    """The verification agent for the org on the current request."""
    return _agents.get()


def _require_sources(agent: VerificationAgent) -> None:
    """503 unless at least one evidence backend is actually reachable.

    Without one, every claim collects zero evidence and the agent's verdict
    describes the deployment's configuration, not the claim. It previously
    answered 200 with "insufficient_data" - or, for any claim containing a
    number, "refuted / fabricated_fact" - which is a confident falsehood.
    """
    try:
        agent._require_evidence_sources()
    except NoEvidenceSourcesConfigured as exc:
        raise HTTPException(status_code=503, detail=_no_sources_detail(exc)) from exc


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
    _require_sources(agent)

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
    _require_sources(agent)

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
    _require_sources(agent)

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
    """Validate that an action completed successfully.

    `action_id` must come from a previous POST /actions/check: that check is
    what recorded the expected post-action state this compares against. An id
    that was never checked is a 404, not a `{"validation": "pending"}` 200.
    """
    if timeout_seconds < 0 or timeout_seconds > 600:
        raise HTTPException(
            status_code=422,
            detail="timeout_seconds must be between 0 and 600",
        )

    try:
        validation = await agent.validate_action_by_id(action_id, timeout_seconds)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_action_id",
                "message": (
                    f"No pre-action safety check is on record for '{action_id}'. "
                    "POST /api/verification/actions/check first and use the "
                    "action_id it returns."
                ),
            },
        ) from exc

    return validation.to_dict()


@router.post("/outcomes")
async def record_outcome(
    input: OutcomeInput,
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """Record the actual outcome of a verified claim."""
    recorded = await agent.record_verification_outcome(
        claim_id=input.claim_id,
        was_correct=input.was_correct,
    )

    if not recorded:
        # Answering "recorded" for a claim this process never verified is how
        # the calibration endpoint ended up with zero samples while every write
        # reported success.
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_claim_id",
                "message": (
                    f"No verification is on record for claim '{input.claim_id}', "
                    "so its outcome was not recorded. Outcomes can only be "
                    "recorded for a claim this worker verified (results are held "
                    "in process memory)."
                ),
            },
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
    """Get calibration statistics.

    `brier_score` and `ece` are null and every reliability bin reports
    `accuracy: null` until real outcomes have been recorded via POST /outcomes.
    """
    await agent.calibration.ensure_loaded()
    return agent.get_calibration_stats()


@router.get("/evidence-sources")
async def list_evidence_sources(
    agent: VerificationAgent = Depends(get_agent),
) -> dict[str, Any]:
    """List evidence sources and say which ones this deployment can reach.

    The list used to enumerate the enum unconditionally, which read as "all
    seven of these are available" on a deployment that had none of them.
    """
    configured = set(agent.evidence_sources_available())
    return {
        "sources": [
            {
                "name": source.value,
                "description": _get_source_description(source),
                "configured": source in configured,
            }
            for source in EvidenceSource
        ],
        "configured_count": len(configured),
        "missing_configuration": (
            agent.evidence.missing_source_config() if not configured else {}
        ),
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
