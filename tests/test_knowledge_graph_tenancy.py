"""The knowledge graph must not be shared across orgs, and must not be an SSRF.

Two defects this covers:

  * `get_graph()` returned one process-wide `Neo4jKnowledgeGraph`. Without a
    neo4j driver — the default, since `app/config.py` defines no neo4j settings
    at all — that object holds the whole graph in a plain dict, so every org
    read and wrote the same services, incidents, changes and alerts.
  * `POST /sync/configure` took `prometheus_url` / `alertmanager_url` /
    `jaeger_url` straight from the request body and later fetched them from
    inside the deployment network, giving any authenticated caller a way to
    reach internal services and the cloud metadata endpoint.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.knowledge_graph import api_router as kg
from app.tenant_context import TenantConfig, reset_tenant, set_tenant


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # A fresh registry per test: instances are cached per org for the life of
    # the process, and these tests create services inside them.
    monkeypatch.setattr(kg, "_GRAPHS", kg.scoped(kg._OrgGraph))
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _as_org(org: str):
    return set_tenant(TenantConfig(org_id=org))


async def _graph_for(org: str):
    token = _as_org(org)
    try:
        return await kg.get_graph()
    finally:
        reset_tenant(token)


@pytest.mark.asyncio
async def test_each_org_gets_its_own_graph() -> None:
    acme = await _graph_for("acme")
    globex = await _graph_for("globex")
    assert acme is not globex
    # Same org twice is the same instance, or nothing would persist per request.
    assert acme is await _graph_for("acme")


@pytest.mark.asyncio
async def test_services_do_not_cross_orgs() -> None:
    from app.knowledge_graph.neo4j_backend import ServiceNode

    acme = await _graph_for("acme")
    await acme.create_service(ServiceNode(id="checkout", name="checkout"))

    globex = await _graph_for("globex")
    assert await globex.get_service("checkout") is None
    assert await acme.get_service("checkout") is not None


def test_sync_source_url_is_rejected_by_default(client: TestClient) -> None:
    response = client.post(
        "/api/knowledge-graph/sync/configure",
        json={
            "source": "prometheus",
            "config": {"prometheus_url": "http://169.254.169.254/latest/meta-data/"},
        },
    )
    assert response.status_code == 403
    assert "COPILOT_SYNC_ALLOWED_HOSTS" in response.json()["detail"]


def test_sync_source_url_is_accepted_when_allow_listed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPILOT_SYNC_ALLOWED_HOSTS", "prometheus.internal:9090")
    get_settings.cache_clear()

    allowed = client.post(
        "/api/knowledge-graph/sync/configure",
        json={
            "source": "prometheus",
            "config": {
                "prometheus_url": "http://prometheus.internal:9090",
                # An allow-listed host on any port: the entry names a port, so
                # the bare host must not match it.
                "alertmanager_url": "http://prometheus.internal:9090/am",
            },
        },
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "configured"

    # A host that is not on the list stays blocked even once the list exists.
    denied = client.post(
        "/api/knowledge-graph/sync/configure",
        json={
            "source": "jaeger",
            "config": {"jaeger_url": "http://169.254.169.254/"},
        },
    )
    assert denied.status_code == 403
