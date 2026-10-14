from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.policy import router
from app.mutation_freeze import reset


def test_mutation_freeze_api_controls_tenant_scope(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr("app.api.v1.policy.storage_org_id", lambda: "tenant-a")
    reset()
    client = TestClient(app)
    try:
        response = client.post("/policy/mutation-freeze", json={})
        assert response.status_code == 200
        assert client.get("/policy/mutation-freeze").json()["frozen"] is True

        response = client.request("DELETE", "/policy/mutation-freeze", json={})
        assert response.status_code == 200
        assert client.get("/policy/mutation-freeze").json()["frozen"] is False
    finally:
        reset()
