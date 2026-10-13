"""Run the customer-readiness acceptance preflight without fabricating passes.

The script checks whether the environment contains the prerequisites for live
acceptance. It deliberately does not treat local mocks, placeholders, or
missing credentials as successful external verification.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_GATES = {
    "customer_connector_permissions": "CUSTOMER_ACCEPTANCE_CONNECTOR_EVIDENCE",
    "postgres_crash_restart_and_restore_drill": "CUSTOMER_ACCEPTANCE_POSTGRES_EVIDENCE",
    "production_redis_failover": "CUSTOMER_ACCEPTANCE_REDIS_EVIDENCE",
    "independent_30_case_rca_benchmark": "CUSTOMER_ACCEPTANCE_RCA_EVIDENCE",
    "browser_acceptance_and_accessibility": "CUSTOMER_ACCEPTANCE_BROWSER_EVIDENCE",
    "customer_runner_execution": "CUSTOMER_ACCEPTANCE_RUNNER_EVIDENCE",
    "external_incident_provider_contract": "CUSTOMER_ACCEPTANCE_PROVIDER_EVIDENCE",
    "signed_pilot_artifacts_and_review": "CUSTOMER_ACCEPTANCE_PILOT_EVIDENCE",
}


def build_report() -> dict:
    gates = {}
    for gate, variable in REQUIRED_GATES.items():
        value = os.environ.get(variable, "").strip()
        path = Path(value) if value else None
        gates[gate] = {
            "evidence_variable": variable,
            "artifact": value or None,
            "available": bool(path and path.is_file()),
        }
    return {
        "schema": "omnipotent.customer-acceptance-preflight.v1",
        "repository": str(ROOT),
        "required_gates": gates,
        "ready_to_run": all(item["available"] for item in gates.values()),
        "instruction": (
            "Provide independently captured evidence artifacts through the listed "
            "environment variables; missing artifacts remain blocked."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["ready_to_run"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
