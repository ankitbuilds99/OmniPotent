from app.release_gate import ReleaseEvidence, evaluate_release
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def test_release_gate_requires_all_safety_evidence():
    result = evaluate_release(ReleaseEvidence(0, 0, True, True, 2, True))
    assert result.allowed is True


def test_release_gate_fails_closed_on_any_missing_requirement():
    result = evaluate_release(ReleaseEvidence(0, 0, True, False, 2, True))
    assert result.allowed is False
    assert result.reasons == ("restore_drill_unverified",)


def test_release_gate_rejects_p0_and_safety_violations_even_with_signatures():
    result = evaluate_release(ReleaseEvidence(1, 1, True, True, 5, True))
    assert set(result.reasons) == {"outstanding_p0_requirements", "critical_safety_violations"}


def test_release_gate_rejects_an_undersized_rca_corpus():
    evidence = ReleaseEvidence(0, 0, True, True, 2, True, benchmark_corpus_ready=False)
    result = evaluate_release(evidence)
    assert result.reasons == ("benchmark_corpus_not_release_ready",)


def test_release_gate_requires_verified_artifacts_and_valid_digests():
    evidence = ReleaseEvidence(0, 0, True, True, 2, True,
                               required_artifacts=frozenset({"pilot-report", "restore-log"}),
                               verified_artifacts=frozenset({"pilot-report"}))
    result = evaluate_release(evidence)
    assert result.reasons == ("required_release_artifacts_missing",)

    evidence = ReleaseEvidence(0, 0, True, True, 2, True, artifact_digests_valid=False)
    assert evaluate_release(evidence).reasons == ("release_artifact_digest_invalid",)


def test_release_gate_requires_signatures_when_artifact_coverage_is_complete():
    artifacts = frozenset({"pilot-report", "restore-log"})
    evidence = ReleaseEvidence(
        0, 0, True, True, 2, True,
        required_artifacts=artifacts,
        verified_artifacts=artifacts,
        artifact_digests_valid=True,
    )
    assert evaluate_release(evidence).reasons == ("release_artifact_signature_invalid",)
    signed = ReleaseEvidence(
        0, 0, True, True, 2, True,
        required_artifacts=artifacts,
        verified_artifacts=artifacts,
        artifact_digests_valid=True,
        artifact_signatures_valid=True,
    )
    assert evaluate_release(signed).allowed is True


def test_release_gate_verifies_ed25519_signatures_over_artifact_digests():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    digest = "a" * 64
    signature = base64.urlsafe_b64encode(private.sign(digest.encode())).decode()
    evidence = ReleaseEvidence(
        0, 0, True, True, 2, True,
        required_artifacts=frozenset({"pilot"}), verified_artifacts=frozenset({"pilot"}),
        artifact_digests=(("pilot", digest),), artifact_signatures=(("pilot", signature),),
        artifact_signing_public_key=public,
    )
    assert evaluate_release(evidence).allowed is True
    tampered = ReleaseEvidence(
        0, 0, True, True, 2, True,
        required_artifacts=frozenset({"pilot"}), verified_artifacts=frozenset({"pilot"}),
        artifact_digests=(("pilot", "b" * 64),), artifact_signatures=(("pilot", signature),),
        artifact_signing_public_key=public,
    )
    assert evaluate_release(tampered).reasons == ("release_artifact_signature_invalid",)
