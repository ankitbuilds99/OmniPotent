"""Event Intelligence API Router.

REST API for noise reduction, severity prediction, and feature store.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.event_intelligence.noise_reduction import (
    NoiseReductionEngine,
    NoiseSignal,
)
from app.event_intelligence.severity_predictor import (
    SeverityPredictor,
    IncidentContext,
)
from app.event_intelligence.feature_store import FeatureStore

log = logging.getLogger("devcopilot.event_intelligence.api")

router = APIRouter(prefix="/event-intelligence", tags=["event-intelligence"])

# Global instances
_noise_engine: NoiseReductionEngine | None = None
_severity_predictor: SeverityPredictor | None = None
_feature_store: FeatureStore | None = None


def get_noise_engine() -> NoiseReductionEngine:
    global _noise_engine
    if _noise_engine is None:
        _noise_engine = NoiseReductionEngine()
    return _noise_engine


def get_severity_predictor() -> SeverityPredictor:
    global _severity_predictor
    if _severity_predictor is None:
        _severity_predictor = SeverityPredictor()
    return _severity_predictor


def get_feature_store() -> FeatureStore:
    global _feature_store
    if _feature_store is None:
        _feature_store = FeatureStore()
    return _feature_store


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


class SeverityPredictRequest(BaseModel):
    incident_id: str
    service: str
    alert_type: str = "error"
    error_rate: float | None = None
    latency_p99: float | None = None
    affected_endpoints: list[str] = []
    related_alerts_count: int = 0


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
        deployment_in_progress=store.is_deployment_in_progress(req.service),
        maintenance_window_active=store.is_in_maintenance(req.service),
    )

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
    }


@router.post("/severity/predict")
async def predict_severity(req: SeverityPredictRequest) -> dict[str, Any]:
    """Predict the severity of an incident.

    Uses ML-based scoring considering service criticality,
    blast radius, time sensitivity, and historical patterns.
    """
    predictor = get_severity_predictor()

    context = IncidentContext(
        incident_id=req.incident_id,
        service=req.service,
        alert_type=req.alert_type,
        error_rate=req.error_rate,
        latency_p99=req.latency_p99,
        affected_endpoints=req.affected_endpoints,
        related_alerts_count=req.related_alerts_count,
    )

    prediction = await predictor.predict(context)

    return prediction.to_dict()


@router.post("/features/record-metric")
async def record_metric(req: RecordMetricRequest) -> dict[str, Any]:
    """Record a metric data point for feature extraction."""
    store = get_feature_store()

    await store.record_metric(
        service=req.service,
        metric_name=req.metric_name,
        value=req.value,
        labels=req.labels,
    )

    return {"status": "recorded"}


@router.post("/features/record-alert")
async def record_alert(req: RecordAlertRequest) -> dict[str, Any]:
    """Record an alert occurrence for feature extraction."""
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

    return {"status": "recorded"}


@router.get("/features/{service}/{metric_name}")
async def get_features(service: str, metric_name: str, fingerprint: str = "") -> dict[str, Any]:
    """Get extracted features for a service/metric combination."""
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
        "vector": features.to_vector(),
    }


@router.post("/deployments/start")
async def start_deployment(req: DeploymentRequest) -> dict[str, Any]:
    """Record a deployment start for a service."""
    store = get_feature_store()
    store.record_deployment(req.service)
    return {"status": "recorded", "service": req.service}


@router.post("/deployments/end")
async def end_deployment(req: DeploymentRequest) -> dict[str, Any]:
    """Clear deployment flag for a service."""
    store = get_feature_store()
    store.clear_deployment(req.service)
    return {"status": "cleared", "service": req.service}


@router.get("/deployments/{service}")
async def check_deployment(service: str) -> dict[str, Any]:
    """Check if a deployment is in progress for a service."""
    store = get_feature_store()
    return {
        "service": service,
        "in_progress": store.is_deployment_in_progress(service),
    }


@router.post("/maintenance/add")
async def add_maintenance(req: MaintenanceWindowRequest) -> dict[str, Any]:
    """Add a maintenance window."""
    store = get_feature_store()

    start = datetime.fromisoformat(req.start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(req.end_iso.replace("Z", "+00:00"))

    store.add_maintenance_window(
        services=req.services,
        start=start,
        end=end,
        description=req.description,
    )

    return {"status": "added", "services": req.services}


@router.get("/maintenance/{service}")
async def check_maintenance(service: str) -> dict[str, Any]:
    """Check if a service is in maintenance."""
    store = get_feature_store()
    return {
        "service": service,
        "in_maintenance": store.is_in_maintenance(service),
    }


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get event intelligence system statistics."""
    engine = get_noise_engine()
    store = get_feature_store()

    return {
        "noise_reduction": engine.get_stats(),
        "feature_store": store.get_stats(),
    }
