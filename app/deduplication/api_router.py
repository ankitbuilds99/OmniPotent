"""Deduplication API Router.

`app/deduplication/` is a complete six-layer deduplication engine that nothing
imported: no router, no worker, no call site anywhere in `app/`. Meanwhile
`advanced_deduplication` — sold from the Growth plan upward and displayed to
customers as "Advanced Noise Reduction (95%)" — matched this package's own promise
of "95%+ noise reduction" and gated nothing at all. This router is the seam that
makes both true: the engine runs, and the plan flags that describe it decide how
deep it runs.

Two design points worth stating, because both are easy to get wrong later:

**Pipelines are per-organization.** `DeduplicationPipeline` holds incidents,
fingerprints and a learned dependency graph in instance state. A single shared
pipeline would correlate one tenant's alerts against another's and hand back the
other tenant's `incident_id` — a cross-tenant data leak, not just a wrong answer.
Each organization therefore gets its own pipeline, keyed by org.

**Plan flags select layers, not access.** Every plan includes
`basic_deduplication`, so exact-fingerprint matching (Layer 1) and policy
suppression (Layer 6) always run — suppression covers maintenance windows and known
issues, which are operator safety controls rather than a paid upsell. The
higher-tier flags each switch on the layer they name in the price list:

    advanced_deduplication  -> Layer 2 semantic + Layer 5 ML clustering
    cascade_detection       -> Layer 3 temporal correlation and cascade detection
    service_dependency_map  -> Layer 4 topology correlation

A Starter customer still gets working deduplication; they get fewer layers of it.
The response reports `layers_enabled` so the caller can see which applied.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.billing.runtime import current_org_id, enabled_features
from app.deduplication.models import AlertSeverity, AlertSource, RawAlert
from app.deduplication.pipeline import DeduplicationPipeline, PipelineConfig

log = logging.getLogger("omnipotent.deduplication.api")

router = APIRouter(prefix="/deduplication", tags=["deduplication"])

# Plan flags this router reads. Kept as a module constant so the tests can assert
# the set is exactly what the layer mapping below consumes.
GATING_FEATURES = [
    "advanced_deduplication",
    "cascade_detection",
    "service_dependency_map",
]

# One pipeline per organization — see the module docstring on why this cannot be a
# single shared instance. Like the other engines in this codebase the state is
# process-local, which is why the deployment is pinned to a single replica; see
# tests/test_single_replica_until_state_externalised.py.
_pipelines: dict[str, DeduplicationPipeline] = {}


def _config_for(features: dict[str, bool]) -> PipelineConfig:
    """Translate resolved plan flags into per-layer pipeline configuration."""
    return PipelineConfig(
        # Included in every plan via basic_deduplication.
        enable_layer1_fingerprint=True,
        enable_layer6_suppression=True,
        enable_layer2_semantic=features.get("advanced_deduplication", False),
        enable_layer5_ml=features.get("advanced_deduplication", False),
        enable_layer3_temporal=features.get("cascade_detection", False),
        enable_layer4_topology=features.get("service_dependency_map", False),
    )


def _layers_enabled(config: PipelineConfig) -> list[str]:
    """Layer names active under `config`, in pipeline order."""
    return [
        name
        for name, on in (
            ("fingerprint", config.enable_layer1_fingerprint),
            ("semantic", config.enable_layer2_semantic),
            ("temporal", config.enable_layer3_temporal),
            ("topology", config.enable_layer4_topology),
            ("ml", config.enable_layer5_ml),
            ("suppression", config.enable_layer6_suppression),
        )
        if on
    ]


async def get_pipeline(org_id: UUID | None = None) -> DeduplicationPipeline:
    """Return this organization's pipeline, creating it on first use.

    The plan is resolved on every call so that an upgrade takes effect without a
    restart: an existing pipeline keeps its accumulated incidents and only has its
    layer configuration refreshed.
    """
    org = org_id if org_id is not None else current_org_id()
    features = await enabled_features(GATING_FEATURES, org)
    config = _config_for(features)

    key = str(org)
    pipeline = _pipelines.get(key)
    if pipeline is None:
        pipeline = DeduplicationPipeline(config)
        _pipelines[key] = pipeline
        log.info(
            "Created deduplication pipeline for org %s with layers: %s",
            org,
            ", ".join(_layers_enabled(config)),
        )
        return pipeline

    # Refresh only the layer switches. Replacing `pipeline.config` wholesale would
    # hand the already-constructed layer engines a different config object than the
    # one they were built from, so only the flags the plan controls are updated.
    pipeline.config.enable_layer2_semantic = config.enable_layer2_semantic
    pipeline.config.enable_layer3_temporal = config.enable_layer3_temporal
    pipeline.config.enable_layer4_topology = config.enable_layer4_topology
    pipeline.config.enable_layer5_ml = config.enable_layer5_ml
    return pipeline


def reset_pipelines() -> None:
    """Drop all per-org pipelines. Used by tests and by process shutdown."""
    _pipelines.clear()


class ProcessAlertRequest(BaseModel):
    """An alert arriving from a monitoring system."""

    alert_name: str
    service: str
    source: str = "custom"
    alert_type: str = "metric"
    environment: str = "production"
    message: str = ""
    severity: str = "medium"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    value: float | None = None
    threshold: float | None = None
    region: str | None = None
    cluster: str | None = None
    namespace: str | None = None
    pod_name: str | None = None
    host: str | None = None
    trace_id: str | None = None


def _to_raw_alert(req: ProcessAlertRequest) -> RawAlert:
    """Build a RawAlert, rejecting unknown enum values with a 422 rather than 500.

    `AlertSource("nagios")` and `AlertSeverity("sev1")` raise ValueError; without
    this the caller would get an opaque 500 for what is a request problem.
    """
    try:
        source = AlertSource(req.source)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown source '{req.source}'. Expected one of: "
                + ", ".join(s.value for s in AlertSource)
            ),
        ) from None

    try:
        severity = AlertSeverity(req.severity)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown severity '{req.severity}'. Expected one of: "
                + ", ".join(s.value for s in AlertSeverity)
            ),
        ) from None

    return RawAlert(
        source=source,
        alert_name=req.alert_name,
        alert_type=req.alert_type,
        service=req.service,
        environment=req.environment,
        message=req.message,
        severity=severity,
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        labels=req.labels,
        annotations=req.annotations,
        value=req.value,
        threshold=req.threshold,
        region=req.region,
        cluster=req.cluster,
        namespace=req.namespace,
        pod_name=req.pod_name,
        host=req.host,
        trace_id=req.trace_id,
    )


@router.post("/process")
async def process_alert(req: ProcessAlertRequest) -> dict[str, Any]:
    """Run an alert through the deduplication pipeline.

    Returns the decision — a new incident, a merge into an existing one, or a
    suppression — alongside the layers the caller's plan enabled.
    """
    pipeline = await get_pipeline()
    alert = _to_raw_alert(req)

    decision = pipeline.process_alert(alert)

    result = decision.to_dict()
    result["layers_enabled"] = _layers_enabled(pipeline.config)
    return result


@router.get("/incidents")
async def list_incidents() -> dict[str, Any]:
    """List this organization's active incidents."""
    pipeline = await get_pipeline()
    incidents = pipeline.get_active_incidents()
    return {
        "count": len(incidents),
        "incidents": [i.to_dict() for i in incidents],
    }


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: UUID) -> dict[str, Any]:
    """Fetch a single incident."""
    pipeline = await get_pipeline()
    incident = pipeline.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident.to_dict()


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: UUID) -> dict[str, Any]:
    """Resolve an incident and feed the outcome back to the ML layer."""
    pipeline = await get_pipeline()
    if not pipeline.resolve_incident(incident_id):
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return {"incident_id": str(incident_id), "status": "resolved"}


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Pipeline statistics, including the achieved noise reduction rate."""
    pipeline = await get_pipeline()
    stats = pipeline.get_stats()
    stats["layers_enabled"] = _layers_enabled(pipeline.config)
    return stats
