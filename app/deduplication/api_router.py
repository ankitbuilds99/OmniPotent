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

**This router is also where digests leave the engine.** Layer 6 collects repeating
non-critical alerts into digest groups instead of dropping them, but
`SuppressionEngine.check_suppression` is synchronous and knows nothing about the
tenant, so it cannot reach `app/notifications/delivery.py` — which is async and
per-organization — on its own. Every endpoint here is async and has already
resolved the org, so this is the seam: `set_digest_sink()` attaches
`_deliver_digest_groups` to each org's engine, and a closed digest window is flushed
by the next alert, by `POST /deduplication/digests/flush`, or by a periodic call to
`flush_all_due_digests()` — the last of which is what covers an org whose alerts have
stopped, since a window closes with time rather than with traffic.
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
from app.deduplication.suppression import DigestEntry
from app.tenant_context import TenantConfig, get_tenant
from app.config import get_settings

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

# The tenant each pipeline was created under, kept so a background flush can put it
# back. `_pipelines` is keyed by `org_uuid()`, which is *derived* from the tenant id
# and cannot be turned back into one, and `_deliver_digest_groups` reaches
# `notify()`, which reads the current tenant. Flushing every org from a timer without
# restoring the tenant would deliver one org's digest to whichever org the ambient
# context happened to name — the single-tenant default, for a timer.
_pipeline_tenants: dict[str, TenantConfig | None] = {}


def _config_for(features: dict[str, bool], org: UUID | None = None) -> PipelineConfig:
    """Translate resolved plan flags into per-layer pipeline configuration."""
    tenant_key = org or UUID(int=0)
    return PipelineConfig(
        # Included in every plan via basic_deduplication.
        enable_layer1_fingerprint=True,
        enable_layer6_suppression=True,
        enable_layer2_semantic=features.get("advanced_deduplication", False),
        enable_layer5_ml=features.get("advanced_deduplication", False),
        enable_layer3_temporal=features.get("cascade_detection", False),
        enable_layer4_topology=features.get("service_dependency_map", False),
        shared_state_url=get_settings().copilot_dedup_state_url,
        # A tenant must never restore another tenant's incident snapshot. The
        # tenant-scoped key is part of the configuration, not caller input.
        shared_state_key=f"omnipotent:dedup:v1:{tenant_key}",
        require_shared_state=get_settings().copilot_dedup_state_required,
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


#: The catalogue event a digested alert is delivered as. `alert_triggered` is the
#: only event in `app/notifications/preferences.py` that describes an alert firing,
#: and routing a digest anywhere else would send it to channels the org did not tick
#: for alerts.
DIGEST_EVENT = "alert_triggered"

#: Delivery outcomes that mean "not now" rather than "never". The digest groups go
#: back into the engine's buffer for these, so quiet hours, a flat webhook or a
#: database that would not take the queued row do not consume the backlog.
#: `digest_unavailable` is in the list for that last reason: `deliver()` returns it
#: when the org batches notifications and `hold_for_digest` failed to persist them,
#: so the alerts exist in neither queue and treating it as handled would lose them.
#: Everything else — including `no_channels`, which is the org saying it does not
#: want these, and `unknown_event`, which is a misconfiguration retrying cannot fix —
#: counts as handled, because retrying it forever would only grow the buffer until
#: the 24-hour expiry cleared it anyway.
RETRYABLE_DIGEST_STATUSES = frozenset(
    {"failed", "quiet_hours", "preferences_unavailable", "digest_unavailable"}
)


async def _deliver_digest_groups(entries: list[DigestEntry]) -> bool:
    """Send one digest covering `entries`. The sink `SuppressionEngine` calls.

    Layer 6's digest rules collect a repeating non-critical alert instead of
    dropping it, and until now the collection went nowhere: the buffer accumulated
    and `get_suppression_stats()` reported it as undelivered, which was true and
    useless. This is the seam the engine could not cross on its own — delivery is
    async and needs the org, `check_suppression` is synchronous and tenant-unaware,
    and this router is already both. `notify()` applies the org's routing rules,
    quiet hours and its own digest mode, so an org on hourly/daily batching gets
    this queued through `hold_for_digest` rather than sent twice over.
    """
    from app.notifications.delivery import notify

    total = sum(e.count for e in entries)
    result = await notify(
        event=DIGEST_EVENT,
        title=(
            f"Alert digest: {total} repeated alert(s) in "
            f"{len(entries)} group(s)"
        ),
        body="\n".join(e.summary() for e in entries),
        data={
            "source": "deduplication_digest",
            "groups": [e.to_dict() for e in entries],
            "alert_count": total,
            "service": entries[0].service if len(entries) == 1 else "",
        },
        dedupe_key="deduplication-digest:" + ",".join(e.fingerprint for e in entries),
    )

    status = str(result.get("status") or "")
    if status in RETRYABLE_DIGEST_STATUSES:
        log.info(
            "digest of %d group(s) not delivered (%s); returned to the buffer",
            len(entries),
            status,
        )
        return False
    if status not in ("sent", "digest"):
        # Handled, but not delivered — say which, rather than letting the counter
        # tick up as though the operator had been told.
        log.warning(
            "digest of %d group(s) dropped by notification routing (%s)",
            len(entries),
            status or "no status",
        )
    return True


async def get_pipeline(org_id: UUID | None = None) -> DeduplicationPipeline:
    """Return this organization's pipeline, creating it on first use.

    The plan is resolved on every call so that an upgrade takes effect without a
    restart: an existing pipeline keeps its accumulated incidents and only has its
    layer configuration refreshed.
    """
    org = org_id if org_id is not None else current_org_id()
    features = await enabled_features(GATING_FEATURES, org)
    config = _config_for(features, org)

    key = str(org)
    pipeline = _pipelines.get(key)
    if pipeline is None:
        pipeline = DeduplicationPipeline(config)
        # Connect Layer 6's digest buffer to the notification transport. Attached
        # here rather than in the engine because this is where the org is known.
        pipeline.suppression.set_digest_sink(_deliver_digest_groups)
        _pipelines[key] = pipeline
        _pipeline_tenants[key] = get_tenant()
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
    _pipeline_tenants.clear()


async def flush_all_due_digests() -> list[dict[str, Any]]:
    """Flush every cached org's due digest groups. What a periodic job calls.

    `POST /process` flushes as a side effect of traffic, which is enough while
    alerts keep arriving and nothing at all once they stop: the last group of a
    burst would sit in the buffer until the 24-hour expiry silently dropped it —
    the alert loss Layer 6's digest rules were rewritten to avoid. A timer closes
    that gap, and a timer has no request behind it, so each pipeline is flushed
    under the tenant it was created for rather than under the ambient default.

    Never raises: one org's broken transport is not the other orgs' problem. Orgs
    with nothing due are skipped rather than reported, so the returned list is what
    was actually attempted and an empty list means "nothing was due", not "nothing
    was tried".
    """
    from app.tenant_context import run_with_tenant

    out: list[dict[str, Any]] = []
    for key, pipeline in list(_pipelines.items()):
        if not pipeline.suppression.has_due_digests():
            continue
        try:
            result = await run_with_tenant(
                _pipeline_tenants.get(key),
                pipeline.suppression.flush_digests(),
            )
        except Exception:  # noqa: BLE001 — one org's failure is not the others'
            log.warning("digest flush failed for org %s", key, exc_info=True)
            out.append({"org_id": key, "status": "failed", "groups": 0, "alerts": 0})
            continue
        out.append({"org_id": key, **result})
    return out


class ProcessAlertRequest(BaseModel):
    """An alert arriving from a monitoring system."""

    alert_name: str = Field(min_length=1)
    # `service` was a plain `str`, so `{"service": ""}` was a valid request. Layer 2
    # refuses to correlate a serviceless alert with anything — an unknown service is
    # not a wildcard — so such an alert can only ever get an incident of its own, and
    # Layer 1 groups it with every other serviceless alert of the same name. Neither
    # is what the sender meant. Rejecting it here says so, instead of accepting the
    # payload and quietly giving back a worse answer.
    service: str = Field(min_length=1)
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

    # Alert traffic is what advances a digest window, so an alert arriving is the
    # natural moment to check whether one has closed. `has_due_digests()` keeps the
    # common case free — nothing is due, and no notification lookup happens.
    if pipeline.suppression.has_due_digests():
        result["digest_delivery"] = await pipeline.suppression.flush_digests()
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


@router.post("/digests/flush")
async def flush_digests() -> dict[str, Any]:
    """Send this organization's due digest groups now.

    `POST /process` flushes as a side effect of traffic, which is enough while
    alerts keep arriving and not enough when they stop: the last group of a burst
    would sit in the buffer until the next alert. This is the hook an operator uses
    to close that gap for their own org; a periodic job wants `flush_all_due_digests()`
    instead, which covers every org and restores each one's tenant. It reports what
    happened rather than a bare 200 — `not_wired`, `nothing_due`, `failed` and `sent`
    are different answers and an empty success would hide three of them.
    """
    pipeline = await get_pipeline()
    return await pipeline.suppression.flush_digests()


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Pipeline statistics, including the achieved noise reduction rate."""
    pipeline = await get_pipeline()
    stats = pipeline.get_stats()
    stats["layers_enabled"] = _layers_enabled(pipeline.config)
    return stats
