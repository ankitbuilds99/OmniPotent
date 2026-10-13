from scripts.prd_readiness import build_report


def test_prd_readiness_rejects_missing_evidence_manifest(tmp_path):
    report = build_report(tmp_path / "missing.json")
    assert report["external_gates"]
    assert "live_evidence_manifest_missing" in report["evidence_reasons"]


def test_prd_readiness_accepts_only_validated_gate_entries(tmp_path):
    import json

    artifact = tmp_path / "evidence.txt"
    artifact.write_text("independently captured evidence\n")
    import hashlib

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = tmp_path / "evidence.json"
    manifest.write_text(json.dumps({"gates": {
        gate: {"status": "passed", "artifact": artifact.name, "sha256": digest}
        for gate in (
            "customer_connector_permissions", "postgres_crash_restart_and_restore_drill",
            "production_redis_failover", "independent_30_case_rca_benchmark",
            "browser_acceptance_and_accessibility", "customer_runner_execution",
            "external_incident_provider_contract", "signed_pilot_artifacts_and_review",
        )
    }}))
    report = build_report(manifest)
    assert report["external_gates"] == []
    assert report["release_ready"] is False  # the independent corpus is still only four cases


def test_prd_readiness_rejects_digest_not_matching_artifact(tmp_path):
    import json

    artifact = tmp_path / "evidence.txt"
    artifact.write_text("captured evidence\n")
    manifest = tmp_path / "evidence.json"
    manifest.write_text(json.dumps({"gates": {
        "customer_connector_permissions": {
            "status": "passed", "artifact": artifact.name, "sha256": "a" * 64
        }
    }}))
    report = build_report(manifest)
    assert "customer_connector_permissions" in report["external_gates"]


def test_prd_readiness_reports_external_gates_and_small_corpus():
    report = build_report()
    assert report["schema"] == "omnipotent.prd-readiness.v1"
    assert report["rca_corpus"]["cases"] == 4
    assert report["rca_corpus"]["release_ready"] is False
    assert report["external_gates"]
    assert report["release_ready"] is False

