from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.operator_annotations_api import router, _histories, _memory
from app.tenant_context import TenantConfig, reset_tenant, set_tenant


def test_annotation_chain_and_confirmed_memory_are_distinguishable():
    app = FastAPI()
    app.include_router(router)
    _histories.clear()
    _memory.clear()
    client = TestClient(app)
    incident = "00000000-0000-0000-0000-000000000123"

    created = client.post(f"/incidents/{incident}/annotations", json={
        "field": "root_cause", "value": "cache stampede", "author": "oncall@example.com",
    })
    assert created.status_code == 201
    digest = created.json()["digest"]
    listed = client.get(f"/incidents/{incident}/annotations").json()
    assert listed["chain_valid"] is True
    assert listed["annotations"][0]["kind"] == "operator_annotation"

    promoted = client.post(f"/incidents/{incident}/memory/promote", json={
        "field": "root_cause", "author": "reviewer@example.com", "confirmation": "verified in logs",
    })
    assert promoted.status_code == 200
    assert promoted.json()["source_digest"] == digest
    assert promoted.json()["kind"] == "confirmed_memory"


def test_memory_promotion_requires_an_existing_annotation():
    app = FastAPI()
    app.include_router(router)
    _histories.clear()
    _memory.clear()
    client = TestClient(app)
    response = client.post(
        "/incidents/00000000-0000-0000-0000-000000000124/memory/promote",
        json={"field": "root_cause", "author": "reviewer", "confirmation": "checked"},
    )
    assert response.status_code == 404


def test_fallback_annotation_history_is_tenant_isolated(monkeypatch):
    import app.operator_annotations_api as annotations_api

    monkeypatch.setattr(annotations_api, "_database_ready", lambda: False)
    app = FastAPI()
    app.include_router(router)
    _histories.clear()
    _memory.clear()
    client = TestClient(app)
    incident = "00000000-0000-0000-0000-000000000125"
    token_a = set_tenant(TenantConfig(org_id="org-a"))
    try:
        assert client.post(f"/incidents/{incident}/annotations", json={
            "field": "root_cause", "value": "tenant-a finding", "author": "a",
        }).status_code == 201
    finally:
        reset_tenant(token_a)
    token_b = set_tenant(TenantConfig(org_id="org-b"))
    try:
        assert client.get(f"/incidents/{incident}/annotations").json()["annotations"] == []
        assert client.post(f"/incidents/{incident}/memory/promote", json={
            "field": "root_cause", "author": "b", "confirmation": "checked",
        }).status_code == 404
    finally:
        reset_tenant(token_b)
