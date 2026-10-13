from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.customer_runner_service import create_runner_app


def _payload(**changes):
    value = {
        "token_id": "token-1", "tenant_id": "tenant-a", "cluster": "cluster-a",
        "namespace": "prod", "resource_kind": "Deployment", "resource_name": "api",
        "action": "rollback", "action_digest": "sha256:abc",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    }
    value.update(changes)
    return value


def _request(**changes):
    value = {
        "tenant_id": "tenant-a", "cluster": "cluster-a", "namespace": "prod",
        "resource_kind": "Deployment", "resource_name": "api", "action": "rollback",
        "action_digest": "sha256:abc",
    }
    value.update(changes)
    return value


def test_runner_health_fails_closed_without_executor():
    response = TestClient(create_runner_app()).get("/health")
    assert response.json()["status"] == "degraded"
    assert response.json()["ready"] is False
    assert response.json()["executor_configured"] is False


def test_runner_health_is_ready_with_executor():
    class Executor:
        def execute(self, request):
            return {"action": request.action}

    response = TestClient(create_runner_app(Executor())).get("/health")
    assert response.json()["status"] == "ok"
    assert response.json()["ready"] is True


def test_runner_rejects_scope_drift_before_execution():
    response = TestClient(create_runner_app()).post(
        "/v1/execute", json={"capability": _payload(), "request": _request(namespace="staging")}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "namespace_mismatch"


def test_runner_does_not_claim_execution_without_customer_executor():
    response = TestClient(create_runner_app()).post(
        "/v1/execute", json={"capability": _payload(), "request": _request()}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "customer_executor_not_configured"


def test_runner_executes_only_after_exact_capability_match():
    class Executor:
        def execute(self, request):
            return {"action": request.action, "resource": request.resource_name}

    response = TestClient(create_runner_app(Executor())).post(
        "/v1/execute", json={"capability": _payload(), "request": _request()}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
