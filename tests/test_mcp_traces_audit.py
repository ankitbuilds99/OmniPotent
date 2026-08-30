"""Regression tests for the traces MCP server's LIVE (Jaeger) mode.

Live mode used to map each Jaeger span to only span_id/operation/duration_ms,
dropping the `service` and `parent` fields that every downstream tool reads.
The moment an operator set TRACES_API_URL — the documented way to leave demo
mode — service_dependencies, analyze_blast_radius, correlate_incidents and
analyze_critical_path all raised KeyError('service'). These tests drive the real
live code path with a stubbed Jaeger endpoint and assert the four capabilities
produce the same analysis they do over the offline fixtures.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

SERVER_PATH = Path(__file__).resolve().parents[1] / "app" / "mcp" / "servers" / "traces" / "server.py"
JAEGER_URL = "http://jaeger.test:16686"

# A Jaeger-shaped payload: the service name lives once per trace in `processes`
# and is referenced by processID; parentage lives in `references`; failure is
# expressed three different ways (5xx tag, `error` tag, exception log).
ROOT_START_US = 1_750_672_875_000_000

JAEGER_TRACE = {
    "traceID": "abc123",
    "processes": {
        "p1": {"serviceName": "api-gateway"},
        "p2": {"serviceName": "checkout-svc"},
        "p3": {"serviceName": "inventory-svc"},
    },
    "spans": [
        {
            "spanID": "s2", "processID": "p2", "operationName": "checkout",
            "duration": 480_000, "startTime": ROOT_START_US + 5_000,
            "references": [{"refType": "CHILD_OF", "traceID": "abc123", "spanID": "s1"}],
            "tags": [{"key": "error", "type": "bool", "value": True}],
        },
        {
            "spanID": "s1", "processID": "p1", "operationName": "POST /api/checkout",
            "duration": 512_000, "startTime": ROOT_START_US, "references": [],
            "tags": [{"key": "http.status_code", "type": "int64", "value": 500}],
        },
        {
            "spanID": "s3", "processID": "p2", "operationName": "applyDiscount",
            "duration": 2_000, "startTime": ROOT_START_US + 10_000,
            "references": [{"refType": "CHILD_OF", "traceID": "abc123", "spanID": "s2"}],
            "tags": [],
            "logs": [{"timestamp": ROOT_START_US + 11_000, "fields": [
                {"key": "event", "value": "error"},
                {"key": "error.object", "value": "TypeError: Cannot read properties of undefined"},
            ]}],
        },
        {
            "spanID": "s4", "processID": "p3", "operationName": "GET /stock",
            "duration": 44_000, "startTime": ROOT_START_US + 20_000,
            "references": [{"refType": "CHILD_OF", "traceID": "abc123", "spanID": "s2"}],
            "tags": [{"key": "http.status_code", "type": "int64", "value": 200}],
        },
    ],
}


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)

    def json(self):
        return self._payload


def _services_in(traces):
    names = set()
    for tr in traces:
        for proc in (tr.get("processes") or {}).values():
            if proc.get("serviceName"):
                names.add(proc["serviceName"])
    return sorted(names)


def _fake_get(traces=None, detail=None):
    """Stand in for httpx.get against a Jaeger query API.

    Modelled on the real one: /api/services lists the services, and /api/traces
    refuses with HTTP 400 unless a `service` parameter is supplied — the check
    that catches a search which cannot work live.
    """
    traces = [JAEGER_TRACE] if traces is None else traces
    detail = traces if detail is None else detail

    def _get(url, params=None, timeout=None):
        if url.endswith("/api/services"):
            return _Resp({"data": _services_in(traces)})
        if "/api/traces/" in url:
            return _Resp({"data": detail})
        if not (params or {}).get("service"):
            return _Resp({"data": None, "errors": [{"code": 400, "msg": "parameter 'service' is required"}]}, 400)
        wanted = params["service"]
        return _Resp({"data": [t for t in traces if wanted in _services_in([t])]})

    return _get


@pytest.fixture()
def live():
    """Import a private copy of the server with TRACES_API_URL set.

    OFFLINE is decided at import time from the environment, so live mode can only
    be exercised by importing the module with the variable present. A separate
    module object keeps the shared offline import in sys.modules untouched.
    """
    spec = importlib.util.spec_from_file_location("traces_server_live_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"TRACES_API_URL": JAEGER_URL}):
        spec.loader.exec_module(module)
    assert module.OFFLINE is False
    return module


@pytest.fixture()
def live_jaeger(live, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", _fake_get())
    return live


def test_live_get_trace_keeps_service_parent_and_error(live_jaeger):
    tr = live_jaeger.get_trace("abc123")
    spans = {sp["span_id"]: sp for sp in tr["spans"]}

    assert spans["s1"]["service"] == "api-gateway"
    assert spans["s2"]["service"] == "checkout-svc"
    assert spans["s4"]["service"] == "inventory-svc"
    assert spans["s1"]["parent"] is None
    assert spans["s2"]["parent"] == "s1"
    assert spans["s3"]["parent"] == "s2"
    # Three different failure encodings must all read as an error span.
    assert spans["s1"]["status"] == "error"   # 5xx http.status_code
    assert spans["s2"]["status"] == "error"   # classic `error` tag
    assert spans["s3"]["status"] == "error"   # exception log
    assert "TypeError" in spans["s3"]["error"]
    assert spans["s4"]["status"] == "ok"
    # The root is the unparented span, not simply the first one in the list.
    assert tr["root_service"] == "api-gateway"
    assert tr["span_count"] == 4
    assert tr["duration_ms"] == 512


def test_live_service_dependencies_derives_real_edges(live_jaeger):
    assert live_jaeger.service_dependencies() == [
        ["api-gateway", "checkout-svc"],
        ["checkout-svc", "inventory-svc"],
    ]


def test_live_blast_radius_separates_cause_from_symptom(live_jaeger):
    res = live_jaeger.analyze_blast_radius("checkout-svc")
    assert res["affected_upstream"] == ["api-gateway"]
    assert res["downstream_dependencies"] == ["inventory-svc"]


def test_live_correlate_incidents_finds_shared_root(live_jaeger):
    res = live_jaeger.correlate_incidents(["api-gateway", "checkout-svc"])
    assert res["likely_root"] == "checkout-svc"


def test_live_critical_path_localizes_deepest_error_span(live_jaeger):
    res = live_jaeger.analyze_critical_path("abc123")
    assert res["fault_span"]["operation"] == "applyDiscount"
    assert res["fault_span"]["service"] == "checkout-svc"
    # self-time attributes the wait to the span doing the work, not its parent.
    top = res["bottlenecks"][0]
    assert top["service"] == "checkout-svc" and top["operation"] == "checkout"
    assert top["self_time_ms"] == 480 - 2 - 44


def test_live_search_traces_reports_root_service_start_and_min_duration(live_jaeger):
    rows = live_jaeger.search_traces()
    assert len(rows) == 1
    row = rows[0]
    assert row["root_service"] == "api-gateway"
    assert row["status"] == "error"
    expected_start = datetime.fromtimestamp(ROOT_START_US / 1_000_000, tz=timezone.utc)
    assert row["start"] == expected_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    # min_duration_ms is honoured live, exactly as it is offline.
    assert live_jaeger.search_traces(min_duration_ms=1000) == []
    assert len(live_jaeger.search_traces(min_duration_ms=500)) == 1
    # A window pivot off the exemplar path needs `start`, so it must resolve.
    window = expected_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert live_jaeger.get_exemplars(window, window)[0]["trace_id"] == "abc123"


def test_live_get_trace_says_not_found_instead_of_empty_success(live, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", _fake_get(detail=[]))
    res = live.get_trace("nope")
    assert "not found" in res["error"]
    assert res.get("spans") is None
    # analyze_critical_path must propagate the reason, not analyze zero spans.
    assert "not found" in live.analyze_critical_path("nope")["error"]


def test_unattributed_span_is_labelled_not_invented(live, monkeypatch):
    """A span whose processID is missing from `processes` has no measured service.
    It must not vanish from latency attribution, must not acquire a fake service
    name, and must not contribute an imaginary dependency edge."""
    import httpx

    payload = {
        "traceID": "orphan",
        "processes": {"p1": {"serviceName": "api-gateway"}},
        "spans": [
            {"spanID": "r1", "processID": "p1", "operationName": "GET /", "duration": 90_000,
             "startTime": ROOT_START_US, "references": []},
            {"spanID": "r2", "processID": "p-missing", "operationName": "work", "duration": 30_000,
             "startTime": ROOT_START_US + 1_000,
             "references": [{"refType": "CHILD_OF", "spanID": "r1"}]},
        ],
    }
    monkeypatch.setattr(httpx, "get", _fake_get(traces=[payload]))

    tr = live.get_trace("orphan")
    assert tr["spans"][1]["service"] == ""
    analysis = live.analyze_spans(tr["spans"])
    ids = [b["span_id"] for b in analysis["bottlenecks"]]
    assert ids == ["r1", "r2"]
    assert dict(zip(ids, [b["service"] for b in analysis["bottlenecks"]]))["r2"] == live.UNKNOWN_SERVICE
    # No edge may be claimed towards a service the trace never named.
    assert live.service_dependencies() == []


def test_offline_mode_still_analyses_the_bundled_incident(monkeypatch):
    """The fixture path must keep working unchanged after the live-mode repair."""
    from app.mcp.servers.traces import server as traces

    if not traces.OFFLINE:
        pytest.skip("TRACES_API_URL is set")
    # Offline fixtures are demo-gated (fail-closed contract).
    monkeypatch.setenv("COPILOT_DEMO_MODE", "true")
    edges = traces.deps_from_traces(traces._get_traces())
    assert ["api-gateway", "checkout-svc"] in edges
    res = traces.analyze_critical_path("tr-checkout-err-001")
    assert res["fault_span"]["operation"] == "applyDiscount"
