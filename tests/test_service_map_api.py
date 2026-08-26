"""The two routes the Service Map page needs: real topology, real metrics.

The page used to call `/api/kubernetes/services` (404 — that route has never
existed), draw a star topology it invented, and query Prometheus from the browser
at localhost:9090, which always failed and left the page reporting an error rate
derived from incident severities. These tests pin the replacements: edges come
from the graph's own relationships, and metrics either come from a configured
Prometheus or the route says it has none.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/svcmap.db")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants.sqlite"))
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_topology_reports_only_recorded_edges(client):
    """Edges are the graph's DEPENDS_ON/CALLS relationships, not a made-up star."""
    empty = client.get("/api/knowledge-graph/topology")
    assert empty.status_code == 200
    assert empty.json() == {"nodes": [], "edges": [], "count": 0}

    for sid in ("api-gateway", "checkout", "orders-db", "unlinked-worker"):
        assert client.post(
            "/api/knowledge-graph/services", json={"id": sid, "name": sid}
        ).status_code == 200
    client.post(
        "/api/knowledge-graph/relationships",
        json={"source_id": "api-gateway", "target_id": "checkout", "rel_type": "DEPENDS_ON"},
    )
    client.post(
        "/api/knowledge-graph/relationships",
        json={"source_id": "checkout", "target_id": "orders-db", "rel_type": "CALLS"},
    )

    body = client.get("/api/knowledge-graph/topology").json()
    assert {n["id"] for n in body["nodes"]} == {
        "api-gateway",
        "checkout",
        "orders-db",
        "unlinked-worker",
    }
    assert sorted((e["source"], e["target"]) for e in body["edges"]) == [
        ("api-gateway", "checkout"),
        ("checkout", "orders-db"),
    ]
    # The old code connected the gateway to every service and every service to
    # every database. Nothing touches the service with no recorded relationship.
    assert not any("unlinked-worker" in (e["source"], e["target"]) for e in body["edges"])
    # depth=1: api-gateway reaches orders-db transitively, but that is not an edge.
    assert ("api-gateway", "orders-db") not in [
        (e["source"], e["target"]) for e in body["edges"]
    ]


def test_service_metrics_503s_when_prometheus_is_not_configured(client):
    """No Prometheus means no numbers — not zeros that read as a healthy fleet."""
    res = client.get("/api/knowledge-graph/service-metrics")
    assert res.status_code == 503
    assert "PROMETHEUS_URL" in res.json()["detail"]


class _FakePrometheus(BaseHTTPRequestHandler):
    """Answers /api/v1/query with a canned instant-vector per query family."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query).get("query", [""])[0]
        if "histogram_quantile" in query:
            result = [
                {"metric": {"service": "checkout"}, "value": [1, "412.5"]},
            ]
        elif "5.." in query:
            result = [
                {"metric": {"service": "checkout"}, "value": [1, "3.25"]},
                # Prometheus emits NaN for a 0/0 error ratio; it must not become 0.0.
                {"metric": {"service": "orders-db"}, "value": [1, "NaN"]},
            ]
        else:
            result = [
                {"metric": {"service": "checkout"}, "value": [1, "120.5"]},
                {"metric": {"job": "orders-db"}, "value": [1, "8"]},
            ]
        payload = json.dumps({"status": "success", "data": {"resultType": "vector", "result": result}})
        body = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the test server
        return


@pytest.fixture
def fake_prometheus():
    server = HTTPServer(("127.0.0.1", 0), _FakePrometheus)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_service_metrics_proxies_a_configured_prometheus(tmp_path, monkeypatch, fake_prometheus):
    """With PROMETHEUS_URL set the numbers are the ones Prometheus returned."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/svcmap2.db")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants2.sqlite"))
    monkeypatch.setenv("PROMETHEUS_URL", fake_prometheus)
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            res = c.get("/api/knowledge-graph/service-metrics")
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["source"] == "prometheus"
            assert body["services"]["checkout"] == {
                "requests_per_sec": 120.5,
                "error_rate": 3.25,
                "latency_p99": 412.5,
            }
            # `job` is used when `service` is absent, and the NaN error ratio is
            # simply missing rather than reported as 0.
            assert body["services"]["orders-db"] == {"requests_per_sec": 8.0}
    finally:
        get_settings.cache_clear()


def test_service_metrics_502s_when_prometheus_is_unreachable(tmp_path, monkeypatch):
    """A dead Prometheus is an error, not an empty-but-successful metric set."""
    monkeypatch.setenv("COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/svcmap3.db")
    monkeypatch.setenv("COPILOT_TENANT_DB", str(tmp_path / "tenants3.sqlite"))
    # Port 1 is reserved and never listening.
    monkeypatch.setenv("PROMETHEUS_URL", "http://127.0.0.1:1")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            res = c.get("/api/knowledge-graph/service-metrics")
            assert res.status_code == 502
            assert "127.0.0.1:1" in res.json()["detail"]
    finally:
        get_settings.cache_clear()
