"""A configured Neo4j that cannot be reached must refuse, not answer.

The defect this covers: `connect()` recorded `connect_error` and returned False,
and `_session()` then yielded `None` — the same thing it yields when no Neo4j is
configured at all. Every read and write therefore fell through to the relational
`graph_store`, so an operator whose production Neo4j was down (or whose password
had been rotated) got HTTP 200 everywhere: `GET /blast-radius/checkout` answered
`affected_count: 0`, which reads as "nothing depends on checkout, safe to
restart", while `POST /services` wrote into a second, disagreeing copy of the
graph. Nothing anywhere reported the outage — `connect_error` was assigned and
never read.

The graph now raises `KnowledgeGraphUnavailable` (HTTP 503) carrying the reason,
and retries the server instead of latching the first failure for the life of the
process.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.knowledge_graph import api_router as kg
from app.knowledge_graph import graph_store
from app.knowledge_graph.neo4j_backend import (
    KnowledgeGraphUnavailable,
    Neo4jKnowledgeGraph,
    ServiceNode,
)

UNREACHABLE_URI = "bolt://prod-neo4j:7687"


class _FakeResult:
    async def single(self):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _FakeSession:
    def __init__(self, queries: list[str]) -> None:
        self._queries = queries

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def run(self, query: str, **params):
        self._queries.append(query)
        return _FakeResult()


class _FakeDriver:
    def __init__(self, server: "_FakeServer") -> None:
        self._server = server

    async def verify_connectivity(self) -> None:
        if not self._server.reachable:
            raise OSError("connection refused")

    def session(self, database: str | None = None) -> _FakeSession:
        return _FakeSession(self._server.queries)

    async def close(self) -> None:
        self._server.closed += 1


class _FakeServer:
    """A stand-in for the `neo4j` driver package that can be taken down."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.queries: list[str] = []
        self.drivers_created = 0
        self.closed = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_FakeServer":
        server = self

        class AsyncGraphDatabase:
            @staticmethod
            def driver(uri, auth=None, **kwargs):
                server.drivers_created += 1
                return _FakeDriver(server)

        module = types.ModuleType("neo4j")
        module.AsyncGraphDatabase = AsyncGraphDatabase  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "neo4j", module)
        return self


@pytest.fixture
def store_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Records every relational write, so a test can prove none happened."""
    calls: list[tuple] = []

    async def _put_node(label, node_id, props):
        calls.append(("put_node", label, node_id))

    async def _put_edge(*args, **kwargs):
        calls.append(("put_edge",) + args)

    monkeypatch.setattr(graph_store, "put_node", _put_node)
    monkeypatch.setattr(graph_store, "put_edge", _put_edge)
    return calls


class TestAnUnreachableNeo4jRefuses:
    @pytest.mark.asyncio
    async def test_a_read_raises_503_naming_the_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeServer(reachable=False).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)

        assert await graph.connect() is False

        with pytest.raises(KnowledgeGraphUnavailable) as raised:
            await graph.find_blast_radius("checkout")

        assert raised.value.status_code == 503
        assert UNREACHABLE_URI in raised.value.detail

    @pytest.mark.asyncio
    async def test_a_blast_radius_is_never_a_fabricated_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`affected_count: 0` used to mean "safe to restart" here."""
        _FakeServer(reachable=False).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)
        await graph.connect()

        with pytest.raises(KnowledgeGraphUnavailable):
            result = await graph.find_blast_radius("checkout")
            pytest.fail(f"answered {result} from a graph it could not reach")

    @pytest.mark.asyncio
    async def test_a_write_does_not_fork_the_graph_into_the_relational_store(
        self, monkeypatch: pytest.MonkeyPatch, store_calls: list[tuple]
    ) -> None:
        _FakeServer(reachable=False).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)
        await graph.connect()

        with pytest.raises(KnowledgeGraphUnavailable):
            await graph.create_service(ServiceNode(id="checkout", name="checkout"))

        assert store_calls == []

    @pytest.mark.asyncio
    async def test_stats_refuse_rather_than_count_a_different_graph(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It used to answer `connected: false, mode: database` with the row
        counts of the store the operator did not configure."""
        _FakeServer(reachable=False).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)
        await graph.connect()

        with pytest.raises(KnowledgeGraphUnavailable):
            await graph.get_stats()

    @pytest.mark.asyncio
    async def test_a_missing_driver_package_refuses_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

        def _no_neo4j(name, *args, **kwargs):
            if name == "neo4j":
                raise ImportError("No module named 'neo4j'")
            return real_import(name, *args, **kwargs)

        monkeypatch.delitem(sys.modules, "neo4j", raising=False)
        monkeypatch.setattr("builtins.__import__", _no_neo4j)

        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)
        assert await graph.connect() is False

        with pytest.raises(KnowledgeGraphUnavailable) as raised:
            await graph.list_services()

        assert "not installed" in raised.value.detail


class TestTheConfiguredBackendIsStillTheOneThatAnswers:
    @pytest.mark.asyncio
    async def test_no_uri_still_means_the_relational_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal must be scoped to a *configured* Neo4j: the default
        deployment has no URI and the relational graph is its real backend."""

        async def _counts():
            return {"Service": 3}, 2

        monkeypatch.setattr(graph_store, "counts", _counts)
        graph = Neo4jKnowledgeGraph(uri="")
        await graph.connect()

        stats = await graph.get_stats()

        assert stats["mode"] == "database"
        assert stats["node_counts"] == {"Service": 3}

    @pytest.mark.asyncio
    async def test_a_reachable_neo4j_answers_from_cypher(
        self, monkeypatch: pytest.MonkeyPatch, store_calls: list[tuple]
    ) -> None:
        server = _FakeServer(reachable=True).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)

        assert await graph.connect() is True
        await graph.create_service(ServiceNode(id="checkout", name="checkout"))

        assert any("MERGE (s:Service" in q for q in server.queries)
        assert store_calls == []


class TestAServerThatComesBackIsPickedUp:
    @pytest.mark.asyncio
    async def test_the_first_failure_does_not_latch_forever(
        self, monkeypatch: pytest.MonkeyPatch, store_calls: list[tuple]
    ) -> None:
        server = _FakeServer(reachable=False).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)
        assert await graph.connect() is False

        # The operator fixes the server; the retry interval has elapsed.
        server.reachable = True
        graph._last_connect_attempt -= graph.RECONNECT_INTERVAL_SECONDS + 1

        await graph.create_service(ServiceNode(id="checkout", name="checkout"))

        assert any("MERGE (s:Service" in q for q in server.queries)
        assert store_calls == []

    @pytest.mark.asyncio
    async def test_an_outage_is_not_retried_on_every_single_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _FakeServer(reachable=False).install(monkeypatch)
        graph = Neo4jKnowledgeGraph(uri=UNREACHABLE_URI)
        await graph.connect()
        attempts = server.drivers_created

        for _ in range(5):
            with pytest.raises(KnowledgeGraphUnavailable):
                await graph.get_service("checkout")

        assert server.drivers_created == attempts


class TestTheApiSaysSoOutLoud:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        _FakeServer(reachable=False).install(monkeypatch)
        monkeypatch.setenv("COPILOT_NEO4J_URI", UNREACHABLE_URI)
        # A registry of this test's own: graphs are cached per org for the life
        # of the process, and this one must be built from the URI above.
        monkeypatch.setattr(kg, "_GRAPHS", kg.scoped(kg._OrgGraph))
        get_settings.cache_clear()
        yield TestClient(app)
        get_settings.cache_clear()

    def test_blast_radius_is_503_not_an_empty_200(self, client: TestClient) -> None:
        response = client.get("/api/knowledge-graph/blast-radius/checkout")

        assert response.status_code == 503, response.text
        assert UNREACHABLE_URI in response.text

    def test_creating_a_service_is_503_not_a_silent_second_graph(
        self, client: TestClient, store_calls: list[tuple]
    ) -> None:
        response = client.post(
            "/api/knowledge-graph/services",
            json={"id": "checkout", "name": "checkout"},
        )

        assert response.status_code == 503, response.text
        assert store_calls == []
