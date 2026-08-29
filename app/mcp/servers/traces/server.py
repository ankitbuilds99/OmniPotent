"""Distributed-traces MCP server — span search + blast-radius reasoning.

Traces are how a copilot localizes a failure to a specific service/span and
follows a request across microservices — the capability above logs+metrics, and
the input to blast-radius reasoning (separating cause from symptom). When
`TRACES_API_URL` is set it queries a Jaeger-compatible query API; otherwise it
runs in OFFLINE DEMO mode over bundled OpenTelemetry-style fixtures tied to the
checkout-svc incident (the failing span is applyDiscount in checkout-svc, while
its downstream calls are healthy — so cause ≠ symptom).

Tools:
  - search_traces:          recent traces (filter by service / errors / latency)
  - get_trace:              the full span tree for a trace
  - service_dependencies:   the service dependency graph derived from spans
  - analyze_blast_radius:   given a failing service, who is affected (upstream)
                            vs. what it depends on (downstream candidate causes)

Run standalone:
    python -m app.mcp.servers.traces.server
"""

from __future__ import annotations

import os
from collections import deque
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

TRACES_API_URL = os.environ.get("TRACES_API_URL", "").strip()
OFFLINE = not TRACES_API_URL

mcp = FastMCP("traces")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Offline fixtures — multi-scenario support via centralized fixture system
# --------------------------------------------------------------------------- #
def _get_traces() -> list[dict[str, Any]]:
    from app.mcp.fixtures import get_fixture
    traces = get_fixture("traces", "traces")
    return traces if traces else _FALLBACK_TRACES


_FALLBACK_TRACES: list[dict[str, Any]] = [
    {
        "trace_id": "tr-checkout-err-001", "root_service": "api-gateway",
        "operation": "POST /api/checkout", "duration_ms": 512, "status": "error",
        "start": "2026-06-23T10:01:15Z", "span_count": 4,
        "spans": [
            {"span_id": "s1", "service": "api-gateway", "operation": "POST /api/checkout",
             "duration_ms": 512, "status": "error", "parent": None},
            {"span_id": "s2", "service": "checkout-svc", "operation": "checkout",
             "duration_ms": 480, "status": "error", "parent": "s1"},
            {"span_id": "s3", "service": "checkout-svc", "operation": "applyDiscount",
             "duration_ms": 2, "status": "error", "parent": "s2",
             "error": "TypeError: Cannot read properties of undefined (reading 'total') "
                      "at applyDiscount (checkout.js:42)"},
            {"span_id": "s4", "service": "inventory-svc", "operation": "GET /stock",
             "duration_ms": 44, "status": "ok", "parent": "s2"},
        ],
    },
    {
        "trace_id": "tr-checkout-ok-002", "root_service": "api-gateway",
        "operation": "POST /api/checkout", "duration_ms": 233, "status": "ok",
        "start": "2026-06-23T09:59:11Z", "span_count": 4,
        "spans": [
            {"span_id": "a1", "service": "api-gateway", "operation": "POST /api/checkout",
             "duration_ms": 233, "status": "ok", "parent": None},
            {"span_id": "a2", "service": "checkout-svc", "operation": "checkout",
             "duration_ms": 201, "status": "ok", "parent": "a1"},
            {"span_id": "a3", "service": "inventory-svc", "operation": "GET /stock",
             "duration_ms": 41, "status": "ok", "parent": "a2"},
            {"span_id": "a4", "service": "payment-svc", "operation": "POST /charge",
             "duration_ms": 120, "status": "ok", "parent": "a2"},
        ],
    },
]


# --------------------------------------------------------------------------- #
# Live mode — normalising a Jaeger payload into the fixture span shape
#
# Everything downstream of `get_trace` (the dependency graph, blast radius,
# incident correlation, critical-path/fault localization) reasons over spans
# that carry `service` and `parent`. Live mode used to map a Jaeger span to only
# span_id/operation/duration_ms, so every one of those tools raised
# KeyError('service') the moment an operator pointed TRACES_API_URL at a real
# Jaeger — the four headline traces capabilities worked in demo mode only. The
# normalisers below reconstruct the full fixture shape from the wire format:
# service comes from the trace-level `processes` map (or an inlined `process`),
# parentage from the CHILD_OF reference, and status/error from tags and logs,
# because Jaeger has no first-class span status field.
# --------------------------------------------------------------------------- #
UNKNOWN_SERVICE = "unknown-service"


def _tags_of(span: dict) -> dict[str, Any]:
    return {t.get("key"): t.get("value") for t in (span.get("tags") or []) if t.get("key")}


def _span_service(span: dict, processes: dict) -> str:
    """Jaeger stores the emitting service once per trace under `processes` and
    references it from each span via `processID`; some exporters inline it as
    `process` instead. Return "" when neither is present rather than inventing a
    name — an unattributed span must stay visibly unattributed."""
    inline = span.get("process")
    if isinstance(inline, dict) and inline.get("serviceName"):
        return str(inline["serviceName"])
    entry = (processes or {}).get(span.get("processID")) or {}
    if isinstance(entry, dict) and entry.get("serviceName"):
        return str(entry["serviceName"])
    return ""


def _span_parent(span: dict) -> str | None:
    """Parentage lives in `references`, not in a parent field. Prefer CHILD_OF
    (the true call edge); fall back to any reference so a FOLLOWS_FROM tree still
    yields a graph instead of a flat list of orphans."""
    refs = [r for r in (span.get("references") or []) if isinstance(r, dict) and r.get("spanID")]
    for ref in refs:
        if ref.get("refType") == "CHILD_OF":
            return str(ref["spanID"])
    return str(refs[0]["spanID"]) if refs else None


def _recorded_failure(span: dict, tags: dict[str, Any]) -> str:
    """Return the human-readable reason a span recorded a failure, or "".

    The text may live in an OpenTracing `error.*` tag, an OpenTelemetry
    `exception.*` tag, `otel.status_description`, or — most often for exceptions —
    in a span log whose `event` field is `error`/`exception`."""
    for key in ("error.message", "error.object", "exception.message", "exception.type",
                "otel.status_description"):
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for log in span.get("logs") or []:
        if not isinstance(log, dict):
            continue
        fields = {f.get("key"): f.get("value") for f in (log.get("fields") or [])
                  if isinstance(f, dict) and f.get("key")}
        value = fields.get("error.object") or fields.get("exception.message") or fields.get("message")
        if fields.get("event") in {"error", "exception"} and isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _span_status(span: dict) -> tuple[str, str]:
    """Decide ok/error for a Jaeger span and recover the human-readable reason.

    Jaeger has no first-class status field, so the signals have to be ranked. An
    explicit verdict from the instrumentation wins: the OpenTelemetry
    `otel.status_code` or the classic OpenTracing `error` tag. Only when the span
    states no status at all do we fall back to circumstantial evidence — a 5xx
    `http.status_code`, or a recorded error/exception — because without that
    fallback fault localization reports "no error spans" on a trace that plainly
    failed.

    The ranking matters in both directions. This used to force error whenever an
    `exception.*` tag or an `event: exception` log was present, which contradicts
    OpenTelemetry: record_exception() and set_status() are deliberately separate,
    so a span that caught an exception, retried and succeeded sets status OK while
    still carrying the exception record. Such a span was reported as an error span
    and, being the deepest one, became the fault span — naming a recovered call as
    the cause of the incident. A span that says OK is now reported as ok, and its
    recorded exception is not surfaced as the span's error."""
    tags = _tags_of(span)
    # Some exporters emit the proto enum name (STATUS_CODE_ERROR) instead of ERROR.
    otel = str(tags.get("otel.status_code", "")).strip().upper()
    otel = otel[len("STATUS_CODE_"):] if otel.startswith("STATUS_CODE_") else otel
    error_tag = str(tags.get("error", "")).strip().lower() in {"true", "1"}
    recorded = _recorded_failure(span, tags)

    if otel == "ERROR" or error_tag:
        failed = True
    elif otel == "OK":
        failed = False
    else:
        failed = _as_int(tags.get("http.status_code"), 0) >= 500 or bool(recorded)
    if not failed:
        return "ok", ""
    return "error", recorded


def _iso_utc(micros: Any) -> str:
    """Jaeger timestamps are microseconds since the epoch. Return "" for a missing
    one so a trace with no reported start is not silently dated 1970."""
    value = _as_int(micros, 0)
    if value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trace_from_jaeger(payload: dict, trace_id: str = "") -> dict[str, Any]:
    """Convert one Jaeger trace document into the same shape the offline fixtures
    use, so live and demo mode feed the pure analysis helpers identical spans.

    A payload that is not an object at all is treated as a trace with no spans:
    callers report that as an unreadable document, which is a better answer than
    an AttributeError from deep inside the normaliser."""
    if not isinstance(payload, dict):
        payload = {}
    processes = payload.get("processes") or {}
    raw_spans = [sp for sp in (payload.get("spans") or []) if isinstance(sp, dict)]
    spans: list[dict[str, Any]] = []
    for raw in raw_spans:
        status, error = _span_status(raw)
        span: dict[str, Any] = {
            "span_id": raw.get("spanID"),
            "service": _span_service(raw, processes),
            "operation": raw.get("operationName"),
            "duration_ms": round(_as_int(raw.get("duration"), 0) / 1000),
            "status": status,
            "parent": _span_parent(raw),
            "start": _iso_utc(raw.get("startTime")),
        }
        if error:
            span["error"] = error
        spans.append(span)

    # The root is the span nobody in this trace parents; Jaeger does not guarantee
    # it is first in the list, so pick it explicitly (earliest start wins on ties).
    known = {sp["span_id"] for sp in spans}
    roots = [sp for sp in spans if not sp.get("parent") or sp["parent"] not in known]
    root = min(roots or spans, key=lambda sp: sp.get("start") or "\uffff") if spans else {}
    return {
        "trace_id": payload.get("traceID") or trace_id,
        "root_service": root.get("service", ""),
        "operation": root.get("operation"),
        "duration_ms": root.get("duration_ms", 0),
        "status": "error" if any(sp["status"] == "error" for sp in spans) else "ok",
        "start": root.get("start", ""),
        "span_count": len(spans),
        "spans": spans,
    }


# --------------------------------------------------------------------------- #
# Live mode — talking to jaeger-query
# --------------------------------------------------------------------------- #
SUMMARY_KEYS = ("trace_id", "root_service", "operation", "duration_ms", "status", "start", "span_count")


def _jaeger_get(path: str, params: dict | None = None):
    import httpx

    return httpx.get(f"{TRACES_API_URL}{path}", params=params, timeout=20)


def _payload_data(resp) -> list:
    """The `data` array of a jaeger-query response, always as a list.

    jaeger-query answers errors with `"data": null` and a populated `errors`
    array, so `.get("data", [])` still hands back a None that nothing can iterate.
    A body that is not JSON, or not an object, is treated the same way: no data."""
    try:
        body = resp.json()
    except Exception:
        return []
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, list) else []


def _jaeger_reason(resp) -> str:
    """jaeger-query explains refusals in an `errors: [{code, msg}]` array. Quote it
    verbatim so the operator sees the backend's own reason rather than ours."""
    try:
        body = resp.json()
    except Exception:
        return ""
    errors = body.get("errors") if isinstance(body, dict) else None
    return "; ".join(
        str(e["msg"]) for e in (errors or []) if isinstance(e, dict) and e.get("msg")
    )


def _jaeger_services() -> list[str]:
    """Every service jaeger-query knows about, sorted so a fan-out is deterministic."""
    resp = _jaeger_get("/api/services")
    resp.raise_for_status()
    return sorted({s.strip() for s in _payload_data(resp) if isinstance(s, str) and s.strip()})


def _live_traces(service: str | None, error_only: bool, min_dur: int, limit: int) -> list[dict]:
    """Fetch traces from jaeger-query and normalise them into the fixture shape.

    /api/traces *requires* a `service` parameter — jaeger-query rejects the request
    with "parameter 'service' is required" without one (the repo's other Jaeger
    client, app/knowledge_graph/sync_engine.py, always sends it). The live search
    branch used to send only `limit`, so the two callers that legitimately have no
    service to name — service_dependencies(), which wants the whole graph, and
    get_exemplars(), which pivots on a time window — could not work against a real
    backend at all: setting TRACES_API_URL turned them from a KeyError into an
    HTTP error. When the caller names no service we now ask which services exist
    and query them in turn, stopping as soon as `limit` distinct traces are in
    hand; naming a service (the common case) is still a single request.

    Filtering happens on the normalised trace rather than on the wire, so live and
    offline mode answer the same question. In particular error_only is not pushed
    down to Jaeger's tag filter: that matches only spans literally tagged
    error=true and would silently drop traces whose failure is a 5xx status or a
    recorded exception, which _span_status does count.
    """
    services = [service] if service else _jaeger_services()
    out: list[dict] = []
    seen: set[str] = set()
    for name in services:
        resp = _jaeger_get("/api/traces", {"service": name, "limit": limit})
        resp.raise_for_status()
        for payload in _payload_data(resp):
            if not isinstance(payload, dict):
                continue
            tr = trace_from_jaeger(payload)
            # A trace that crosses N services is returned once per service.
            if tr["trace_id"] and tr["trace_id"] in seen:
                continue
            seen.add(tr["trace_id"])
            if error_only and tr["status"] != "error":
                continue
            if min_dur and tr["duration_ms"] < min_dur:
                continue
            if service and not any(sp.get("service") == service for sp in tr["spans"]):
                continue
            out.append(tr)
        if len(out) >= limit:
            break
    return out[:limit]


def deps_from_traces(traces: list[dict]) -> list[list[str]]:
    """Derive caller→callee service edges from span parent links. Pure + sorted
    so the dependency graph is deterministic.

    Spans whose service could not be attributed are skipped rather than defaulted:
    an edge to a made-up service name would be a claim the trace never made."""
    span_service: dict[str, str] = {}
    for tr in traces:
        for sp in tr.get("spans", []):
            service = sp.get("service")
            if sp.get("span_id") and service:
                span_service[sp["span_id"]] = service
    edges: set[tuple[str, str]] = set()
    for tr in traces:
        for sp in tr.get("spans", []):
            parent = sp.get("parent")
            callee = sp.get("service")
            if callee and parent and parent in span_service:
                caller = span_service[parent]
                if caller != callee:
                    edges.add((caller, callee))
    return [list(e) for e in sorted(edges)]


def blast_radius(edges: list[list[str]], failing_service: str) -> dict:
    """Given service dependency edges (caller→callee) and a failing service,
    return who is AFFECTED (callers, transitively upstream) vs. its DOWNSTREAM
    dependencies (candidate causes to rule in/out). Pure + unit-testable."""
    upstream: dict[str, list[str]] = {}
    downstream: dict[str, list[str]] = {}
    for caller, callee in edges:
        downstream.setdefault(caller, []).append(callee)
        upstream.setdefault(callee, []).append(caller)

    def _bfs(graph: dict[str, list[str]], start: str) -> list[str]:
        seen: set[str] = set()
        q = deque(graph.get(start, []))
        while q:
            node = q.popleft()
            if node in seen:
                continue
            seen.add(node)
            q.extend(graph.get(node, []))
        return sorted(seen)

    return {
        "failing_service": failing_service,
        "affected_upstream": _bfs(upstream, failing_service),
        "downstream_dependencies": _bfs(downstream, failing_service),
    }


def analyze_spans(spans: list[dict]) -> dict:
    """Latency attribution + fault localization over a span tree. Pure + testable.

    self_time = a span's own duration minus its direct children's durations, so the
    bottleneck is attributed to the span actually doing the work — not its slow
    parent. Separately surfaces error spans and the *fault span* (the deepest error
    span), since on an error incident the fault is often a fast span the latency
    view would overlook.

    A span the backend could not attribute to a service is still reported — its
    timings are real — but it is labelled UNKNOWN_SERVICE so the reader can tell
    "the trace did not say" apart from a genuine service name. A span that arrived
    without an id is handled the same way: it is read with .get() throughout and
    simply parents nothing, because one malformed span in a thousand-span trace
    must not turn the whole critical-path analysis into a KeyError.
    """
    by_id = {sp["span_id"]: sp for sp in spans if sp.get("span_id")}
    children: dict[str, list[dict]] = {}
    for sp in spans:
        parent = sp.get("parent")
        if parent:
            children.setdefault(parent, []).append(sp)

    def _depth(sp: dict) -> int:
        # `seen` guards against a parent cycle in a malformed trace, which would
        # otherwise spin here forever instead of returning a depth.
        d, cur, seen = 0, sp, {id(sp)}
        while cur.get("parent") and cur["parent"] in by_id:
            cur = by_id[cur["parent"]]
            if id(cur) in seen:
                break
            seen.add(id(cur))
            d += 1
        return d

    bottlenecks = []
    for sp in spans:
        # A span with no id has no children registered against it -- `children`
        # is keyed only by truthy `parent` values -- so the lookup was already
        # returning [] for one; this just says so without passing None as a key.
        span_id = sp.get("span_id")
        kids = children.get(span_id, []) if span_id else []
        self_time = max(0, sp.get("duration_ms", 0) - sum(c.get("duration_ms", 0) for c in kids))
        bottlenecks.append({
            "span_id": sp.get("span_id"), "service": sp.get("service") or UNKNOWN_SERVICE,
            "operation": sp.get("operation"),
            "duration_ms": sp.get("duration_ms", 0), "self_time_ms": self_time,
            "status": sp.get("status", "ok"),
        })
    bottlenecks.sort(key=lambda b: b["self_time_ms"], reverse=True)

    error_spans = [sp for sp in spans if sp.get("status") == "error" or sp.get("error")]
    fault = max(error_spans, key=_depth) if error_spans else None
    return {
        "bottlenecks": bottlenecks,
        "error_spans": [
            {"span_id": sp.get("span_id"), "service": sp.get("service") or UNKNOWN_SERVICE,
             "operation": sp.get("operation"), "error": sp.get("error", "")} for sp in error_spans
        ],
        "fault_span": (
            {"span_id": fault.get("span_id"), "service": fault.get("service") or UNKNOWN_SERVICE,
             "operation": fault.get("operation"), "error": fault.get("error", "")} if fault else None
        ),
    }


@mcp.tool()
def analyze_critical_path(trace_id: str) -> dict:
    """Attribute latency by SELF-time (a span's own work, not its children's) and
    localize the FAULT to the deepest error span — so the agent blames the span
    actually responsible, not its slow parent. Returns ranked bottlenecks, error
    spans, and the fault span."""
    tr = get_trace(trace_id)
    if tr.get("error"):
        return tr
    return {"trace_id": trace_id, **analyze_spans(list(tr.get("spans", [])))}


@mcp.tool()
def get_exemplars(start: str, end: str, error_only: bool = True, limit: int | str = 5) -> list[dict]:
    """Exemplar pivot: given a time window (e.g. the bucket where a metric anomaly
    fired), return representative traces whose start falls inside it — a
    deterministic anomaly → trace jump instead of guessing which trace to read.
    Timestamps are ISO-8601 UTC (lexicographic compare is chronological)."""
    limit = _as_int(limit, 5)
    rows = search_traces(error_only=error_only, limit=100)
    hits = [r for r in rows if start <= str(r.get("start", "")) <= end]
    return hits[:limit]


@mcp.tool()
def search_traces(
    service: str | None = None, error_only: bool = False,
    min_duration_ms: int | str = 0, limit: int | str = 20,
) -> list[dict]:
    """Search recent traces (summary rows). Filter by service, errors only, or a
    minimum root duration — the entry point for following a slow/failing request."""
    limit = _as_int(limit, 20)
    min_dur = _as_int(min_duration_ms, 0)
    if OFFLINE:
        out = []
        for tr in _get_traces():
            if error_only and tr["status"] != "error":
                continue
            if min_dur and tr["duration_ms"] < min_dur:
                continue
            if service and not any(sp["service"] == service for sp in tr["spans"]):
                continue
            out.append({k: tr[k] for k in SUMMARY_KEYS})
        return out[:limit]

    # Live rows go through the same normaliser as get_trace, so they carry a real
    # root_service and start (they used to be blank and absent, which broke
    # get_exemplars' window filter) and honour min_duration_ms, which the live
    # branch previously ignored outright.
    return [{k: tr[k] for k in SUMMARY_KEYS}
            for tr in _live_traces(service, error_only, min_dur, limit)]


@mcp.tool()
def get_trace(trace_id: str) -> dict:
    """Return the full span tree for a trace (service, operation, duration, status,
    parent, error) — to localize exactly which span failed or was slow."""
    if OFFLINE:
        tr = next((t for t in _get_traces() if t["trace_id"] == trace_id), None)
        return tr or {"error": f"unknown trace '{trace_id}' (offline demo has "
                      f"{[t['trace_id'] for t in _get_traces()]})"}

    resp = _jaeger_get(f"/api/traces/{trace_id}")
    data = _payload_data(resp)
    payload = next((row for row in data if isinstance(row, dict)), None)

    # An unknown trace is a 404 from a real jaeger-query, not a 200 with an empty
    # array, so the "not found" answer has to be decided before raise_for_status()
    # turns it into an HTTPStatusError; the empty-array form is kept because some
    # gateways in front of Jaeger answer that way instead. Either way the point is
    # to name the missing trace rather than return a zero-span trace that reads as
    # a successful lookup of a request with no work in it.
    if resp.status_code == 404 or (resp.status_code == 200 and not data):
        reason = _jaeger_reason(resp)
        return {"error": f"trace '{trace_id}' not found at the configured traces API "
                         f"({TRACES_API_URL})" + (f": {reason}" if reason else "")}
    resp.raise_for_status()
    if payload is None:
        # 200 with a `data` array holding something that is not a trace object.
        # Report the shape we actually got instead of raising AttributeError from
        # inside the normaliser.
        return {"error": f"traces API ({TRACES_API_URL}) returned no readable trace document "
                         f"for '{trace_id}' (data[0] is {type(data[0]).__name__}, expected object)"}
    return trace_from_jaeger(payload, trace_id=trace_id)


@mcp.tool()
def service_dependencies() -> list[list[str]]:
    """Return the runtime service dependency graph as caller→callee edges, derived
    from observed spans (not static config)."""
    if OFFLINE:
        return deps_from_traces(_get_traces())
    # jaeger-query's search returns whole trace documents, not summaries, so the
    # spans needed for the edges are already in hand — this used to re-fetch each
    # trace individually, one HTTP round trip per result, for data it already had.
    return deps_from_traces(_live_traces(None, False, 0, 100))


@mcp.tool()
def analyze_blast_radius(failing_service: str) -> dict:
    """Given a failing service, compute who is AFFECTED (upstream callers,
    transitively) vs. its DOWNSTREAM dependencies (candidate causes) — the
    cause-vs-symptom separation that focuses an investigation."""
    edges = service_dependencies()
    result = blast_radius(edges, failing_service)
    result["edges"] = edges
    return result


@mcp.tool()
def correlate_incidents(services: list[str]) -> dict:
    """Given several services alerting at once (an incident 'storm'), use the
    dependency graph to rank which one the others most likely cascade FROM — so you
    chase ONE shared root cause instead of N incidents. Returns the ranked
    candidates and the single most-likely shared root (`likely_root`)."""
    from app.correlation import correlate_storm

    result = correlate_storm(list(services or []), service_dependencies())
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
