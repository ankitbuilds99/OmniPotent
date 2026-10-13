from app.safety_preflight import ActionIntent, ResourceSnapshot, validate_rollback_preflight


def _pair(**changes):
    current = ResourceSnapshot(
        uid="uid-1", resource_version="rv-1", namespace="prod",
        name="checkout", kind="Deployment", controller="argocd",
        current_revision=9, available_revisions=(8, 9),
    )
    intent = ActionIntent(
        uid="uid-1", resource_version="rv-1", namespace="prod",
        name="checkout", kind="Deployment", target_revision=8,
        expected_controller="argocd",
    )
    return intent, ResourceSnapshot(**{**current.__dict__, **changes})


def test_matching_snapshot_allows_rollback():
    intent, current = _pair()
    assert validate_rollback_preflight(intent, current).allowed


def test_concurrent_update_invalidates_intent():
    intent, current = _pair(resource_version="rv-2")
    result = validate_rollback_preflight(intent, current)
    assert result.allowed is False
    assert "resource_version_changed" in result.reasons


def test_gitops_controller_and_schema_mismatch_fail_closed():
    intent, current = _pair(controller="flux", schema_compatible=False)
    result = validate_rollback_preflight(intent, current)
    assert result.allowed is False
    assert set(result.reasons) >= {"controller_ownership_changed", "schema_incompatible"}
