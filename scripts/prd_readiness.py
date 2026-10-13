"""Emit a machine-readable PRD implementation/release-readiness summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Make direct execution (`python scripts/prd_readiness.py`) behave like
# module execution from the repository root.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.benchmark import validate_release_corpus  # noqa: E402

STATUS = ROOT / "docs" / "PRD_REQUIREMENTS_STATUS.md"
CASES = ROOT / "evals" / "benchmark_cases.yaml"
LIVE_GATES = (
    "customer_connector_permissions",
    "postgres_crash_restart_and_restore_drill",
    "production_redis_failover",
    "independent_30_case_rca_benchmark",
    "browser_acceptance_and_accessibility",
    "customer_runner_execution",
    "external_incident_provider_contract",
    "signed_pilot_artifacts_and_review",
)


def _verified_gates(evidence_path: Path | None) -> tuple[list[str], list[str]]:
    if evidence_path is None or not evidence_path.exists():
        return list(LIVE_GATES), ["live_evidence_manifest_missing"]
    try:
        manifest = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return list(LIVE_GATES), ["live_evidence_manifest_invalid"]
    entries = manifest.get("gates") if isinstance(manifest, dict) else None
    if not isinstance(entries, dict):
        return list(LIVE_GATES), ["live_evidence_manifest_invalid"]
    unresolved: list[str] = []
    reasons: list[str] = []
    for gate in LIVE_GATES:
        entry = entries.get(gate)
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        artifact = entry.get("artifact") if isinstance(entry, dict) else None
        artifact_path = Path(artifact) if isinstance(artifact, str) else None
        if artifact_path is not None and not artifact_path.is_absolute():
            artifact_path = evidence_path.parent / artifact_path
        actual_digest = None
        if artifact_path is not None and artifact_path.is_file():
            actual_digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if (
            not isinstance(entry, dict)
            or entry.get("status") != "passed"
            or not isinstance(digest, str)
            or len(digest) != 64
            or actual_digest != digest.lower()
        ):
            unresolved.append(gate)
            reasons.append(f"invalid_or_missing_evidence:{gate}")
    return unresolved, reasons


def build_report(evidence_path: Path | None = None) -> dict:
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    corpus_ready, corpus_reasons = validate_release_corpus(cases)
    status_text = STATUS.read_text(encoding="utf-8")
    partial = status_text.count("| Implemented in part |")
    implemented = status_text.count("| Implemented |")
    live_gates, evidence_reasons = _verified_gates(evidence_path)
    return {
        "schema": "omnipotent.prd-readiness.v1",
        "requirements": {"implemented": implemented, "implemented_in_part": partial},
        "rca_corpus": {
            "cases": len(cases),
            "release_ready": corpus_ready,
            "reasons": list(corpus_reasons),
        },
        "external_gates": live_gates,
        "evidence_manifest": str(evidence_path) if evidence_path else None,
        "evidence_reasons": evidence_reasons,
        "release_ready": corpus_ready and not live_gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evidence", type=Path, help="validated JSON evidence manifest")
    args = parser.parse_args()
    report = build_report(args.evidence)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

