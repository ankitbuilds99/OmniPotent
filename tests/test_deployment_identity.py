from app.deployment_identity import validate_deployment_identity


def _evidence():
    return {
        "service": "checkout",
        "image": "registry.example/checkout@sha256:" + "a" * 64,
        "commit_sha": "b" * 40,
        "source": "argocd",
    }


def test_deployment_identity_accepts_immutable_image_and_commit():
    result = validate_deployment_identity(_evidence(), expected_service="checkout")
    assert result.verified is True
    assert result.reasons == ()


def test_deployment_identity_rejects_tag_and_missing_source():
    result = validate_deployment_identity({"service": "checkout", "image": "checkout:latest"})
    assert result.verified is False
    assert set(result.reasons) == {
        "immutable_image_digest_missing", "commit_sha_invalid", "identity_source_missing",
    }


def test_deployment_identity_rejects_expected_binding_mismatch():
    result = validate_deployment_identity(
        _evidence(), expected_digest="sha256:" + "c" * 64, expected_commit="d" * 40,
    )
    assert result.verified is False
    assert {"image_digest_mismatch", "commit_sha_mismatch"} <= set(result.reasons)

