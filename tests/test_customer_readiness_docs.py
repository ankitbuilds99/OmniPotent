from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_customer_readiness_audit_deliverables_exist():
    required = {
        "docs/customer_readiness/01_repo_audit.md": ("Baseline", "Release decision"),
        "docs/customer_readiness/02_capability_matrix.md": ("Prompt", "Current release classes"),
        "docs/customer_readiness/03_end_to_end_trace.md": ("Read-only investigation path", "Missing execution evidence"),
        "docs/product/customer_journeys.md": ("First-time customer to first evidence", "State display contract"),
        "docs/product/route_map.md": ("Route and contract map", "Durable entities"),
        "docs/adr/0006-customer-readiness-build-vs-reuse.md": ("Decision", "Consequences"),
    }

    for relative_path, markers in required.items():
        document = (ROOT / relative_path).read_text(encoding="utf-8")
        assert all(marker in document for marker in markers), relative_path
