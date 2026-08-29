"""Tenant isolation of the continually-learned incident corpus.

The learned corpus used to be one process-wide JSON file with no org in its path,
so a root cause learned from org A's investigation was merged into every other
org's search results — and, via the planner's prior-incidents block, into their
LLM prompt. These tests pin the scoping: a learned record is readable by the org
that learned it and by nobody else, while single-tenant deployments keep the exact
file they had before.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app import incident_memory as mem
from app.config import get_settings
from app.tenant_context import TenantConfig, reset_tenant, set_tenant

_REPORT_A = {
    "summary": "acme payments outage",
    "root_cause": "acme secret rotation broke the stripe key in acme-pay",
    "affected_services": ["acme-pay"],
    "recommended_actions": ["Re-mint the stripe key"],
    "verification": {"verdict": "verified", "rationale": "sandbox proved fail->pass"},
    "abstained": False,
}
_QUERY_A = "stripe key rotation payments outage acme-pay"


@contextmanager
def _as_org(org_id: str | None):
    token = set_tenant(TenantConfig(org_id=org_id) if org_id else None)
    try:
        yield
    finally:
        reset_tenant(token)


@pytest.fixture
def multi_tenant(monkeypatch, tmp_path):
    """A multi-tenant deployment whose learned corpus lives under tmp_path."""
    settings = get_settings()
    monkeypatch.setattr(settings, "copilot_multi_tenant", True)
    monkeypatch.setattr(settings, "copilot_learn_incidents", True)
    monkeypatch.setattr(settings, "copilot_learned_corpus", str(tmp_path / "learned.json"))
    return tmp_path


def test_learned_incident_is_not_visible_to_another_org(multi_tenant):
    """The reproduction from the audit: org A learns, org B must not see it."""
    with _as_org("org-A"):
        record = mem.learn_from_report("why 500s?", _REPORT_A, date="2026-08-28")
        assert record is not None
        assert any(h["id"] == record["id"] for h in mem.search(_QUERY_A, limit=5))

    with _as_org("org-B"):
        ids = [h["id"] for h in mem.search(_QUERY_A, limit=5)]
        assert record["id"] not in ids
        assert all(not r.get("learned") for r in mem.load_corpus())
        # Nothing of org A's prose reaches org B, not just its id.
        assert not any("acme" in json.dumps(h).lower() for h in mem.search(_QUERY_A, limit=5))


def test_planner_prior_incidents_block_is_org_scoped(multi_tenant):
    """The live read path: what plan_node prepends to the planner's human message."""
    nodes = pytest.importorskip("app.graph.nodes")

    with _as_org("org-A"):
        assert mem.learn_from_report("why 500s?", _REPORT_A, date="2026-08-28") is not None
        assert "acme secret rotation" in nodes._prior_incidents_block(_QUERY_A)

    with _as_org("org-B"):
        assert "acme" not in nodes._prior_incidents_block(_QUERY_A).lower()


def test_each_org_gets_its_own_corpus_file(multi_tenant):
    with _as_org("org-A"):
        path_a = mem.learned_corpus_path()
    with _as_org("org-B"):
        path_b = mem.learned_corpus_path()
    assert path_a != path_b
    assert path_a.parent == path_b.parent == multi_tenant
    # Sanitising alone would collapse these two distinct org ids onto one file.
    with _as_org("acme/prod"):
        slashed = mem.learned_corpus_path()
    with _as_org("acme-prod"):
        dashed = mem.learned_corpus_path()
    assert slashed != dashed


def test_both_orgs_keep_their_own_copy_of_a_shared_failure_mode(multi_tenant):
    """Dedupe is per-org, so org B learning the same root cause is not suppressed
    by (nor does it inherit) org A's already-stored record."""
    with _as_org("org-A"):
        a = mem.learn_from_report("why 500s?", _REPORT_A, date="2026-08-28")
    with _as_org("org-B"):
        b = mem.learn_from_report("why 500s?", _REPORT_A, date="2026-08-28")
        assert b is not None and b["org"] == "org-B"
        assert [r["org"] for r in mem.load_corpus() if r.get("learned")] == ["org-B"]
    assert a is not None and a["org"] == "org-A"
    with _as_org("org-A"):
        assert [r["org"] for r in mem.load_corpus() if r.get("learned")] == ["org-A"]


def test_record_stamped_with_another_org_is_never_served(multi_tenant):
    """Second lock: even a corpus file that somehow holds a foreign record — a
    shared base path, a restored pre-scoping backup — does not serve it."""
    with _as_org("org-B"):
        target = mem.learned_corpus_path()
        foreign = mem.build_record("q", _REPORT_A, date="2026-08-28", seq=0)
        unowned = {**mem.build_record("q", _REPORT_A, date="2026-08-28", seq=1), "id": "LEGACY-1"}
        target.write_text(json.dumps([{**foreign, "org": "org-A"}, unowned]))
        ids = [r.get("id") for r in mem.load_corpus()]
        assert foreign["id"] not in ids
        assert "LEGACY-1" not in ids


def test_untenanted_call_refuses_to_learn_and_reads_only_the_bundled_corpus(
    multi_tenant, caplog
):
    """No resolved tenant in a multi-tenant deployment means no owner for the
    record; filing it in a shared bucket would recreate the leak."""
    with _as_org(None):
        assert mem.learned_corpus_path() is None
        with caplog.at_level("WARNING", logger="omnipotent.incident_memory"):
            assert mem.learn_from_report("why 500s?", _REPORT_A, date="2026-08-28") is None
        assert "no tenant is resolved" in caplog.text
        assert all(not r.get("learned") for r in mem.load_corpus())
    assert not list(multi_tenant.glob("learned*.json"))


def test_single_tenant_keeps_the_unscoped_path_and_still_learns(monkeypatch, tmp_path):
    """Multi-tenancy is opt-in; with it off there is one org, so the shared file
    is that org's file and an existing learned_incidents.json is not orphaned."""
    settings = get_settings()
    base = tmp_path / "learned.json"
    monkeypatch.setattr(settings, "copilot_multi_tenant", False)
    monkeypatch.setattr(settings, "copilot_learn_incidents", True)
    monkeypatch.setattr(settings, "copilot_learned_corpus", str(base))

    with _as_org(None):
        record = mem.learn_from_report("why 500s?", _REPORT_A, date="2026-08-28")
    assert mem.learned_corpus_path() == base
    assert record is not None and "org" not in record
    assert base.exists()
    # Readable from any context, including one that carries a tenant.
    with _as_org("org-A"):
        assert any(h["id"] == record["id"] for h in mem.search(_QUERY_A, limit=5))
