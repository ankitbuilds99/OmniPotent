"""Knowledge graph, knowledge base and severity prediction tell the truth.

Seven defects this covers, all cases where the answer looked confident and was
not derived from the question:

  1. `_parse_datetime` returned `now()` for a missing value, so every open
     incident read back with `resolved_at`, `detected_at` and `acknowledged_at`
     populated — a different value on each request, a time-to-resolve of zero,
     and no way for a caller to tell an open incident from a closed one.
  2. `/graphql/status` reported "available" and handed out a URL that 404'd:
     the GraphQL sub-router was attached to the knowledge-graph router *after*
     that router had already been copied onto the app.
  3. Knowledge base search scored a "semantic" similarity between two
     word-frequency vectors indexed by alphabetical rank, so the score carried
     no information about the query. Two different nonsense queries returned
     every document in the org, identically ranked, at up to 0.39 relevance
     with `matched_terms: []`.
  4. Severity prediction could not return SEV1 through the HTTP API: the router
     never supplied `blast_radius`, whose absent-value fallback pinned that
     term at 0.3, and no other input could make up the difference.
  5. `Document.to_dict()` cut the body to 500 characters, and the Knowledge
     Base page renders list items directly — so a runbook longer than that was
     shown to the reader truncated mid-sentence.
  6. Get-by-id answered 200 with `{"error": "..."}` for an id that does not
     exist, so `if (res.ok)` could not tell present from missing.
  7. `ALERT_TYPE_WEIGHTS` was defined and never read: a service being *down*
     scored exactly the same as a CPU alert on it.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.config as cfg
from app.api.main import app
from app.database import engine as db_engine
from app.event_intelligence.severity_predictor import (
    IncidentContext,
    PredictedSeverity,
    SeverityPredictor,
)
from app.knowledge_base.rag import Document, DocumentType, KnowledgeBase
from app.knowledge_graph import api_router as kg


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The real app against a database of this test's own.

    A fresh `_GRAPHS` registry because graphs are cached per org for the life of
    the process, and these tests write into them.
    """
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/ki.db")
    monkeypatch.setattr(kg, "_GRAPHS", kg.scoped(kg._OrgGraph))
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None

    with TestClient(app) as c:
        yield c

    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


# 1. An open incident is open


class TestOpenIncidentsStayOpen:
    def test_an_open_incident_has_no_resolved_at(self, client: TestClient) -> None:
        client.post(
            "/api/knowledge-graph/incidents",
            json={"id": "inc-open", "title": "still burning", "status": "open"},
        )

        body = client.get("/api/knowledge-graph/incidents/inc-open").json()

        assert body["status"] == "open"
        assert body["resolved_at"] is None
        assert body["detected_at"] is None
        assert body["acknowledged_at"] is None

    def test_reading_twice_gives_the_same_answer(self, client: TestClient) -> None:
        """The stamp used to be minted per request, so two reads disagreed."""
        client.post(
            "/api/knowledge-graph/incidents",
            json={"id": "inc-twice", "title": "still burning", "status": "open"},
        )

        first = client.get("/api/knowledge-graph/incidents/inc-twice").json()
        second = client.get("/api/knowledge-graph/incidents/inc-twice").json()

        assert first == second

    def test_a_resolved_incident_keeps_its_timestamps(self, client: TestClient) -> None:
        client.post(
            "/api/knowledge-graph/incidents",
            json={
                "id": "inc-done",
                "title": "fixed",
                "status": "resolved",
                "started_at": "2026-01-01T00:00:00+00:00",
                "resolved_at": "2026-01-01T00:30:00+00:00",
            },
        )

        body = client.get("/api/knowledge-graph/incidents/inc-done").json()

        assert body["resolved_at"].startswith("2026-01-01T00:30:00")

    def test_time_to_resolve_is_unknown_while_the_incident_is_open(self) -> None:
        """Not zero. `ttr_seconds` was `resolved_at - started_at` against a
        `resolved_at` of now(), which read as "resolved instantly"."""
        graph = kg.Neo4jKnowledgeGraph()
        incident = graph._dict_to_incident(
            {"id": "i", "title": "t", "status": "open", "started_at": "2026-01-01T00:00:00+00:00"}
        )

        assert incident.ttr_seconds is None
        assert incident.ttd_seconds is None

    @pytest.mark.parametrize("absent", [None, "", "   ", "not-a-timestamp"])
    def test_an_unusable_value_is_absent_not_now(self, absent) -> None:
        graph = kg.Neo4jKnowledgeGraph()
        assert graph._parse_optional_datetime(absent) is None

    def test_a_required_timestamp_still_falls_back_to_now(self) -> None:
        """started_at is not nullable, so it must never come back as None."""
        graph = kg.Neo4jKnowledgeGraph()
        assert graph._parse_datetime(None) is not None

    def test_an_alert_that_is_still_firing_has_no_resolved_at(self) -> None:
        graph = kg.Neo4jKnowledgeGraph()
        alert = graph._node_to_alert({"id": "a", "name": "n", "fired_at": None})
        assert alert.resolved_at is None
        assert alert.fired_at is not None


# 2. GraphQL is reachable


class TestGraphQLIsMounted:
    def test_the_advertised_url_answers(self, client: TestClient) -> None:
        status = client.get("/api/knowledge-graph/graphql/status").json()
        if not status["available"]:
            pytest.skip("strawberry-graphql is not installed")

        response = client.post(
            "/api/knowledge-graph/graphql", json={"query": "{ services { id } }"}
        )

        assert response.status_code == 200
        assert "errors" not in response.json()

    def test_the_schema_returns_what_was_written(self, client: TestClient) -> None:
        if not client.get("/api/knowledge-graph/graphql/status").json()["available"]:
            pytest.skip("strawberry-graphql is not installed")
        client.post(
            "/api/knowledge-graph/services",
            json={"id": "checkout", "name": "checkout"},
        )

        body = client.post(
            "/api/knowledge-graph/graphql", json={"query": "{ services { id name } }"}
        ).json()

        assert {"id": "checkout", "name": "checkout"} in body["data"]["services"]

    def test_status_and_reality_agree(self, client: TestClient) -> None:
        """The point of the bug: these two disagreed."""
        status = client.get("/api/knowledge-graph/graphql/status").json()
        reachable = client.post(
            "/api/knowledge-graph/graphql", json={"query": "{ services { id } }"}
        ).status_code != 404

        assert status["available"] == reachable


# 3. Search answers the query it was asked


def _kb(*docs: Document) -> KnowledgeBase:
    base = KnowledgeBase()
    for doc in docs:
        base.add_document(doc)
    return base


def _doc(doc_id: str, title: str, content: str, **kwargs) -> Document:
    return Document(
        id=doc_id,
        title=title,
        content=content,
        doc_type=kwargs.pop("doc_type", DocumentType.RUNBOOK),
        tags=kwargs.pop("tags", []),
        service=kwargs.pop("service", None),
    )


class TestSearchIsAboutTheQuery:
    @pytest.fixture
    def base(self) -> KnowledgeBase:
        return _kb(
            _doc("kb-1", "Redis eviction", "Redis evicted session keys under memory pressure."),
            _doc("kb-2", "Postgres failover", "The primary was promoted after a network partition."),
            _doc("kb-3", "TLS renewal", "Certificate renewal is handled by cert-manager."),
        )

    def test_a_query_matching_nothing_returns_nothing(self, base: KnowledgeBase) -> None:
        assert asyncio.run(base.search("quantum blockchain unicorn")) == []

    def test_two_different_nonsense_queries_are_not_the_same_answer(
        self, base: KnowledgeBase
    ) -> None:
        """Both used to return every document, identically ranked."""
        first = asyncio.run(base.search("zzz nonexistent term"))
        second = asyncio.run(base.search("quantum blockchain unicorn"))
        assert first == [] and second == []

    def test_the_right_document_wins(self, base: KnowledgeBase) -> None:
        results = asyncio.run(base.search("redis session keys evicted"))

        assert results[0].document.id == "kb-1"

    def test_a_different_query_picks_a_different_document(self, base: KnowledgeBase) -> None:
        assert asyncio.run(base.search("certificate renewal"))[0].document.id == "kb-3"
        assert asyncio.run(base.search("primary promoted partition"))[0].document.id == "kb-2"

    def test_matched_terms_are_reported_and_non_empty_for_a_hit(
        self, base: KnowledgeBase
    ) -> None:
        """Every hit used to carry `matched_terms: []` while claiming relevance."""
        top = asyncio.run(base.search("redis eviction"))[0]

        assert set(top.matched_terms) >= {"redis", "eviction"}

    def test_relevance_is_bounded(self, base: KnowledgeBase) -> None:
        for result in asyncio.run(base.search("redis session keys under memory pressure")):
            assert 0.0 < result.relevance_score <= 1.0

    def test_covering_more_of_the_query_scores_higher(self) -> None:
        base = _kb(
            _doc("all", "Redis eviction", "Redis evicted session keys under memory pressure."),
            _doc("some", "Redis notes", "Redis is a cache."),
        )

        results = {r.document.id: r.relevance_score for r in
                   asyncio.run(base.search("redis evicted session keys"))}

        assert results["all"] > results["some"]

    def test_a_full_keyword_match_reads_as_a_good_hit(self, base: KnowledgeBase) -> None:
        """Not 0.4. The relevance is shown to a human as a percentage."""
        top = asyncio.run(base.search("redis evicted session keys under memory pressure"))[0]

        assert top.relevance_score > 0.7

    def test_the_title_is_searchable_not_just_the_body(self) -> None:
        base = _kb(_doc("kb-1", "Kafka rebalance storm", "See the linked dashboard."))

        assert asyncio.run(base.search("kafka rebalance"))[0].document.id == "kb-1"

    def test_tags_are_searchable(self) -> None:
        base = _kb(_doc("kb-1", "Untitled", "No detail here.", tags=["cassandra"]))

        assert asyncio.run(base.search("cassandra"))[0].document.id == "kb-1"

    def test_filters_still_apply(self, base: KnowledgeBase) -> None:
        assert asyncio.run(base.search("redis", doc_type=DocumentType.POSTMORTEM)) == []

    def test_an_empty_query_returns_nothing(self, base: KnowledgeBase) -> None:
        assert asyncio.run(base.search("")) == []

    def test_rag_says_it_found_nothing_when_it_found_nothing(
        self, base: KnowledgeBase
    ) -> None:
        answer = asyncio.run(base.query("quantum blockchain unicorn"))

        assert answer.sources == []
        assert answer.confidence == 0.0


class TestBM25Relevance:
    def test_no_overlap_scores_zero(self) -> None:
        from app.incident_memory import bm25_relevance

        assert bm25_relevance("alpha beta", ["gamma delta"]) == [0.0]

    def test_a_full_match_approaches_one(self) -> None:
        from app.incident_memory import bm25_relevance

        [score] = bm25_relevance("alpha beta", ["alpha beta"])
        assert 0.5 < score <= 1.0

    def test_a_query_of_mostly_unknown_words_scores_low(self) -> None:
        """The unknown terms have to count against the score, or one lucky word
        out of ten reads as a perfect match."""
        from app.incident_memory import bm25_relevance

        [mostly_unknown] = bm25_relevance("alpha zzz yyy xxx www vvv", ["alpha beta"])
        [full] = bm25_relevance("alpha beta", ["alpha beta"])
        assert mostly_unknown < full

    def test_an_empty_corpus_is_not_an_error(self) -> None:
        from app.incident_memory import bm25_relevance

        assert bm25_relevance("anything", []) == []

    def test_bm25_rank_still_ranks(self) -> None:
        """The shared core was refactored underneath it."""
        from app.incident_memory import bm25_rank

        ranked = bm25_rank(
            "redis eviction",
            [
                {"id": "a", "title": "postgres failover"},
                {"id": "b", "title": "redis eviction storm"},
            ],
        )
        assert [r["id"] for r in ranked] == ["b", "a"]
        assert ranked[0]["score"] > 0 and ranked[1]["score"] == 0


# 4 + 7. Severity prediction uses every input it claims to


def _context(**kwargs) -> IncidentContext:
    kwargs.setdefault("incident_id", "i1")
    kwargs.setdefault("service", "checkout-web")
    kwargs.setdefault("alert_type", "down")
    return IncidentContext(**kwargs)


_TOTAL_OUTAGE = {
    "error_rate": 100.0,
    "latency_p99": 999999.0,
    "affected_endpoints": [f"/e{i}" for i in range(50)],
    "related_alerts_count": 1000,
    "blast_radius": {
        "affected_percentage": 1.0,
        "revenue_path_affected": True,
        "customer_facing_impact": True,
    },
    "current_slo_status": {"budget_remaining_percent": 0, "status": "exhausted"},
}


class TestSev1IsReachable:
    def test_a_total_outage_of_a_critical_service_is_sev1(self) -> None:
        prediction = asyncio.run(SeverityPredictor().predict(_context(**_TOTAL_OUTAGE)))

        assert prediction.predicted_severity == PredictedSeverity.SEV1
        assert prediction.escalation_recommended is True
        assert prediction.recommended_response_time_minutes == 5

    def test_a_quiet_alert_on_a_minor_service_is_not(self) -> None:
        prediction = asyncio.run(
            SeverityPredictor().predict(
                _context(service="analytics", alert_type="cpu", error_rate=0.1)
            )
        )

        assert prediction.predicted_severity != PredictedSeverity.SEV1

    def test_the_blast_radius_moves_the_answer(self) -> None:
        """It was pinned at the unknown-value fallback for every request."""
        without = asyncio.run(
            SeverityPredictor().predict(
                _context(**{**_TOTAL_OUTAGE, "blast_radius": None})
            )
        )
        with_radius = asyncio.run(SeverityPredictor().predict(_context(**_TOTAL_OUTAGE)))

        assert with_radius.factors.blast_radius_score > without.factors.blast_radius_score

    def test_confidence_does_not_claim_a_blast_radius_nobody_computed(self) -> None:
        unknown = asyncio.run(
            SeverityPredictor().predict(
                _context(**{**_TOTAL_OUTAGE, "blast_radius": None})
            )
        )
        known = asyncio.run(SeverityPredictor().predict(_context(**_TOTAL_OUTAGE)))

        assert known.confidence > unknown.confidence

    def test_the_weights_sum_to_one(self) -> None:
        """SEV1 needs 0.85, so the reachable maximum has to be at least that."""
        maxed = SeverityPredictor()._calculate_severity_score(
            kg_factors := _all_factors_at(1.0)
        )
        assert maxed == pytest.approx(1.0)
        assert kg_factors.alert_type_score == 1.0

    def test_an_slo_breach_is_read_from_the_request(self) -> None:
        breached = asyncio.run(
            SeverityPredictor().predict(_context(**_TOTAL_OUTAGE))
        )
        assert breached.factors.slo_breach is True


def _all_factors_at(value: float):
    from app.event_intelligence.severity_predictor import SeverityFactors

    return SeverityFactors(
        service_criticality=value,
        blast_radius_score=value,
        time_sensitivity=value,
        historical_pattern_score=value,
        error_rate_impact=value,
        user_impact_score=value,
        revenue_impact=True,
        customer_facing=True,
        slo_breach=True,
        alert_type_score=value,
    )


class TestAlertTypeIsScored:
    def test_down_outranks_cpu(self) -> None:
        down = asyncio.run(SeverityPredictor().predict(_context(alert_type="down")))
        cpu = asyncio.run(SeverityPredictor().predict(_context(alert_type="cpu")))

        assert down.factors.alert_type_score > cpu.factors.alert_type_score

    def test_the_difference_reaches_the_score(self) -> None:
        """Identical payloads differing only in alert_type used to produce
        byte-identical factor dicts."""
        predictor = SeverityPredictor()
        down = predictor._calculate_severity_score(
            asyncio.run(predictor._calculate_factors(_context(alert_type="down")))
        )
        cpu = predictor._calculate_severity_score(
            asyncio.run(predictor._calculate_factors(_context(alert_type="cpu")))
        )

        assert down > cpu

    @pytest.mark.parametrize(
        ("alert_type", "expected"),
        [
            ("down", 1.0),
            ("security", 0.95),
            ("error_rate", 0.85),
            ("cpu", 0.4),
            ("high_error_rate", 0.85),  # substring
            ("ERROR_RATE", 0.85),  # case
            ("error-rate", 0.85),  # separator
        ],
    )
    def test_the_shipped_table_is_what_gets_used(self, alert_type, expected) -> None:
        assert SeverityPredictor()._get_alert_type_weight(alert_type) == expected

    def test_an_unknown_type_is_neutral_not_zero(self) -> None:
        """An unusual name must not be able to talk the severity down."""
        assert SeverityPredictor()._get_alert_type_weight("analytics-nonsense") == 0.5
        assert SeverityPredictor()._get_alert_type_weight("") == 0.5

    def test_every_weight_in_the_table_is_reachable(self) -> None:
        predictor = SeverityPredictor()
        for name, weight in predictor.ALERT_TYPE_WEIGHTS.items():
            assert predictor._get_alert_type_weight(name) == weight


class TestSeverityOverHttp:
    def test_sev1_is_reachable_through_the_api(self, client: TestClient) -> None:
        body = client.post(
            "/api/event-intelligence/severity/predict",
            json={
                "incident_id": "i1",
                "service": "checkout-web",
                "alert_type": "down",
                "error_rate": 100.0,
                "latency_p99": 999999.0,
                "affected_endpoints": [f"/e{i}" for i in range(50)],
                "related_alerts_count": 1000,
                "blast_radius": {
                    "affected_percentage": 1.0,
                    "revenue_path_affected": True,
                    "customer_facing_impact": True,
                },
                "slo_status": {"budget_remaining_percent": 0, "status": "exhausted"},
            },
        ).json()

        assert body["predicted_severity"] == "sev1"
        assert body["inputs"]["blast_radius_source"] == "request"
        assert body["inputs"]["slo_status_provided"] is True

    def test_the_response_says_when_no_blast_radius_was_available(
        self, client: TestClient
    ) -> None:
        body = client.post(
            "/api/event-intelligence/severity/predict",
            json={"incident_id": "i1", "service": "nowhere", "alert_type": "down"},
        ).json()

        assert body["inputs"]["blast_radius_source"] == "unknown"
        assert body["inputs"]["blast_radius"] is None

    def test_the_blast_radius_comes_from_the_knowledge_graph(
        self, client: TestClient
    ) -> None:
        """Omitting it means "work it out", not "assume moderate"."""
        for svc in ("checkout", "web", "billing"):
            client.post("/api/knowledge-graph/services", json={"id": svc, "name": svc})
        client.post(
            "/api/knowledge-graph/relationships",
            json={"source_id": "web", "target_id": "checkout", "rel_type": "depends_on"},
        )

        body = client.post(
            "/api/event-intelligence/severity/predict",
            json={"incident_id": "i1", "service": "checkout", "alert_type": "down"},
        ).json()

        assert body["inputs"]["blast_radius_source"] == "knowledge_graph"
        assert body["inputs"]["blast_radius"]["known_service_count"] == 3

    def test_alert_type_changes_the_answer_over_http(self, client: TestClient) -> None:
        payload = {"incident_id": "i1", "service": "checkout", "error_rate": 50.0}
        down = client.post(
            "/api/event-intelligence/severity/predict",
            json={**payload, "alert_type": "down"},
        ).json()
        cpu = client.post(
            "/api/event-intelligence/severity/predict",
            json={**payload, "alert_type": "cpu"},
        ).json()

        assert down["factors"] != cpu["factors"]
        assert down["factors"]["alert_type_score"] > cpu["factors"]["alert_type_score"]


# 5. The reader gets the whole article


_LONG = "x" * 400 + "\nthe part that used to be cut off " * 10


class TestArticleBodiesAreWhole:
    def test_to_dict_returns_the_full_body(self) -> None:
        doc = _doc("kb-1", "Long runbook", _LONG)

        assert doc.to_dict()["content"] == _LONG

    def test_the_excerpt_is_where_the_truncation_went(self) -> None:
        body = _doc("kb-1", "Long runbook", _LONG).to_dict()

        assert body["excerpt"].endswith("...")
        assert len(body["excerpt"]) == 503

    def test_a_short_article_has_no_ellipsis(self) -> None:
        body = _doc("kb-1", "Short", "brief").to_dict()

        assert body["content"] == body["excerpt"] == "brief"

    def test_the_list_endpoint_agrees_with_the_detail_endpoint(
        self, client: TestClient
    ) -> None:
        """The Knowledge Base page renders list items and never calls detail."""
        client.post(
            "/api/knowledge/documents",
            json={"title": "Long runbook", "content": _LONG, "doc_type": "runbook"},
        )

        listed = client.get("/api/knowledge/documents?limit=100").json()["documents"]
        for item in listed:
            full = client.get(f"/api/knowledge/documents/{item['id']}").json()
            assert item["content"] == full["content"]

    def test_the_shipped_runbook_is_not_truncated(self, client: TestClient) -> None:
        """kb-001 ships at 635 characters and was cut mid-sentence."""
        listed = client.get("/api/knowledge/documents?limit=100").json()["documents"]
        seeded = [d for d in listed if d["id"] == "kb-001"]
        assert seeded, "kb-001 should be seeded"
        assert not seeded[0]["content"].endswith("...")


# 6. Missing means 404


class TestMissingIsNotFound:
    def test_an_unknown_service_is_404(self, client: TestClient) -> None:
        response = client.get("/api/knowledge-graph/services/nope")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "service_not_found"

    def test_an_unknown_incident_is_404(self, client: TestClient) -> None:
        response = client.get("/api/knowledge-graph/incidents/nope")

        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "incident_not_found"

    def test_a_present_service_is_still_200(self, client: TestClient) -> None:
        """The positive control: present and missing have to differ."""
        client.post("/api/knowledge-graph/services", json={"id": "svc-a", "name": "a"})

        assert client.get("/api/knowledge-graph/services/svc-a").status_code == 200

    def test_an_unknown_sync_source_is_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/knowledge-graph/sync/configure",
            json={"source": "bogus", "config": {}},
        )

        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "unknown_source"

    def test_the_error_names_the_sources_that_do_work(self, client: TestClient) -> None:
        detail = client.post(
            "/api/knowledge-graph/sync/configure",
            json={"source": "bogus", "config": {}},
        ).json()["detail"]

        assert set(detail["valid_sources"]) == {
            "github", "cloudtrail", "prometheus", "kubernetes", "jaeger",
        }
