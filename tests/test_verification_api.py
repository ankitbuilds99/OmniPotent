"""HTTP-level tests for /api/verification/*.

These pin the behaviours that were previously faked:

* a claim was "verified" against zero evidence backends, and any claim
  containing a number came back ``refuted / fabricated_fact`` — a confident
  falsehood produced entirely by missing configuration;
* ``POST /outcomes`` answered ``{"status": "recorded"}`` for claim ids it had
  never seen, because the write key and the read key differed, so
  ``GET /calibration`` reported zero samples forever;
* ``POST /actions/{id}/validate`` returned a literal ``{"validation":
  "pending"}`` dict and ignored both of its arguments;
* ``drop_database`` and ``terraform_destroy`` were cleared as ``is_safe: true``
  with an empty risk list, because unknown action names fell through to a
  permissive generic check.

The Prometheus-backed cases run against a real HTTP server on localhost, so the
httpx query path, the JSON parsing and the evidence classification are all
exercised for real; nothing is stubbed inside application code.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app.database import engine as db_engine
from app.verification import api_router as verification_api


# --------------------------------------------------------------------------
# A real Prometheus-shaped HTTP server. Query -> instant-vector value.
# --------------------------------------------------------------------------

class _PromHandler(BaseHTTPRequestHandler):
    values: dict[str, float] = {}
    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path != "/api/v1/query":
            self.send_response(404)
            self.end_headers()
            return

        query = parse_qs(parsed.query).get("query", [""])[0]
        type(self).seen.append(query)

        value = None
        for needle, val in type(self).values.items():
            if needle in query:
                value = val
                break

        if value is None:
            body: dict[str, Any] = {"status": "success", "data": {"resultType": "vector", "result": []}}
        else:
            body = {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"service": "checkout"}, "value": [1700000000, str(value)]}
                    ],
                },
            }

        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:  # noqa: A003 - silence test noise
        return


@pytest.fixture
def prometheus() -> Iterator[str]:
    """A real Prometheus HTTP API on localhost. Returns its base URL."""
    _PromHandler.values = {}
    _PromHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PromHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def _fresh_agents(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Rebuild the per-org agents so each test sees the settings it set.

    The database DSN is set through monkeypatch rather than at import time:
    a module-level ``os.environ`` write leaks a sqlite DSN into the rest of the
    pytest session, and tests/integration/conftest.py reads that variable to
    decide a real database is available and hands it to asyncpg.
    """
    monkeypatch.setenv(
        "COPILOT_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/verification_api.db"
    )
    verification_api._agents.clear()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None
    yield
    verification_api._agents.clear()
    cfg.get_settings.cache_clear()
    db_engine._engine = None
    db_engine._session_factory = None


@pytest.fixture
def client(_fresh_agents: None) -> Iterator[TestClient]:
    from app.api import main as api

    with TestClient(api.app) as c:
        yield c


def _configure(monkeypatch: pytest.MonkeyPatch, **settings: str) -> None:
    for key, value in settings.items():
        monkeypatch.setenv(key.upper(), value)
    cfg.get_settings.cache_clear()
    verification_api._agents.clear()


# --------------------------------------------------------------------------
# Claim verification: honest 503 instead of a fabricated verdict
# --------------------------------------------------------------------------

def test_verify_claim_503_when_no_evidence_backend_is_configured(client: TestClient) -> None:
    r = client.post(
        "/api/verification/claims/verify",
        json={"text": "The database is down", "service_id": "svc"},
    )

    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["error"] == "no_evidence_sources_configured"
    assert "PROMETHEUS_URL" in detail["missing_configuration"]
    assert "LOKI_URL or ELASTICSEARCH_URL" in detail["missing_configuration"]


def test_true_numeric_claim_is_not_reported_as_a_fabricated_fact(client: TestClient) -> None:
    """The regression that mattered most: a numeric claim used to come back
    ``200 refuted / fabricated_fact`` purely because no backend was reachable."""
    r = client.post(
        "/api/verification/claims/verify",
        json={"text": "Error rate rose to 42 percent", "cited_values": {"error_rate": 0.42}},
    )

    assert r.status_code == 503
    body = r.json()
    assert "refuted" not in json.dumps(body)
    assert "fabricated_fact" not in json.dumps(body)


def test_cited_source_is_not_reported_as_wrong_attribution_without_evidence(
    client: TestClient,
) -> None:
    r = client.post(
        "/api/verification/claims/verify",
        json={"text": "CPU is high", "cited_sources": ["prometheus"]},
    )

    assert r.status_code == 503
    assert "wrong_attribution" not in json.dumps(r.json())


def test_hypothesis_and_batch_endpoints_also_refuse(client: TestClient) -> None:
    r = client.post(
        "/api/verification/hypotheses/verify",
        json={"hypothesis": "Connection pool exhausted", "service_id": "checkout"},
    )
    assert r.status_code == 503

    r = client.post(
        "/api/verification/claims/verify-batch",
        json=[{"text": "Latency is up", "service_id": "checkout"}],
    )
    assert r.status_code == 503


def test_evidence_sources_reports_what_is_actually_reachable(client: TestClient) -> None:
    r = client.get("/api/verification/evidence-sources")

    assert r.status_code == 200
    body = r.json()
    assert body["configured_count"] == 0
    assert all(s["configured"] is False for s in body["sources"])
    assert "PROMETHEUS_URL" in body["missing_configuration"]


# --------------------------------------------------------------------------
# With a reachable backend the endpoint produces a real, evidence-backed verdict
# --------------------------------------------------------------------------

def test_verified_status_is_reachable_with_a_real_metrics_backend(
    monkeypatch: pytest.MonkeyPatch, prometheus: str
) -> None:
    _PromHandler.values = {"status=~\"5..\"": 0.42}
    _configure(monkeypatch, prometheus_url=prometheus)

    from app.api import main as api

    with TestClient(api.app) as client:
        r = client.get("/api/verification/evidence-sources")
        assert r.json()["configured_count"] == 1

        r = client.post(
            "/api/verification/claims/verify",
            json={"text": "Error rate is elevated", "service_id": "checkout"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    # A real query went out to the real server.
    assert any("checkout" in q for q in _PromHandler.seen)
    assert body["supporting_evidence_count"] >= 1
    assert body["evidence_sources_used"] == ["metrics"]
    assert body["status"] == "verified"
    assert body["hallucination_detected"] is False


def test_numeric_claim_matching_the_backend_is_not_a_hallucination(
    monkeypatch: pytest.MonkeyPatch, prometheus: str
) -> None:
    _PromHandler.values = {"status=~\"5..\"": 0.42}
    _configure(monkeypatch, prometheus_url=prometheus)

    from app.api import main as api

    with TestClient(api.app) as client:
        r = client.post(
            "/api/verification/claims/verify",
            json={
                "text": "Error rate rose to 42 percent",
                "service_id": "checkout",
                "cited_values": {"error_rate": 0.42},
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hallucination_detected"] is False, body
    assert body["status"] != "refuted", body


def test_numeric_claim_contradicted_by_the_backend_is_flagged(
    monkeypatch: pytest.MonkeyPatch, prometheus: str
) -> None:
    """The same claim against a backend that says 0.01 must NOT be verified."""
    _PromHandler.values = {"status=~\"5..\"": 0.01}
    _configure(monkeypatch, prometheus_url=prometheus)

    from app.api import main as api

    with TestClient(api.app) as client:
        r = client.post(
            "/api/verification/claims/verify",
            json={
                "text": "Error rate rose to 42 percent",
                "service_id": "checkout",
                "cited_values": {"error_rate": 0.42},
            },
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hallucination_detected"] is True
    assert body["status"] == "refuted"


# --------------------------------------------------------------------------
# Outcome recording + calibration
# --------------------------------------------------------------------------

def test_record_outcome_for_an_unknown_claim_is_404_not_recorded(client: TestClient) -> None:
    r = client.post(
        "/api/verification/outcomes",
        json={"claim_id": "never-seen", "was_correct": True},
    )

    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_claim_id"


def test_verify_then_record_outcome_actually_moves_calibration(
    monkeypatch: pytest.MonkeyPatch, prometheus: str
) -> None:
    """End to end: verify -> POST /outcomes with the returned claim_id ->
    GET /calibration shows the sample. This is the flow that silently recorded
    nothing because the cache was keyed by ``f"{id}:{text[:50]}"`` while the
    lookup used the bare id."""
    _PromHandler.values = {"status=~\"5..\"": 0.42}
    _configure(monkeypatch, prometheus_url=prometheus)

    from app.api import main as api

    with TestClient(api.app) as client:
        before = client.get("/api/verification/calibration").json()
        assert before["recorded_outcomes"] == 0
        assert before["brier_score"] is None
        assert before["ece"] is None
        # Every bin must be honest about having no data.
        assert all(b["accuracy"] is None for b in before["reliability_diagram"]["bins"])
        assert before["reliability_diagram"]["total_samples"] == 0

        verified = client.post(
            "/api/verification/claims/verify",
            json={"text": "Error rate is elevated", "service_id": "checkout"},
        )
        assert verified.status_code == 200, verified.text
        claim_id = verified.json()["claim_id"]

        recorded = client.post(
            "/api/verification/outcomes",
            json={"claim_id": claim_id, "was_correct": True},
        )
        assert recorded.status_code == 200, recorded.text

        after = client.get("/api/verification/calibration").json()

    assert after["recorded_outcomes"] == 1
    assert after["reliability_diagram"]["total_samples"] == 1
    assert after["brier_score"] is not None
    assert after["ece"] is not None
    populated = [b for b in after["reliability_diagram"]["bins"] if b["samples"] > 0]
    assert len(populated) == 1
    assert populated[0]["accuracy"] == 1.0


def test_calibration_says_where_its_numbers_live(client: TestClient) -> None:
    body = client.get("/api/verification/calibration").json()

    assert body["persistent"] is False
    assert "COPILOT_REDIS_URL" in body["storage"]


# --------------------------------------------------------------------------
# Pre-action safety: destructive verbs must not be waved through
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "action_type",
    ["drop_database", "terraform_destroy", "delete_deployment", "remove-namespace", "PURGE"],
)
def test_destructive_action_names_are_refused(client: TestClient, action_type: str) -> None:
    r = client.post(
        "/api/verification/actions/check",
        json={"action_type": action_type, "target": "prod-db", "params": {}},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["is_safe"] is False, body
    assert body["failed_preconditions"], body
    assert body["identified_risks"], body


def test_unknown_action_type_fails_closed(client: TestClient) -> None:
    r = client.post(
        "/api/verification/actions/check",
        json={"action_type": "launch_missiles", "target": "silo-1", "params": {}},
    )

    body = r.json()
    assert body["is_safe"] is False
    assert any("nrecognized" in p for p in body["failed_preconditions"]), body
    assert body["is_reversible"] is False


def test_flagged_risk_cannot_still_score_a_clean_one(client: TestClient) -> None:
    """A scale-up flagged as risky used to net back to safety_score 1.0."""
    r = client.post(
        "/api/verification/actions/check",
        json={"action_type": "scale", "target": "checkout", "params": {"replicas": 50}},
    )

    body = r.json()
    assert body["safety_score"] < 1.0, body


def test_preconditions_are_not_claimed_without_a_cluster(client: TestClient) -> None:
    r = client.post(
        "/api/verification/actions/check",
        json={"action_type": "scale", "target": "checkout", "params": {"replicas": 3}},
    )

    body = r.json()
    assert body["preconditions_met"] is False
    assert any("Kubernetes" in p for p in body["failed_preconditions"]), body
    assert body["is_safe"] is False


# --------------------------------------------------------------------------
# Post-action validation
# --------------------------------------------------------------------------

def test_validate_unknown_action_id_is_404(client: TestClient) -> None:
    r = client.post("/api/verification/actions/abc123/validate")

    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_action_id"


def test_validate_uses_the_stored_check_and_reports_it_cannot_observe(
    client: TestClient,
) -> None:
    checked = client.post(
        "/api/verification/actions/check",
        json={"action_type": "scale", "target": "checkout", "params": {"replicas": 3}},
    )
    action_id = checked.json()["action_id"]

    r = client.post(
        f"/api/verification/actions/{action_id}/validate",
        params={"timeout_seconds": 1},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["action_id"] == action_id
    assert body["target"] == "checkout"
    # The stored check is genuinely used: its expected post-state comes back.
    assert body["expected_post_state"] == {"replicas": 3, "available_replicas": 3}
    assert body["validated"] is None
    assert body["observed_state"] is None
    assert "Kubernetes" in body["reason"]
    # The old handler returned a literal dict with these two keys.
    assert "validation" not in body


def test_validate_rejects_an_absurd_timeout(client: TestClient) -> None:
    checked = client.post(
        "/api/verification/actions/check",
        json={"action_type": "scale", "target": "checkout", "params": {"replicas": 3}},
    )
    action_id = checked.json()["action_id"]

    r = client.post(
        f"/api/verification/actions/{action_id}/validate",
        params={"timeout_seconds": 99999},
    )

    assert r.status_code == 422


def test_action_ids_are_unique_within_the_same_second(client: TestClient) -> None:
    ids = {
        client.post(
            "/api/verification/actions/check",
            json={"action_type": "scale", "target": f"svc-{i}", "params": {"replicas": 3}},
        ).json()["action_id"]
        for i in range(5)
    }

    assert len(ids) == 5
