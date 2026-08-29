"""Second-round regression tests for the traces MCP server's live (Jaeger) mode.

The first repair restored `service`/`parent` on the live get_trace path but left
five defects that only show up against a *real* jaeger-query rather than a stub
that answers 200 to anything:

  * /api/traces was called without the `service` parameter jaeger-query requires,
    so service_dependencies() and get_exemplars() — the two callers that name no
    service — failed live with an HTTP error;
  * a span that recorded a handled exception was classified as an error span even
    when it explicitly reported otel.status_code=OK, so a recovered call became
    the fault span;
  * analyze_spans still indexed spans with sp["span_id"], raising KeyError on a
    malformed span;
  * the friendly "trace not found" answer was unreachable because a real Jaeger
    replies 404 and raise_for_status() fired first;
  * a non-object element in the `data` array raised AttributeError.

The stub below therefore behaves like jaeger-query does: it lists services on
/api/services, rejects a service-less search with HTTP 400, and answers 404 for
an unknown trace.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest import mock

import pytest

SERVER_PATH = Path(__file__).resolve().parents[1] / "app" / "mcp" / "servers" / "traces" / "server.py"
JAEGER_URL = "http://jaeger.test:16686"
START_US = 1_750_672_875_000_000


def _span(span_id, process, operation, duration_us, offset_us, parent=None, tags=None, logs=None):
    span = {
        "spanID": span_id, "processID": process, "operationName": operation,
        "duration": duration_us, "startTime": START_US + offset_us,
        "references": ([{"refType": "CHILD_OF", "spanID": parent}] if parent else []),
        "tags": [{"key": k, "value": v} for k, v in (tags or {}).items()],
    }
    if logs:
        span["logs"] = [{"timestamp": START_US + offset_us,
                         "fields": [{"key": k, "value": v} for k, v in log.items()]} for log in logs]
    return span


# checkout-svc retried a call that threw, and succeeded: the span records the
# exception but reports otel.status_code=OK. inventory-svc genuinely failed.
RECOVERED_TRACE = {
    "traceID": "recovered",
    "processes": {"p1": {"serviceName": "api-gateway"}, "p2": {"serviceName": "checkout-svc"}},
    "spans": [
        _span("g1", "p1", "POST /api/checkout", 300_000, 0,
              tags={"otel.status_code": "OK", "http.status_code": 200}),
        _span("c1", "p2", "chargeWithRetry", 250_000, 1_000, parent="g1",
              tags={"otel.status_code": "OK", "http.status_code": 200},
              logs=[{"event": "exception", "exception.message": "transient DNS blip (retried, succeeded)"}]),
    ],
}

# Two services, both of which report the same trace — Jaeger returns a trace once
# per service it touches, so a fan-out must not count it twice.
SHARED_TRACE = {
    "traceID": "shared",
    "processes": {"p1": {"serviceName": "api-gateway"}, "p2": {"serviceName": "checkout-svc"}},
    "spans": [
        _span("s1", "p1", "POST /api/checkout", 500_000, 0),
        _span("s2", "p2", "checkout", 400_000, 1_000, parent="s1", tags={"error": True}),
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


class FakeJaeger:
    """A stub that refuses the requests a real jaeger-query refuses."""

    def __init__(self, traces):
        self.traces = traces
        self.calls: list[tuple[str, dict]] = []

    def services_of(self, trace):
        return sorted({p["serviceName"] for p in (trace.get("processes") or {}).values()})

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/api/services"):
            return _Resp({"data": sorted({s for t in self.traces for s in self.services_of(t)})})
        if "/api/traces/" in url:
            trace_id = url.rsplit("/", 1)[-1]
            hit = [t for t in self.traces if t.get("traceID") == trace_id]
            if not hit:
                return _Resp({"data": None, "errors": [{"code": 404, "msg": "trace not found"}]}, 404)
            return _Resp({"data": hit})
        if not (params or {}).get("service"):
            return _Resp({"data": None,
                          "errors": [{"code": 400, "msg": "parameter 'service' is required"}]}, 400)
        wanted = params["service"]
        return _Resp({"data": [t for t in self.traces if wanted in self.services_of(t)]})


@pytest.fixture()
def live():
    """Import a private copy of the server with TRACES_API_URL set (OFFLINE is
    decided at import time), leaving the shared offline import untouched."""
    spec = importlib.util.spec_from_file_location("traces_server_remainder_test", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"TRACES_API_URL": JAEGER_URL}):
        spec.loader.exec_module(module)
    assert module.OFFLINE is False
    return module


@pytest.fixture()
def jaeger(live, monkeypatch):
    def _install(traces):
        import httpx

        fake = FakeJaeger(traces)
        monkeypatch.setattr(httpx, "get", fake.get)
        return fake

    return _install


# --------------------------------------------------------------------------- #
# 1. every /api/traces request names a service
# --------------------------------------------------------------------------- #
def test_service_less_search_asks_jaeger_which_services_exist(live, jaeger):
    fake = jaeger([SHARED_TRACE])

    rows = live.search_traces(limit=10)

    searches = [params for url, params in fake.calls if url.endswith("/api/traces")]
    assert searches, "no search was issued"
    assert all(p.get("service") for p in searches), searches
    assert [p["service"] for p in searches] == ["api-gateway", "checkout-svc"]
    assert [r["trace_id"] for r in rows] == ["shared"], "a trace seen under two services was counted twice"


def test_dependency_graph_and_exemplars_work_without_a_named_service(live, jaeger):
    """Both call search with no service; against a real Jaeger that was an HTTP
    400, so neither tool worked live regardless of the span normalisation."""
    jaeger([SHARED_TRACE])

    assert live.service_dependencies() == [["api-gateway", "checkout-svc"]]
    assert live.analyze_blast_radius("checkout-svc")["affected_upstream"] == ["api-gateway"]

    window = live._iso_utc(START_US)
    assert [t["trace_id"] for t in live.get_exemplars(window, window)] == ["shared"]


def test_named_service_search_is_a_single_request(live, jaeger):
    fake = jaeger([SHARED_TRACE])

    assert [r["trace_id"] for r in live.search_traces(service="checkout-svc")] == ["shared"]
    assert [url for url, _ in fake.calls] == [f"{JAEGER_URL}/api/traces"]


def test_error_body_with_null_data_yields_no_rows_not_a_crash(live, monkeypatch):
    """jaeger-query sends `"data": null` (not []) alongside its errors array."""
    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, params=None, timeout=None: _Resp(
        {"data": None, "errors": [{"code": 400, "msg": "nope"}]} if url.endswith("/api/services")
        else {"data": None}))
    assert live.search_traces() == []


# --------------------------------------------------------------------------- #
# 2. a recorded exception is not a failed span
# --------------------------------------------------------------------------- #
def test_recovered_exception_with_status_ok_is_not_an_error(live, jaeger):
    jaeger([RECOVERED_TRACE])

    tr = live.get_trace("recovered")
    recovered = next(sp for sp in tr["spans"] if sp["span_id"] == "c1")
    assert recovered["status"] == "ok"
    assert "error" not in recovered
    assert tr["status"] == "ok"

    analysis = live.analyze_critical_path("recovered")
    assert analysis["error_spans"] == []
    assert analysis["fault_span"] is None
    # A healthy trace must not be offered as an error exemplar.
    assert live.search_traces(error_only=True) == []


def test_status_ok_beats_a_5xx_tag_but_an_explicit_error_still_wins(live):
    ok_despite_5xx = {"tags": [{"key": "otel.status_code", "value": "OK"},
                               {"key": "http.status_code", "value": 503}]}
    assert live._span_status(ok_despite_5xx) == ("ok", "")

    explicit = {"tags": [{"key": "otel.status_code", "value": "STATUS_CODE_ERROR"},
                         {"key": "exception.message", "value": "boom"}]}
    assert live._span_status(explicit) == ("error", "boom")

    error_tag_wins = {"tags": [{"key": "error", "value": True},
                               {"key": "otel.status_code", "value": "OK"}]}
    assert live._span_status(error_tag_wins)[0] == "error"


def test_an_exception_on_a_span_with_no_status_is_still_an_error(live):
    """Without an explicit status the exception record is the only failure signal
    there is — dropping it would report "no error spans" on a broken trace."""
    unset = {"tags": [], "logs": [{"fields": [
        {"key": "event", "value": "exception"},
        {"key": "exception.message", "value": "TypeError: cannot read 'total'"},
    ]}]}
    assert live._span_status(unset) == ("error", "TypeError: cannot read 'total'")


# --------------------------------------------------------------------------- #
# 3. a malformed span degrades instead of raising
# --------------------------------------------------------------------------- #
def test_analyze_spans_tolerates_a_span_without_an_id(live):
    spans = [
        {"span_id": "s1", "service": "api-gateway", "operation": "GET /", "duration_ms": 90},
        {"service": "checkout-svc", "operation": "work", "duration_ms": 30, "parent": "s1"},
    ]
    analysis = live.analyze_spans(spans)

    assert [b["operation"] for b in analysis["bottlenecks"]] == ["GET /", "work"]
    # The id-less span still declares its parent, so its 30ms is still subtracted
    # from the parent's self time; it simply cannot be a parent itself.
    assert analysis["bottlenecks"][0]["self_time_ms"] == 60
    assert analysis["bottlenecks"][1]["self_time_ms"] == 30
    assert analysis["fault_span"] is None


def test_analyze_spans_does_not_hang_on_a_parent_cycle(live):
    spans = [
        {"span_id": "a", "service": "svc", "operation": "a", "duration_ms": 5, "parent": "b"},
        {"span_id": "b", "service": "svc", "operation": "b", "duration_ms": 5, "parent": "a",
         "status": "error"},
    ]
    assert live.analyze_spans(spans)["fault_span"]["operation"] in {"a", "b"}


# --------------------------------------------------------------------------- #
# 4. + 5. the real "unknown trace" answer, and an unreadable document
# --------------------------------------------------------------------------- #
def test_unknown_trace_reports_not_found_on_a_404(live, jaeger):
    """A real jaeger-query answers 404, so the friendly branch has to be decided
    before raise_for_status() turns it into an HTTPStatusError."""
    jaeger([SHARED_TRACE])

    res = live.get_trace("no-such-trace")
    assert "not found" in res["error"]
    assert "no-such-trace" in res["error"]
    assert "trace not found" in res["error"]  # the backend's own words
    assert res.get("spans") is None
    assert "not found" in live.analyze_critical_path("no-such-trace")["error"]


def test_a_real_http_failure_is_still_raised_not_reported_as_not_found(live, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, params=None, timeout=None: _Resp({"data": None}, 503))
    with pytest.raises(httpx.HTTPStatusError):
        live.get_trace("abc123")


def test_non_object_trace_document_is_reported_not_crashed(live, monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get",
                        lambda url, params=None, timeout=None: _Resp({"data": ["abc123"]}))
    res = live.get_trace("abc123")
    assert "abc123" in res["error"] and "str" in res["error"]
    assert res.get("spans") is None
