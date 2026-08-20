"""The deduplication engine must be reachable, tenant-isolated, and plan-gated.

`app/deduplication/` is a 6-layer, ~4k-line engine that nothing in `app/` imported:
no router, no worker, no call site. At the same time `advanced_deduplication` was
sold from the Growth plan up and enforced nowhere. These tests pin the three things
that wiring has to get right, each of which fails silently otherwise:

1. The routes exist and the pipeline actually returns decisions.
2. Pipelines are per-organization — a shared one would merge one tenant's alerts
   into another tenant's incident and return that incident's ID.
3. Plan flags select layers. A Starter customer gets fingerprint + suppression; the
   paid flags each switch on the layer their price-list entry names.
"""

from __future__ import annotations

import pytest
from uuid import UUID, uuid4

from app import tenant_context
from app.billing import runtime as billing_runtime
from app.billing.models import FeatureOverride
from app.deduplication import api_router as dedup_api
from app.deduplication.models import AlertSeverity, AlertSource

# Flag -> layers it must switch on, straight from the price list:
#   advanced_deduplication = "Advanced Noise Reduction (95%)"
#   cascade_detection      = "Cascade Detection"
#   service_dependency_map = "Service Dependency Map"
FLAG_TO_LAYERS = {
    "advanced_deduplication": {"semantic", "ml"},
    "cascade_detection": {"temporal"},
    "service_dependency_map": {"topology"},
}

# Included in every plan via basic_deduplication, so never gated.
ALWAYS_ON_LAYERS = {"fingerprint", "suppression"}


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset the per-org pipelines and the shared billing store between tests."""
    dedup_api.reset_pipelines()
    billing_runtime._BILLING_STORE = None
    billing_runtime._BILLING_API = None
    yield
    dedup_api.reset_pipelines()
    billing_runtime._BILLING_STORE = None
    billing_runtime._BILLING_API = None


async def _grant(org_id: UUID, *feature_keys: str) -> None:
    """Enable features for an org via a feature override.

    Overrides are checked ahead of the plan by `FeatureGateService`, so this grants
    a flag without having to build a subscription and price plan per test.
    """
    store = billing_runtime.get_billing_store()
    for key in feature_keys:
        await store.save_feature_override(
            FeatureOverride(org_id=org_id, feature_key=key, enabled=True)
        )


def _alert(**overrides):
    payload = {
        "alert_name": "HighErrorRate",
        "service": "checkout",
        "source": "prometheus",
        "severity": "high",
        "message": "error rate 12%",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# 1. Reachability
# --------------------------------------------------------------------------


def test_app_serves_the_deduplication_routes() -> None:
    """The engine is only wired if the app actually answers on these paths.

    Asserted over HTTP rather than by walking `app.routes`, because FastAPI wraps
    included routers in `_IncludedRouter` objects that carry no `.path` — an
    introspection-based check would have to reproduce that structure and would rot.
    """
    from fastapi.testclient import TestClient

    from app.api import main as api

    with TestClient(api.app) as client:
        stats = client.get("/api/deduplication/stats")
        assert stats.status_code == 200, (
            f"GET /api/deduplication/stats returned {stats.status_code}; the pipeline "
            f"is unreachable"
        )
        assert "metrics" in stats.json()

        processed = client.post("/api/deduplication/process", json=_alert())
        assert processed.status_code == 200, processed.text
        body = processed.json()
        assert body["decision"] == "new_incident"
        assert set(body["layers_enabled"]) >= ALWAYS_ON_LAYERS

        listed = client.get("/api/deduplication/incidents")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1


def test_unknown_source_over_http_is_a_422() -> None:
    """The enum guard has to survive the round trip, not just the unit call."""
    from fastapi.testclient import TestClient

    from app.api import main as api

    with TestClient(api.app) as client:
        resp = client.post("/api/deduplication/process", json=_alert(source="nagios"))
    assert resp.status_code == 422
    assert "nagios" in resp.text


async def test_process_alert_returns_a_decision() -> None:
    pipeline = await dedup_api.get_pipeline()
    alert = dedup_api._to_raw_alert(dedup_api.ProcessAlertRequest(**_alert()))

    decision = pipeline.process_alert(alert)

    assert decision.raw_alert_id == alert.alert_id
    assert decision.decision.value in {
        "new_incident",
        "add_to_existing",
        "suppress",
        "flapping",
    }


async def test_identical_alerts_deduplicate() -> None:
    """The whole point of the engine: the second copy joins the first's incident."""
    pipeline = await dedup_api.get_pipeline()
    req = dedup_api.ProcessAlertRequest(**_alert())

    first = pipeline.process_alert(dedup_api._to_raw_alert(req))
    second = pipeline.process_alert(dedup_api._to_raw_alert(req))

    assert first.decision.value == "new_incident"
    assert second.decision.value == "add_to_existing", (
        f"a duplicate alert produced {second.decision.value}; deduplication is not "
        f"actually running"
    )
    assert second.incident_id == first.incident_id


# --------------------------------------------------------------------------
# 2. Tenant isolation
# --------------------------------------------------------------------------


async def test_each_org_gets_its_own_pipeline() -> None:
    org_a, org_b = uuid4(), uuid4()

    pipeline_a = await dedup_api.get_pipeline(org_a)
    pipeline_b = await dedup_api.get_pipeline(org_b)

    assert pipeline_a is not pipeline_b


async def test_one_tenants_alert_never_joins_anothers_incident() -> None:
    """A shared pipeline would leak `incident_id` across tenants."""
    org_a, org_b = uuid4(), uuid4()
    req = dedup_api.ProcessAlertRequest(**_alert())

    decision_a = (await dedup_api.get_pipeline(org_a)).process_alert(
        dedup_api._to_raw_alert(req)
    )
    decision_b = (await dedup_api.get_pipeline(org_b)).process_alert(
        dedup_api._to_raw_alert(req)
    )

    assert decision_b.decision.value == "new_incident", (
        "org B's alert was merged into org A's incident — cross-tenant correlation"
    )
    assert decision_b.incident_id != decision_a.incident_id

    incidents_a = (await dedup_api.get_pipeline(org_a)).get_active_incidents()
    incidents_b = (await dedup_api.get_pipeline(org_b)).get_active_incidents()
    assert {i.incident_id for i in incidents_a}.isdisjoint(
        {i.incident_id for i in incidents_b}
    )


async def test_pipeline_follows_the_bound_tenant() -> None:
    """With no explicit org the pipeline comes from tenant context, not a global."""
    cfg = tenant_context.TenantConfig(org_id=str(uuid4()))
    token = tenant_context.set_tenant(cfg)
    try:
        scoped = await dedup_api.get_pipeline()
    finally:
        tenant_context.reset_tenant(token)

    unscoped = await dedup_api.get_pipeline()
    assert scoped is not unscoped


# --------------------------------------------------------------------------
# 3. Plan gating
# --------------------------------------------------------------------------


async def test_free_plan_still_gets_working_deduplication() -> None:
    """Gating selects depth, not access — basic_deduplication is in every plan."""
    pipeline = await dedup_api.get_pipeline(uuid4())
    layers = set(dedup_api._layers_enabled(pipeline.config))

    assert ALWAYS_ON_LAYERS <= layers, (
        f"an unsubscribed org lost {ALWAYS_ON_LAYERS - layers}; every plan includes "
        f"basic_deduplication, so exact matching and suppression must stay on"
    )


async def test_paid_layers_are_off_without_the_flag() -> None:
    gated = {layer for layers in FLAG_TO_LAYERS.values() for layer in layers}
    pipeline = await dedup_api.get_pipeline(uuid4())
    layers = set(dedup_api._layers_enabled(pipeline.config))

    assert layers.isdisjoint(gated), (
        f"{layers & gated} ran for an org on the free plan; these are sold from the "
        f"Growth tier up"
    )


@pytest.mark.parametrize("flag,expected", sorted(FLAG_TO_LAYERS.items()))
async def test_each_flag_switches_on_the_layers_it_names(
    flag: str, expected: set[str]
) -> None:
    org = uuid4()
    await _grant(org, flag)

    layers = set(dedup_api._layers_enabled((await dedup_api.get_pipeline(org)).config))

    assert expected <= layers, f"{flag} is granted but {expected - layers} stayed off"

    other = {
        layer
        for key, layers_for_key in FLAG_TO_LAYERS.items()
        if key != flag
        for layer in layers_for_key
    }
    assert layers.isdisjoint(other - expected), (
        f"granting {flag} also enabled {layers & (other - expected)}, which belongs to "
        f"a different plan flag"
    )


async def test_full_plan_enables_all_six_layers() -> None:
    org = uuid4()
    await _grant(org, *dedup_api.GATING_FEATURES)

    layers = set(dedup_api._layers_enabled((await dedup_api.get_pipeline(org)).config))

    assert layers == ALWAYS_ON_LAYERS | {
        layer for layers_for_key in FLAG_TO_LAYERS.values() for layer in layers_for_key
    }


async def test_gating_features_matches_what_the_config_reads() -> None:
    """GATING_FEATURES must be exactly the keys `_config_for` consumes.

    A flag left out of the list resolves to False forever, so the layer it controls
    would be permanently off even for a customer paying for it.
    """
    all_on = dedup_api._config_for({k: True for k in dedup_api.GATING_FEATURES})
    all_off = dedup_api._config_for({})

    changed = set(dedup_api._layers_enabled(all_on)) - set(
        dedup_api._layers_enabled(all_off)
    )
    expected = {layer for layers in FLAG_TO_LAYERS.values() for layer in layers}
    assert changed == expected


async def test_upgrade_takes_effect_without_losing_incidents() -> None:
    """Plans are re-resolved per call; an upgrade must not reset accumulated state."""
    org = uuid4()
    pipeline = await dedup_api.get_pipeline(org)
    pipeline.process_alert(dedup_api._to_raw_alert(dedup_api.ProcessAlertRequest(**_alert())))
    assert pipeline.config.enable_layer2_semantic is False

    await _grant(org, "advanced_deduplication")
    upgraded = await dedup_api.get_pipeline(org)

    assert upgraded is pipeline, "upgrading rebuilt the pipeline and dropped incidents"
    assert upgraded.config.enable_layer2_semantic is True
    assert len(upgraded.get_active_incidents()) == 1


async def test_billing_outage_does_not_take_down_the_pipeline(monkeypatch) -> None:
    """A failing billing store must not turn into a failed alert ingestion."""

    class _Broken:
        async def get_feature_status(self, *args, **kwargs):
            raise RuntimeError("billing store unavailable")

    monkeypatch.setattr(billing_runtime, "get_billing_api", lambda: _Broken())

    features = await billing_runtime.enabled_features(dedup_api.GATING_FEATURES, uuid4())
    assert all(features.values()), (
        "feature resolution failed closed; a billing outage would silently downgrade "
        "every customer's deduplication instead of degrading gracefully"
    )


# --------------------------------------------------------------------------
# Request translation
# --------------------------------------------------------------------------


def test_unknown_source_is_a_422_not_a_500() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        dedup_api._to_raw_alert(dedup_api.ProcessAlertRequest(**_alert(source="nagios")))
    assert exc.value.status_code == 422
    assert "nagios" in exc.value.detail


def test_unknown_severity_is_a_422_not_a_500() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        dedup_api._to_raw_alert(dedup_api.ProcessAlertRequest(**_alert(severity="sev1")))
    assert exc.value.status_code == 422
    assert "sev1" in exc.value.detail


def test_request_fields_reach_the_raw_alert() -> None:
    alert = dedup_api._to_raw_alert(
        dedup_api.ProcessAlertRequest(
            **_alert(
                source="datadog",
                severity="critical",
                labels={"team": "payments"},
                namespace="prod",
                trace_id="abc123",
            )
        )
    )

    assert alert.source is AlertSource.DATADOG
    assert alert.severity is AlertSeverity.CRITICAL
    assert alert.service == "checkout"
    assert alert.labels == {"team": "payments"}
    assert alert.namespace == "prod"
    assert alert.trace_id == "abc123"


# --------------------------------------------------------------------------
# The shared billing store
# --------------------------------------------------------------------------


def test_billing_router_and_gate_share_one_store() -> None:
    """Two stores would mean an org that upgraded still looked unsubscribed."""
    from app.api import billing_router

    assert billing_router._get_billing_api is billing_runtime.get_billing_api
    assert billing_runtime.get_billing_store() is billing_runtime.get_billing_store()
