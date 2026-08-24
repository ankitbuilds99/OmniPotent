"""The knowledge base survives a restart, and one org cannot read another's.

Every document lived in `KnowledgeBase._documents`, a dict on a per-org object
in process memory, and the API router mutated that dict directly. Runbook
write-ups, postmortems and architecture notes vanished on restart, and with more
than one worker whether a document existed depended on which process answered.
These tests run the same contract against the `knowledge_documents` table.
"""

from __future__ import annotations

import asyncio

import pytest

import app.config as cfg
from app import tenant_context
from app.database import engine as db_engine
from app.knowledge_base import store
from app.knowledge_base.rag import Document, DocumentType


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/app.db")
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)

    asyncio.run(db_engine.init_db())
    yield

    asyncio.run(db_engine.close_db())
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    tenant_context.current_tenant.set(None)


def _as(org: str, coro_factory):
    """Run one coroutine as `org`, on its own loop, like a separate request."""

    async def run():
        token = tenant_context.set_tenant(tenant_context.TenantConfig(org_id=org))
        try:
            return await coro_factory()
        finally:
            tenant_context.reset_tenant(token)

    return asyncio.run(run())


def _doc(title: str = "Redis eviction storm", **kwargs) -> Document:
    return Document(
        id=kwargs.pop("id", ""),
        title=title,
        content=kwargs.pop("content", "Redis evicted the session keys under memory pressure."),
        doc_type=kwargs.pop("doc_type", DocumentType.POSTMORTEM),
        tags=kwargs.pop("tags", ["redis", "cache"]),
        service=kwargs.pop("service", "sessions"),
        author=kwargs.pop("author", "Ankit"),
        **kwargs,
    )


def test_a_created_document_is_readable_after_a_restart(db):
    doc_id = _as("acme", lambda: store.create_document(_doc()))

    # Nothing in this process carries over — the next read is a fresh request
    # against the database, which is what a restarted worker does.
    found = _as("acme", lambda: store.get_document(doc_id))

    assert found is not None
    assert found.title == "Redis eviction storm"
    assert found.doc_type is DocumentType.POSTMORTEM
    assert found.tags == ["redis", "cache"]
    assert found.author == "Ankit"


def test_every_org_gets_its_own_copy_of_the_built_ins(db):
    acme, _ = _as("acme", lambda: store.list_documents())
    umbrella, _ = _as("umbrella", lambda: store.list_documents())

    assert {d.id for d in acme} == {"kb-001", "kb-002", "kb-003", "kb-004", "kb-005"}
    assert {d.id for d in umbrella} == {d.id for d in acme}


def test_a_deleted_built_in_stays_deleted_and_only_for_that_org(db):
    assert _as("acme", lambda: store.delete_document("kb-001")) is True

    remaining, total = _as("acme", lambda: store.list_documents())
    assert "kb-001" not in {d.id for d in remaining}
    assert total == 4

    # The org next door still has its own copy.
    assert _as("umbrella", lambda: store.get_document("kb-001")) is not None


def test_deleting_every_document_does_not_reseed(db):
    for doc_id in ("kb-001", "kb-002", "kb-003", "kb-004", "kb-005"):
        _as("acme", lambda d=doc_id: store.delete_document(d))

    docs, total = _as("acme", lambda: store.list_documents())
    assert docs == [] and total == 0


def test_one_orgs_document_is_invisible_to_another(db):
    doc_id = _as("acme", lambda: store.create_document(_doc()))

    assert _as("umbrella", lambda: store.get_document(doc_id)) is None
    assert _as("umbrella", lambda: store.delete_document(doc_id)) is False
    assert _as("umbrella", lambda: store.update_document(doc_id, title="mine now")) is False
    # ...and none of that touched the owner's copy.
    assert _as("acme", lambda: store.get_document(doc_id)).title == "Redis eviction storm"


def test_updates_apply_only_the_supplied_fields(db):
    doc_id = _as("acme", lambda: store.create_document(_doc()))
    before = _as("acme", lambda: store.get_document(doc_id))

    assert _as(
        "acme",
        lambda: store.update_document(doc_id, content="Now with a memory limit.", tags=["redis"]),
    ) is True

    after = _as("acme", lambda: store.get_document(doc_id))
    assert after.content == "Now with a memory limit."
    assert after.tags == ["redis"]
    assert after.title == before.title  # untouched
    assert after.service == before.service
    assert after.updated_at >= before.updated_at


def test_updating_a_document_that_is_not_there_is_a_miss_not_a_crash(db):
    assert _as("acme", lambda: store.update_document("kb-999", title="x")) is False
    assert _as("acme", lambda: store.delete_document("kb-999")) is False


def test_reposting_the_same_document_replaces_it(db):
    """Ids are derived from title+content, so a repeat POST collides with itself.
    The dict this replaced overwrote silently; a unique violation would be a 500."""
    first = _as("acme", lambda: store.create_document(_doc(author="Ankit")))
    second = _as("acme", lambda: store.create_document(_doc(author="SRE Team")))

    assert first == second
    docs, _ = _as("acme", lambda: store.list_documents(service="sessions"))
    assert len(docs) == 1
    assert docs[0].author == "SRE Team"


def test_list_filters_by_type_service_tag_and_text(db):
    _as("acme", lambda: store.create_document(_doc()))

    by_type, _ = _as("acme", lambda: store.list_documents(doc_type="postmortem"))
    assert [d.id for d in by_type] == [d.id for d in by_type if d.doc_type.value == "postmortem"]
    assert len(by_type) == 1

    by_service, _ = _as("acme", lambda: store.list_documents(service="database"))
    assert [d.id for d in by_service] == ["kb-001"]

    by_tag, _ = _as("acme", lambda: store.list_documents(tag="kubernetes"))
    assert [d.id for d in by_tag] == ["kb-002"]

    # Case-insensitive, and matches content as well as title.
    by_text, _ = _as("acme", lambda: store.list_documents(search_text="ISTIOCTL"))
    assert [d.id for d in by_text] == ["kb-005"]


def test_paging_reports_the_full_total(db):
    page, total = _as("acme", lambda: store.list_documents(limit=2))

    assert len(page) == 2
    assert total == 5

    rest, _ = _as("acme", lambda: store.list_documents(limit=2, offset=2))
    assert not {d.id for d in page} & {d.id for d in rest}


def test_the_newest_document_is_listed_first(db):
    _as("acme", lambda: store.create_document(_doc(title="Just written")))

    docs, _ = _as("acme", lambda: store.list_documents(limit=1))
    assert docs[0].title == "Just written"


def test_search_ranks_this_orgs_documents_only(db):
    _as(
        "acme",
        lambda: store.create_document(
            _doc(title="Kafka consumer lag", content="Consumer lag grew after a rebalance storm.")
        ),
    )

    hits = _as("acme", lambda: store.search("consumer lag rebalance"))
    assert hits
    assert hits[0].document.title == "Kafka consumer lag"
    assert hits[0].relevance_score > 0

    # The other org has only the built-ins, so this query finds nothing of ours.
    assert "Kafka consumer lag" not in {
        r.document.title for r in _as("umbrella", lambda: store.search("consumer lag rebalance"))
    }


def test_tags_and_stats_are_scoped_to_the_org(db):
    _as("acme", lambda: store.create_document(_doc(tags=["redis"], service="sessions")))

    tags = _as("acme", lambda: store.list_tags())
    assert "redis" in tags
    assert tags == sorted(tags)
    assert "redis" not in _as("umbrella", lambda: store.list_tags())

    stats = _as("acme", lambda: store.stats())
    assert stats["total_documents"] == 6
    assert stats["by_type"]["postmortem"] == 1
    assert stats["by_service"]["sessions"] == 1
    assert _as("umbrella", lambda: store.stats())["total_documents"] == 5


def test_timestamps_come_back_timezone_aware(db):
    """SQLite has no timezone type; a naive datetime here would serialise to an
    ISO string with no offset for the frontend to guess at."""
    doc_id = _as("acme", lambda: store.create_document(_doc()))

    doc = _as("acme", lambda: store.get_document(doc_id))
    assert doc.created_at.tzinfo is not None
    assert doc.updated_at.tzinfo is not None
