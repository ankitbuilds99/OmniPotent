"""Event Intelligence API Router.

REST API for noise reduction, severity prediction, and feature store.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.event_intelligence.noise_reduction import (
    NoiseReductionEngine,
    NoiseSignal,
)
from app.event_intelligence.severity_predictor import (
    SeverityPredictor,
    IncidentContext,
)
from app.event_intelligence.feature_store import FeatureStore, FeatureStoreUnavailable
from app.tenancy.scoping import scoped
from app.tenant_context import tenant_id

log = logging.getLogger("omnipotent.event_intelligence.api")

router = APIRouter(prefix="/event-intelligence", tags=["event-intelligence"])

# One client per URL, shared by every org's store: `from_url` builds a
# connection pool, and one pool per tenant would be hundreds of them.
_redis_clients: dict[str, Any] = {}


def _feature_store_redis() -> Any:
    """A real Redis client for the feature store, or None if not configured.

    None means recorded metrics and alerts live in process memory only, and the
    API says so on every write. A stub client is never substituted: "the data
    was saved" has to be true or reported as false.
    """
    url = (get_settings().copilot_redis_url or "").strip()
    if not url:
        return None

    client = _redis_clients.get(url)
    if client is not None:
        return client

    try:
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(url)
    except Exception as exc:  # pragma: no cover - bad URL / missing driver
        # COPILOT_REDIS_URL was set, so falling back to memory would quietly
        # downgrade a deployment that asked for durability.
        raise FeatureStoreUnavailable(
            f"COPILOT_REDIS_URL is set but a Redis client could not be built: {exc}"
        ) from exc

    _redis_clients[url] = client
    return client


def _build_feature_store() -> FeatureStore:
    """Build this org's feature store.

    Redis is shared by every tenant, so the org id is part of every key. The
    registry builds lazily inside the request that first needs the store, which
    is why the tenant is readable here.
    """
    return FeatureStore(
        redis_client=_feature_store_redis(),
        namespace=tenant_id() or "-",
    )


# One instance per org. These are not stateless helpers: the noise engine
# accumulates alert fingerprints, the predictor learns from submitted incidents,
# and the feature store holds per-service history. Shared, one tenant's alert
# volume shapes another's severity predictions and leaks service names back.
_noise_engines = scoped(NoiseReductionEngine)
_severity_predictors = scoped(SeverityPredictor)
_feature_stores = scoped(_build_feature_store)


def get_noise_engine() -> NoiseReductionEngine:
    return _noise_engines.get()


def get_severity_predictor() -> SeverityPredictor:
    return _severity_predictors.get()


def get_feature_store() -> FeatureStore:
    return _feature_stores.get()


@contextmanager
def _storage_errors() -> Iterator[None]:
    """Turn a storage failure into a 503 that names the backend.

    The store raises instead of logging and returning success, so this is the
    one place that decides what the caller is told - and it is never "recorded".
    """
    try:
        yield
    except FeatureStoreUnavailable as exc:
        log.warning("event-intelligence feature store unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "feature_store_unavailable",
                "message": str(exc),
            },
        ) from exc


def _storage_note(store: FeatureStore) -> dict[str, Any]:
    """Where the write actually landed, attached to every write response.

    Without Redis this history is real but process-local. Saying so on the
    response is the difference between "recorded" meaning "durably stored" and
    "recorded" meaning "held until this worker restarts".
    """
    return {"durable": store.is_durable, "storage": store.storage_description}


def _parse_window_bound(value: str, field_name: str) -> datetime:
    """Parse an ISO-8601 maintenance-window bound, or 422.

    An unparseable timestamp used to escape as a ValueError and surface as a
    500, which reads as "the server is broken" rather than "that string is not
    a timestamp". A bound without an offset is taken as UTC.
    """
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field_name} must be an ISO-8601 timestamp "
                f"(e.g. 2026-01-01T00:00:00Z); got {value!r}"
            ),
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class NoiseCheckRequest(BaseModel):
    alert_id: str
    source: str
    service: str
    metric_name: str = ""
    value: float = 0.0
    threshold: float = 0.0
    environment: str = "production"
    severity: str = "warning"
    labels: dict[str, str] = {}
    # Optional: the fingerprint this alert's history was recorded under via
    # /features/record-alert. Defaults to the signal's own derived fingerprint,
    # which is returned in the response so callers can record against it.
    fingerprint: str = ""


class SeverityPredictRequest(BaseModel):
    incident_id: str
    service: str
    alert_type: str = "error"
    error_rate: float | None = None
    latency_p99: float | None = None
    affected_endpoints: list[str] = []
    related_alerts_count: int = 0
    # Two of the predictor's inputs had no way in. Left unset they fall back to
    # neutral priors (0.3 and False), and with the blast radius pinned at 0.3
    # the score could not clear the 0.85 SEV1 threshold no matter what else the
    # caller sent -- the top severity was unreachable through this API. Omitting
    # blast_radius now means "work it out from the knowledge graph", not
    # "assume moderate".
    blast_radius: dict[str, Any] | None = None
    slo_status: dict[str, Any] | None = None


class RecordMetricRequest(BaseModel):
    service: str
    metric_name: str
    value: float
    labels: dict[str, str] = {}


class RecordAlertRequest(BaseModel):
    fingerprint: str
    service: str
    metric_name: str = ""
    severity: str = "warning"
    resolved: bool = False
    resolution_time_seconds: int | None = None
    auto_resolved: bool = False


class DeploymentRequest(BaseModel):
    service: str


class MaintenanceWindowRequest(BaseModel):
    services: list[str]
    start_iso: str
    end_iso: str
    description: str = ""


@router.post("/noise/check")
async def check_noise(req: NoiseCheckRequest) -> dict[str, Any]:
    """Check if an alert should be considered noise.

    Returns a noise decision with category and confidence.
    """
    engine = get_noise_engine()

    with _storage_errors():
        store = get_feature_store()

        signal = NoiseSignal(
            alert_id=req.alert_id,
            source=req.source,
            service=req.service,
            metric_name=req.metric_name,
            value=req.value,
            threshold=req.threshold,
            timestamp=datetime.now(timezone.utc),
            environment=req.environment,
            severity=req.severity,
            labels=req.labels,
            deployment_in_progress=await store.is_deployment_in_progress(req.service),
            maintenance_window_active=await store.is_in_maintenance(req.service),
        )

        # The feature store computes exactly the history the engine's
        # statistical and auto-resolve strategies need, and none of it used to
        # reach them: hourly_stddev stayed None, so _check_statistical_anomaly
        # returned early on every HTTP request and the strategy was dead.
        fingerprint = req.fingerprint or signal.fingerprint()
        features = await store.get_features(fingerprint, req.service, req.metric_name)

    # Only feed in statistics that were computed from real samples. A mean of
    # 0.0 over zero data points is not a baseline, and a z-score against it
    # would be a verdict invented from missing history.
    if features.hourly_sample_count >= 2:
        signal.hourly_avg = features.hourly_avg
        signal.hourly_stddev = features.hourly_stddev
    if features.daily_sample_count >= 2:
        signal.daily_avg = features.daily_avg
    signal.flap_count_1h = features.flap_count_1h
    signal.similar_alerts_1h = features.service_alert_count_1h
    signal.auto_resolve_rate_7d = features.auto_resolve_rate_7d

    decision = await engine.evaluate(signal)

    return {
        "alert_id": decision.alert_id,
        "is_noise": decision.is_noise,
        "noise_category": decision.noise_category.value if decision.noise_category else None,
        "confidence": decision.confidence,
        "reasoning": decision.reasoning,
        "should_suppress": decision.should_suppress,
        "suppress_duration_seconds": decision.suppress_duration_seconds,
        "human_review_recommended": decision.human_review_recommended,
        "strategy_used": decision.strategy_used,
        # What the decision was actually informed by, so a caller can tell a
        # judgement backed by history from one made with none.
        "history": {
            "fingerprint": fingerprint,
            "metric_samples_1h": features.hourly_sample_count,
            "metric_samples_24h": features.daily_sample_count,
            "alert_count_1h": features.alert_count_1h,
            "flap_count_1h": features.flap_count_1h,
            "service_alert_count_1h": features.service_alert_count_1h,
            "auto_resolve_rate_7d": features.auto_resolve_rate_7d,
            "statistical_check_applied": features.hourly_sample_count >= 2,
        },
    }


async def _blast_radius_from_graph(service: str) -> dict[str, Any] | None:
    """This org's blast radius for `service`, in the shape the predictor scores.

    The knowledge graph already walks the dependency edges; it reports affected
    services, and the predictor wants a fraction plus two flags. Returns None
    when the graph has never heard of the service, so the predictor keeps its
    "unknown, assume moderate" prior rather than being handed a confident zero.
    """
    try:
        from app.knowledge_graph.api_router import get_graph

        graph = await get_graph()
        result = await graph.find_blast_radius(service)
        known = await graph.list_services(limit=1000)
    except Exception:  # graph unavailable, unconfigured, or not isolated
        log.debug("blast radius unavailable for %s", service, exc_info=True)
        return None

    if not known or not any(s.id == service or s.name == service for s in known):
        return None

    affected = result.get("affected_services", [])
    names = [a["service"].name for a in affected] + [a["service"].id for a in affected]

    predictor = get_severity_predictor()
    return {
        "affected_percentage": len(affected) / len(known),
        "revenue_path_affected": any(predictor.is_revenue_path(n) for n in names),
        "customer_facing_impact": any(predictor.is_customer_facing(n) for n in names),
        "affected_count": len(affected),
        "known_service_count": len(known),
    }


@router.post("/severity/predict")
async def predict_severity(req: SeverityPredictRequest) -> dict[str, Any]:
    """Predict the severity of an incident.

    Scores service criticality, blast radius, alert type, time sensitivity and
    historical patterns. The blast radius comes from the request when supplied
    and from this org's knowledge graph otherwise.
    """
    predictor = get_severity_predictor()

    blast_radius = req.blast_radius
    if blast_radius is None:
        blast_radius = await _blast_radius_from_graph(req.service)

    context = IncidentContext(
        incident_id=req.incident_id,
        service=req.service,
        alert_type=req.alert_type,
        error_rate=req.error_rate,
        latency_p99=req.latency_p99,
        affected_endpoints=req.affected_endpoints,
        related_alerts_count=req.related_alerts_count,
        blast_radius=blast_radius,
        current_slo_status=req.slo_status,
    )

    prediction = await predictor.predict(context)

    return {
        **prediction.to_dict(),
        # Which of the two optional inputs the score actually had, so a caller
        # can tell a judgement backed by a dependency graph from one made
        # against the default prior.
        "inputs": {
            "blast_radius": blast_radius,
            "blast_radius_source": (
                "request" if req.blast_radius is not None
                else "knowledge_graph" if blast_radius is not None
                else "unknown"
            ),
            "slo_status_provided": req.slo_status is not None,
        },
    }


@router.post("/features/record-metric")
async def record_metric(req: RecordMetricRequest) -> dict[str, Any]:
    """Record a metric data point for feature extraction."""
    with _storage_errors():
        store = get_feature_store()

        await store.record_metric(
            service=req.service,
            metric_name=req.metric_name,
            value=req.value,
            labels=req.labels,
        )

    return {"status": "recorded", **_storage_note(store)}


@router.post("/features/record-alert")
async def record_alert(req: RecordAlertRequest) -> dict[str, Any]:
    """Record an alert occurrence for feature extraction."""
    with _storage_errors():
        store = get_feature_store()

        await store.record_alert(
            fingerprint=req.fingerprint,
            service=req.service,
            metric_name=req.metric_name,
            severity=req.severity,
            resolved=req.resolved,
            resolution_time_seconds=req.resolution_time_seconds,
            auto_resolved=req.auto_resolved,
        )

    return {"status": "recorded", **_storage_note(store)}


@router.get("/features/{service}/{metric_name}")
async def get_features(service: str, metric_name: str, fingerprint: str = "") -> dict[str, Any]:
    """Get extracted features for a service/metric combination."""
    with _storage_errors():
        store = get_feature_store()

        fp = fingerprint or f"{service}:{metric_name}"
        features = await store.get_features(fp, service, metric_name)

    return {
        "fingerprint": fp,
        "service": service,
        "metric_name": metric_name,
        "features": {
            "hourly_avg": features.hourly_avg,
            "hourly_stddev": features.hourly_stddev,
            "daily_avg": features.daily_avg,
            "daily_stddev": features.daily_stddev,
            "alert_count_1h": features.alert_count_1h,
            "alert_count_24h": features.alert_count_24h,
            "flap_count_1h": features.flap_count_1h,
            "auto_resolve_rate_7d": features.auto_resolve_rate_7d,
            "is_business_hours": features.is_business_hours,
            "deployment_in_progress": features.deployment_in_progress,
            "maintenance_active": features.maintenance_active,
        },
        # How many data points each statistic came from. A consumer cannot tell
        # "the average is 0.0" from "there is no history" without these, and
        # that ambiguity is what let an empty store look like a calm baseline.
        "sample_counts": {
            "hourly": features.hourly_sample_count,
            "daily": features.daily_sample_count,
            "weekly": features.weekly_sample_count,
        },
        "vector": features.to_vector(),
        **_storage_note(store),
    }


@router.post("/deployments/start")
async def start_deployment(req: DeploymentRequest) -> dict[str, Any]:
    """Record a deployment start for a service."""
    with _storage_errors():
        store = get_feature_store()
        await store.record_deployment(req.service)
    return {"status": "recorded", "service": req.service, **_storage_note(store)}


@router.post("/deployments/end")
async def end_deployment(req: DeploymentRequest) -> dict[str, Any]:
    """Clear deployment flag for a service."""
    with _storage_errors():
        store = get_feature_store()
        await store.clear_deployment(req.service)
    return {"status": "cleared", "service": req.service, **_storage_note(store)}


@router.get("/deployments/{service}")
async def check_deployment(service: str) -> dict[str, Any]:
    """Check if a deployment is in progress for a service."""
    with _storage_errors():
        store = get_feature_store()
        in_progress = await store.is_deployment_in_progress(service)
    return {
        "service": service,
        "in_progress": in_progress,
        **_storage_note(store),
    }


@router.post("/maintenance/add")
async def add_maintenance(req: MaintenanceWindowRequest) -> dict[str, Any]:
    """Add a maintenance window."""
    start = _parse_window_bound(req.start_iso, "start_iso")
    end = _parse_window_bound(req.end_iso, "end_iso")

    if end <= start:
        raise HTTPException(
            status_code=422,
            detail="end_iso must be after start_iso",
        )

    with _storage_errors():
        store = get_feature_store()
        await store.add_maintenance_window(
            services=req.services,
            start=start,
            end=end,
            description=req.description,
        )

    return {
        "status": "added",
        "services": req.services,
        "start": start.isoformat(),
        "end": end.isoformat(),
        **_storage_note(store),
    }


@router.get("/maintenance/{service}")
async def check_maintenance(service: str) -> dict[str, Any]:
    """Check if a service is in maintenance."""
    with _storage_errors():
        store = get_feature_store()
        in_maintenance = await store.is_in_maintenance(service)
    return {
        "service": service,
        "in_maintenance": in_maintenance,
        **_storage_note(store),
    }


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get event intelligence system statistics."""
    engine = get_noise_engine()

    with _storage_errors():
        store = get_feature_store()
        feature_store_stats = await store.get_stats()

    return {
        "noise_reduction": engine.get_stats(),
        "feature_store": feature_store_stats,
    }
