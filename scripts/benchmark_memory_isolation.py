"""Deterministic tenant-isolation benchmark for verified incident memory."""

from __future__ import annotations

import argparse
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app import incident_memory
from app.config import get_settings
from app.tenant_context import TenantConfig, reset_tenant, set_tenant


@contextmanager
def _tenant(org: str):
    token = set_tenant(TenantConfig(org_id=org))
    try:
        yield
    finally:
        reset_tenant(token)


def run(org_count: int = 25) -> dict:
    if org_count < 2:
        raise ValueError("org_count must be at least 2")
    settings = get_settings()
    previous = (
        settings.copilot_multi_tenant,
        settings.copilot_learn_incidents,
        settings.copilot_learned_corpus,
    )
    leaks: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="omnipotent-memory-") as directory:
        settings.copilot_multi_tenant = True
        settings.copilot_learn_incidents = True
        settings.copilot_learned_corpus = str(Path(directory) / "learned.json")
        owners = [f"benchmark-org-{index}" for index in range(org_count)]
        for owner in owners:
            with _tenant(owner):
                incident_memory.learn_from_report(
                    f"query-{owner}",
                    {
                        "summary": f"summary-{owner}",
                        "root_cause": f"root-cause-{owner}",
                        "affected_services": [f"service-{owner}"],
                        "recommended_actions": [f"action-{owner}"],
                        "verification": {"verdict": "verified"},
                        "abstained": False,
                    },
                    date="2026-01-01",
                )
        for owner in owners:
            with _tenant(owner):
                expected = incident_memory.search(f"root-cause-{owner}", limit=100)
                if not any(owner in json.dumps(item) for item in expected):
                    leaks.append({"owner": owner, "reason": "own_memory_missing"})
                for other in owners:
                    if other == owner:
                        continue
                    foreign = incident_memory.search(f"root-cause-{other}", limit=100)
                    if any(other in json.dumps(item) for item in foreign):
                        leaks.append({"owner": owner, "foreign": other})
        result = {
            "schema": "omnipotent.memory-isolation-benchmark.v1",
            "organizations": org_count,
            "records_written": org_count,
            "leaks": leaks,
            "isolation_preserved": not leaks,
        }
    (
        settings.copilot_multi_tenant,
        settings.copilot_learn_incidents,
        settings.copilot_learned_corpus,
    ) = previous
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organizations", type=int, default=25)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = run(args.organizations)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["isolation_preserved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

