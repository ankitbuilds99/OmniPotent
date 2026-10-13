from datetime import datetime, timedelta, timezone

from app.customer_runner import RunnerCapability, RunnerRequest, validate_capability


def _pair(**changes):
    values = dict(
        token_id="t1", tenant_id="org-a", cluster="prod-1", namespace="payments",
        resource_kind="Deployment", resource_name="checkout", action="rollback",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), action_digest="abc",
    )
    capability = RunnerCapability(**{**values, **changes})
    request = RunnerRequest(
        tenant_id="org-a", cluster="prod-1", namespace="payments",
        resource_kind="Deployment", resource_name="checkout", action="rollback", action_digest="abc",
    )
    return capability, request


def test_runner_accepts_exact_scoped_capability():
    capability, request = _pair()
    assert validate_capability(capability, request) == (True, "allowed")


def test_runner_rejects_tenant_and_resource_drift():
    capability, request = _pair(tenant_id="org-b")
    assert validate_capability(capability, request) == (False, "tenant_id_mismatch")


def test_runner_rejects_expired_capability():
    capability, request = _pair(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert validate_capability(capability, request) == (False, "capability_expired")
