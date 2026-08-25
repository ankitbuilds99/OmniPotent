"""Feature store: does a write actually go anywhere, and does anything read it?

Three things were faked here and are pinned below.

1. **Persistence.** ``FeatureStore`` accepted a ``redis_client`` and every
   endpoint constructed it with none, so the Redis branch was dead code. What
   little it did write it never read back: statistics always came from the
   process-local dicts. ``/features/record-metric`` answered
   ``{"status": "recorded"}`` either way, which reads as "stored" when it meant
   "held until this worker restarts".

2. **The computed features were thrown away.** ``/noise/check`` passed exactly
   two booleans out of the feature store into the signal, so
   ``hourly_stddev`` was always ``None`` and the engine's statistical strategy
   returned on its first line every single time.

3. **Errors were success.** A failing Redis command was logged at warning level
   and the caller was told "recorded".

The Redis cases run against ``fakeredis``, a real in-process implementation of
the Redis command set - the sorted-set range queries, the TTLs and the hash
commands all execute for real. It is a test double at the network boundary
only; application code is never handed a substitute (with no
``COPILOT_REDIS_URL`` the store keeps real history in memory and *says* so).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import fakeredis.aioredis as fakeredis_asyncio
import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app.database import engine as db_engine
from app.event_intelligence import api_router as ei_api
from app.event_intelligence.feature_store import (
    FeatureStore,
    FeatureStoreUnavailable,
)


class _BrokenRedis:
    """A client whose every command fails, as an unreachable server would."""

    def __getattr__(self, name: str) -> Any:
        async def _fail(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError(f"connection refused during {name.upper()}")

        return _fail


@pytest.fixture
def redis_client() -> Iterator[Any]:
    client = fakeredis_asyncio.FakeRedis()
    yield client


@pytest.fixture(autouse=True)
def _fresh_stores(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A clean per-org registry, and a sqlite DSN that does not outlive the test.

    Setting COPILOT_DATABASE_URL at import time leaks a sqlite DSN into the rest
    of the pytest session; tests/integration/conftest.py reads that variable to
    decide a real database is available and hands it straight to asyncpg.
    """
    monkeypatch.setenv(
        "COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/event_intelligence.db"
    )
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    ei_api._feature_stores.clear()
    ei_api._noise_engines.clear()
    ei_api._redis_clients.clear()
    yield
    ei_api._feature_stores.clear()
    ei_api._noise_engines.clear()
    ei_api._redis_clients.clear()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def client(_fresh_stores: None) -> Iterator[TestClient]:
    from app.api import main as api

    with TestClient(api.app) as c:
        yield c


# ---------------------------------------------------------------------------
# Persistence: a second instance must see the first one's writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recorded_history_survives_a_new_store_instance(redis_client: Any) -> None:
    """The whole point of the Redis branch: a restarted worker sees the data.

    Before, every read came from ``self._metric_history`` - a per-instance
    defaultdict - so a fresh instance saw an empty store no matter what Redis
    held.
    """
    writer = FeatureStore(redis_client=redis_client, namespace="acme")
    for value in (1.0, 1.2, 0.9):
        await writer.record_metric("checkout", "error_rate", value)
    await writer.record_alert(
        "fp1", "checkout", "error_rate", "warning",
        resolved=True, auto_resolved=True,
    )
    await writer.record_deployment("checkout")

    reader = FeatureStore(redis_client=redis_client, namespace="acme")
    assert reader._metric_history == {}  # nothing in this process's memory

    features = await reader.get_features("fp1", "checkout", "error_rate")

    assert features.hourly_sample_count == 3
    assert features.hourly_avg == pytest.approx((1.0 + 1.2 + 0.9) / 3)
    assert features.hourly_stddev > 0
    assert features.alert_count_1h == 1
    assert features.auto_resolve_rate_7d == 1.0
    assert features.service_alert_count_1h == 1
    assert features.deployment_in_progress is True


@pytest.mark.asyncio
async def test_maintenance_windows_survive_a_new_store_instance(redis_client: Any) -> None:
    now = datetime.now(timezone.utc)
    writer = FeatureStore(redis_client=redis_client, namespace="acme")
    await writer.add_maintenance_window(
        ["checkout"], now - timedelta(minutes=5), now + timedelta(minutes=30)
    )

    reader = FeatureStore(redis_client=redis_client, namespace="acme")
    assert await reader.is_in_maintenance("checkout") is True
    assert await reader.is_in_maintenance("payments") is False


@pytest.mark.asyncio
async def test_a_window_that_has_not_started_is_not_active(redis_client: Any) -> None:
    now = datetime.now(timezone.utc)
    store = FeatureStore(redis_client=redis_client, namespace="acme")
    await store.add_maintenance_window(
        ["checkout"], now + timedelta(hours=1), now + timedelta(hours=2)
    )

    assert await store.is_in_maintenance("checkout") is False


@pytest.mark.asyncio
async def test_naive_maintenance_bounds_are_read_as_utc(redis_client: Any) -> None:
    """Comparing a naive datetime against an aware ``now()`` raised TypeError,
    which surfaced from ``GET /maintenance/{service}`` as a 500."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for store in (
        FeatureStore(namespace="acme"),
        FeatureStore(redis_client=redis_client, namespace="acme"),
    ):
        await store.add_maintenance_window(
            ["checkout"], now - timedelta(minutes=5), now + timedelta(minutes=30)
        )
        assert await store.is_in_maintenance("checkout") is True


@pytest.mark.asyncio
async def test_redis_state_is_namespaced_per_org(redis_client: Any) -> None:
    """One Redis, many tenants: the org has to be in the key, or one org's
    history becomes the baseline another org's alerts are scored against."""
    acme = FeatureStore(redis_client=redis_client, namespace="acme")
    umbrella = FeatureStore(redis_client=redis_client, namespace="umbrella")

    await acme.record_metric("checkout", "error_rate", 1.0)
    await acme.record_alert("fp1", "checkout", "error_rate", "warning")
    await acme.record_deployment("checkout")

    theirs = await umbrella.get_features("fp1", "checkout", "error_rate")
    assert theirs.hourly_sample_count == 0
    assert theirs.alert_count_1h == 0
    assert theirs.service_alert_count_1h == 0
    assert theirs.deployment_in_progress is False

    stats = await umbrella.get_stats()
    assert stats["metric_keys"] == 0
    assert stats["total_metrics"] == 0

    keys = sorted(k.decode() for k in await redis_client.keys("*"))
    assert keys and all(k.startswith("ei:acme:") for k in keys), keys


@pytest.mark.asyncio
async def test_two_identical_samples_at_the_same_instant_are_two_points(
    redis_client: Any,
) -> None:
    """A sorted-set member is unique, so ``f"{ts}:{value}"`` silently collapsed
    duplicate readings into one and skewed every statistic computed from them."""
    ts = datetime.now(timezone.utc)
    store = FeatureStore(redis_client=redis_client, namespace="acme")

    await store.record_metric("checkout", "error_rate", 1.0, timestamp=ts)
    await store.record_metric("checkout", "error_rate", 1.0, timestamp=ts)

    features = await store.get_features("fp", "checkout", "error_rate")
    assert features.hourly_sample_count == 2


@pytest.mark.asyncio
async def test_stats_and_storage_description_say_where_the_data_is(
    redis_client: Any,
) -> None:
    memory = FeatureStore(namespace="acme")
    assert memory.is_durable is False
    assert "COPILOT_REDIS_URL" in memory.storage_description
    assert (await memory.get_stats())["durable"] is False

    durable = FeatureStore(redis_client=redis_client, namespace="acme")
    assert durable.is_durable is True
    assert "redis" in durable.storage_description
    assert (await durable.get_stats())["durable"] is True


# ---------------------------------------------------------------------------
# A failed write is never reported as a success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failing_redis_command_raises_instead_of_returning_success() -> None:
    store = FeatureStore(redis_client=_BrokenRedis(), namespace="acme")

    with pytest.raises(FeatureStoreUnavailable) as exc:
        await store.record_metric("checkout", "error_rate", 1.0)
    assert "COPILOT_REDIS_URL" in str(exc.value)

    with pytest.raises(FeatureStoreUnavailable):
        await store.record_alert("fp1", "checkout", "error_rate", "warning")

    with pytest.raises(FeatureStoreUnavailable):
        await store.get_features("fp1", "checkout", "error_rate")


def test_storage_failure_is_a_503_naming_the_backend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ei_api, "_feature_store_redis", lambda: _BrokenRedis())
    ei_api._feature_stores.clear()

    r = client.post(
        "/api/event-intelligence/features/record-metric",
        json={"service": "checkout", "metric_name": "error_rate", "value": 1.0},
    )

    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "feature_store_unavailable"
    assert "COPILOT_REDIS_URL" in detail["message"]


# ---------------------------------------------------------------------------
# HTTP: writes say where they landed, and Redis is actually used when set
# ---------------------------------------------------------------------------

def test_write_responses_admit_they_are_process_local(client: TestClient) -> None:
    r = client.post(
        "/api/event-intelligence/features/record-metric",
        json={"service": "checkout", "metric_name": "error_rate", "value": 1.0},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "recorded"
    assert body["durable"] is False
    assert "COPILOT_REDIS_URL" in body["storage"]


def test_writes_go_to_redis_when_it_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, redis_client: Any
) -> None:
    monkeypatch.setattr(ei_api, "_feature_store_redis", lambda: redis_client)
    ei_api._feature_stores.clear()

    r = client.post(
        "/api/event-intelligence/features/record-metric",
        json={"service": "checkout", "metric_name": "error_rate", "value": 1.0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["durable"] is True

    # Drop every in-process store, as a restart would, and read again.
    ei_api._feature_stores.clear()
    r = client.get("/api/event-intelligence/features/checkout/error_rate")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sample_counts"]["hourly"] == 1
    assert body["durable"] is True

    stats = client.get("/api/event-intelligence/stats").json()["feature_store"]
    assert stats["total_metrics"] == 1
    assert stats["durable"] is True


# ---------------------------------------------------------------------------
# /noise/check has to consume the history it computes
# ---------------------------------------------------------------------------

def _record_baseline(client: TestClient, values: list[float]) -> None:
    for value in values:
        r = client.post(
            "/api/event-intelligence/features/record-metric",
            json={"service": "checkout", "metric_name": "error_rate", "value": value},
        )
        assert r.status_code == 200, r.text


def test_noise_check_uses_the_recorded_metric_history(client: TestClient) -> None:
    """With a real baseline the engine's statistical strategy has to fire.

    ``/noise/check`` never called ``get_features()``, so ``hourly_stddev`` was
    ``None`` on every HTTP request and ``_check_statistical_anomaly`` returned
    on its first line - the strategy was unreachable over HTTP.
    """
    _record_baseline(client, [1.0, 1.2, 0.9, 1.1, 1.05])

    r = client.post(
        "/api/event-intelligence/noise/check",
        json={
            "alert_id": "a1",
            "source": "prometheus",
            "service": "checkout",
            "metric_name": "error_rate",
            "value": 1.1,
            "threshold": 0.5,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["history"]["metric_samples_1h"] == 5
    assert body["history"]["statistical_check_applied"] is True
    assert body["strategy_used"] == "statistical"
    assert body["is_noise"] is True
    assert "z-score" in body["reasoning"]


def test_noise_check_does_not_invent_a_baseline_from_no_history(
    client: TestClient,
) -> None:
    """A mean of 0.0 over zero samples is not a baseline, and a z-score against
    it would be a verdict manufactured from missing data."""
    r = client.post(
        "/api/event-intelligence/noise/check",
        json={
            "alert_id": "a2",
            "source": "prometheus",
            "service": "never-seen",
            "metric_name": "error_rate",
            "value": 99.0,
            "threshold": 0.5,
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["history"]["metric_samples_1h"] == 0
    assert body["history"]["statistical_check_applied"] is False
    assert body["strategy_used"] != "statistical"


def test_noise_check_sees_recorded_alert_history(client: TestClient) -> None:
    """Alert-derived features (flapping, service volume) also have to arrive."""
    probe = client.post(
        "/api/event-intelligence/noise/check",
        json={
            "alert_id": "probe",
            "source": "prometheus",
            "service": "checkout",
            "metric_name": "error_rate",
            "value": 1.0,
            "threshold": 0.5,
        },
    )
    fingerprint = probe.json()["history"]["fingerprint"]

    for i in range(6):
        r = client.post(
            "/api/event-intelligence/features/record-alert",
            json={
                "fingerprint": fingerprint,
                "service": "checkout",
                "metric_name": "error_rate",
                "severity": "warning",
                "resolved": True,
                "auto_resolved": True,
                "resolution_time_seconds": 30,
            },
        )
        assert r.status_code == 200, r.text

    r = client.post(
        "/api/event-intelligence/noise/check",
        json={
            "alert_id": "a3",
            "source": "prometheus",
            "service": "checkout",
            "metric_name": "error_rate",
            "value": 1.0,
            "threshold": 0.5,
        },
    )

    body = r.json()
    assert body["history"]["flap_count_1h"] == 6
    assert body["history"]["service_alert_count_1h"] == 6
    assert body["history"]["auto_resolve_rate_7d"] == 1.0


# ---------------------------------------------------------------------------
# Maintenance-window input validation
# ---------------------------------------------------------------------------

def test_unparseable_maintenance_timestamp_is_422_not_500(client: TestClient) -> None:
    r = client.post(
        "/api/event-intelligence/maintenance/add",
        json={
            "services": ["checkout"],
            "start_iso": "not-a-date",
            "end_iso": "2026-01-01T00:00:00Z",
        },
    )

    assert r.status_code == 422, r.text
    assert "ISO-8601" in r.json()["detail"]


def test_backwards_maintenance_window_is_rejected(client: TestClient) -> None:
    r = client.post(
        "/api/event-intelligence/maintenance/add",
        json={
            "services": ["checkout"],
            "start_iso": "2026-01-02T00:00:00Z",
            "end_iso": "2026-01-01T00:00:00Z",
        },
    )

    assert r.status_code == 422, r.text
    assert "after start_iso" in r.json()["detail"]


def test_maintenance_window_round_trips_over_http(client: TestClient) -> None:
    now = datetime.now(timezone.utc)
    r = client.post(
        "/api/event-intelligence/maintenance/add",
        json={
            "services": ["checkout"],
            "start_iso": (now - timedelta(minutes=5)).isoformat(),
            "end_iso": (now + timedelta(minutes=30)).isoformat(),
            "description": "index rebuild",
        },
    )
    assert r.status_code == 200, r.text

    assert client.get(
        "/api/event-intelligence/maintenance/checkout"
    ).json()["in_maintenance"] is True
    assert client.get(
        "/api/event-intelligence/maintenance/payments"
    ).json()["in_maintenance"] is False


def test_deployment_markers_round_trip_over_http(client: TestClient) -> None:
    assert client.get(
        "/api/event-intelligence/deployments/checkout"
    ).json()["in_progress"] is False

    client.post(
        "/api/event-intelligence/deployments/start", json={"service": "checkout"}
    )
    assert client.get(
        "/api/event-intelligence/deployments/checkout"
    ).json()["in_progress"] is True

    client.post(
        "/api/event-intelligence/deployments/end", json={"service": "checkout"}
    )
    assert client.get(
        "/api/event-intelligence/deployments/checkout"
    ).json()["in_progress"] is False
