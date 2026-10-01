"""FastAPI surface for OmniPotent.

This module provides the REST API for the OmniPotent AI-powered incident
investigation system. It supports:

- **Chat/Investigation**: Interactive incident investigation with AI assistance
- **Approval Workflow**: Human-in-the-loop approval for risky actions
- **Streaming**: Real-time Server-Sent Events for live investigation progress
- **Webhooks**: PagerDuty, Slack, and Stripe integrations
- **Multi-tenant**: Organization isolation with RBAC and usage metering
- **Admin**: Organization management, API keys, and integrations

Endpoints:
    POST /chat     -> start an investigation; returns final answer or an
                      approval request (with the thread_id to resume).
    POST /approve  -> resume a paused investigation with a human decision.
    GET  /healthz  -> liveness probe.

Sessions are keyed by thread_id and kept alive in-process for the demo. A
production deployment would externalize this (the checkpointer already persists
graph state to SQLite/Postgres, so sessions are reconstructable).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable, Iterator
from typing import Any
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import anyio
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocketException
from starlette.requests import HTTPConnection
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from app.security.headers import SecurityHeadersMiddleware, csp_connect_sources

from app import (
    audit,
    feedback,
    investigation_history,
    metering,
    metrics_source,
    model_catalog,
    observability,
    runtime,
    tenant_context,
)
from app.config import ROOT, get_settings
from app.integrations import pagerduty as pd_webhook
from app.integrations import slack
from app.llm import active_api_key, resolved_models
from app.session import CopilotSession, TurnResult
from app.tenancy import auth as tenant_auth
from app.tenancy import models as tn_models

# Activate logging + LangSmith tracing before any model/graph is built.
observability.init()
log = logging.getLogger("omnipotent.api")

# Static catalog of the MCP servers + their tools, surfaced to the UI sidebar.
# Mirrors app/mcp/client.py — kept here so /config needs no server subprocesses.
MCP_CATALOG = [
    {
        "name": "datadog",
        "label": "Datadog",
        "custom": True,
        "tools": [
            "search_logs", "get_error_summary", "cluster_logs", "get_metric", "list_services",
            "detect_anomaly", "compute_burn_rate", "onset_timeline",
        ],
    },
    {
        "name": "repo",
        "label": "Repository",
        "custom": True,
        "tools": ["list_dir", "read_file", "grep", "git_log"],
    },
    {
        "name": "github",
        "label": "GitHub",
        "custom": True,
        "tools": [
            "list_recent_commits", "get_commit_diff", "correlate_changes", "first_bad_deploy",
            "list_workflow_runs", "get_failed_job_logs", "create_pull_request",
        ],
    },
    {
        "name": "pagerduty",
        "label": "PagerDuty",
        "custom": True,
        "tools": [
            "list_incidents", "get_incident", "get_incident_alerts",
            "add_incident_note", "acknowledge_incident", "resolve_incident",
        ],
    },
    {
        "name": "kubernetes",
        "label": "Kubernetes",
        "custom": True,
        "tools": [
            "list_pods", "describe_pod", "get_events", "get_deployment_status",
            "rollout_history", "scale_deployment", "rollback_deployment", "restart_deployment",
        ],
    },
    {
        "name": "sentry",
        "label": "Sentry",
        "custom": True,
        "tools": ["list_issues", "get_issue", "get_latest_event"],
    },
    {
        "name": "memory",
        "label": "Incident memory",
        "custom": True,
        "tools": ["search_incidents", "get_incident_record"],
    },
    {
        "name": "traces",
        "label": "Traces",
        "custom": True,
        "tools": [
            "search_traces", "get_trace", "service_dependencies", "analyze_blast_radius",
            "analyze_critical_path", "get_exemplars", "correlate_incidents",
        ],
    },
    {
        "name": "deploys",
        "label": "Deploys",
        "custom": True,
        "tools": ["list_deploys", "get_deploy", "deploys_in_window"],
    },
    {
        "name": "argocd",
        "label": "ArgoCD",
        "custom": True,
        "tools": [
            "list_applications", "get_application", "get_application_history",
            "get_application_resources", "get_sync_status", "sync_application",
            "rollback_application",
        ],
    },
    {
        "name": "terraform",
        "label": "Terraform",
        "custom": True,
        "tools": [
            "list_workspaces", "get_workspace", "get_state", "get_run_history",
            "get_run_details", "get_plan_output", "list_state_resources",
            "trigger_plan", "trigger_apply",
        ],
    },
]

# thread_id -> live session (insertion-ordered; used for LRU eviction).
_SESSIONS: dict[str, CopilotSession] = {}
# Serializes session creation/reconstruction so concurrent first-requests for the
# same thread don't each spin up (and leak) a session.
_SESSION_LOCK = asyncio.Lock()
# Per-thread locks: one thread can't run two turns at once, WITHOUT serializing
# unrelated threads (so different users investigate in parallel).
_THREAD_LOCKS: dict[str, asyncio.Lock] = {}
# Liveness bookkeeping for safe eviction: threads with a turn in flight, threads
# paused awaiting approval, and last-use timestamps (for LRU).
_RUNNING: set[str] = set()
_AWAITING: set[str] = set()
_LAST_USED: dict[str, float] = {}


def _thread_lock(thread_id: str) -> asyncio.Lock:
    lock = _THREAD_LOCKS.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _THREAD_LOCKS[thread_id] = lock
    return lock


class _ConfigGate:
    """A writer-preferring reader/writer gate.

    Agent turns are *readers* — many run concurrently across threads. A config
    change (model / sources / github / reset / shutdown) is the *writer* — it
    waits for in-flight turns to drain, then runs exclusively so it can never
    tear a session down mid-investigation. This replaces the old single global
    lock that forced every investigation in the whole process to run one-at-a-time.
    """

    def __init__(self) -> None:
        self._cond = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @asynccontextmanager
    async def turn(self):
        async with self._cond:
            while self._writer or self._writers_waiting:
                await self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            async with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @asynccontextmanager
    async def exclusive(self):
        async with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers > 0:
                    await self._cond.wait()
                self._writer = True
            finally:
                self._writers_waiting -= 1
        try:
            yield
        finally:
            async with self._cond:
                self._writer = False
                self._cond.notify_all()


_gate = _ConfigGate()

# Paths that bypass auth AND the limits middleware (probes + CORS preflight).
_OPEN_PATHS = frozenset({"/healthz", "/readyz", "/metrics/slo", "/frontend-config", "/auth/providers"})
# Auth callback paths need to be open for OAuth redirects
_AUTH_PATH_PREFIXES = ("/auth/",)
# Back-office paths authenticated by COPILOT_OPERATOR_TOKEN rather than a tenant API
# key. These skip tenant-key resolution in require_auth; each route still carries
# _operator_guard, which fails closed when the token is unset or wrong.
#
# The CRM and the waitlist roll-up are here because they are the deployment's own
# conversion tracking — every customer's contact details, health score and MRR,
# and every lead's email. They were behind `require_auth`, which on a
# multi-tenant deployment means any tenant API key: the cheapest self-serve
# signup could read the whole customer list and the whole waitlist.
_OPERATOR_PATHS = frozenset(
    {
        "/admin/plan",
        "/waitlist/stats",
        "/customers",
        "/customers/stats",
        "/customers/dashboard",
        "/customers/trials/active",
        "/customers/usage",
        "/customers/convert",
    }
)
# Anonymous-reachable paths. Deliberately NOT in _OPEN_PATHS: those bypass the
# limits middleware too, and an unauthenticated POST with no rate limit is a
# spam endpoint. These skip only the credential check.
_PUBLIC_PATHS = frozenset({"/signup", "/waitlist", "/waitlist/position"})

# The status page's public half. A status page is the address customers are sent
# to when the product is down, and the verification link in a subscription email
# is opened by a mail client that holds no credential at all — the single-use
# random token in that link's query string is the route's credential. Every one
# of these answered 401 on any deployment with a token configured, so nobody
# outside the tenant could read the page, nobody could subscribe, and no emailed
# address could ever be verified (`notify_subscribers` skips unverified rows, so
# the subscription was kept forever and told about nothing).
#
# Scoped by method, not by prefix, because the same router hangs its admin writes
# off the same paths: POST/DELETE on /api/status/components|incidents|maintenances
# create and delete them and POST /api/status/refresh forces a health sync, and
# none of those carry a guard of their own. Only reads plus the two subscriber
# routes are opened. `DELETE /api/status/subscribe/{id}` stays closed: nothing
# emails an unsubscribe link, so no anonymous flow needs it. A new *read* route
# under this prefix that is not meant for the public has to be excluded here.
#
# Not in _OPEN_PATHS, for the same reason as the paths above: that set also
# bypasses LimitsMiddleware, and an unauthenticated POST with neither a per-IP
# rate limit nor a body cap is a spam endpoint.
_STATUS_PAGE_PREFIX = "/api/status"
_PUBLIC_STATUS_POSTS = frozenset({"/api/status/subscribe"})


def _is_public_status_page(method: str, path: str) -> bool:
    """True for the anonymously reachable part of the status page router."""
    if path != _STATUS_PAGE_PREFIX and not path.startswith(f"{_STATUS_PAGE_PREFIX}/"):
        return False
    if method in ("GET", "HEAD"):
        return True
    return method == "POST" and path in _PUBLIC_STATUS_POSTS


# --- In-memory per-IP fixed-window rate limiter (single-instance only) ---
# {ip: (window_start_epoch, count)}. Good enough for one process; a multi-instance
# deployment should front this with a shared limiter (e.g. Redis / a gateway).
_RL: dict[str, tuple[float, int]] = {}
_RL_LOCK = asyncio.Lock()
_RL_MAX_KEYS = 10_000  # prune entries from old windows past this, to bound memory


def _client_ip(request: Request, trust_proxy: bool) -> str:
    # Only trust X-Forwarded-For behind a known proxy — otherwise any client can
    # spoof it to dodge the per-IP limiter.
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# The active limiter: in-memory (backed by _RL so tests can clear it) unless
# COPILOT_REDIS_URL selects the shared Redis backend. Built lazily so config/env
# overrides and test resets are honored.
_LIMITER: Any = None


def _get_limiter() -> Any:
    global _LIMITER
    if _LIMITER is None:
        from app.ratelimit import InMemoryRateLimiter, make_rate_limiter
        url = get_settings().copilot_redis_url
        _LIMITER = make_rate_limiter(url, store=_RL) if url.strip() else InMemoryRateLimiter(_RL)
    return _LIMITER


def _reset_limiter() -> None:
    """Drop the cached limiter (tests re-read config/env)."""
    global _LIMITER
    _LIMITER = None
    _RL.clear()


async def _rate_limited(ip: str, limit_per_min: int) -> bool:
    async with _RL_LOCK:
        return await _get_limiter().over_limit(ip, limit_per_min, window=60)


class LimitsMiddleware(BaseHTTPMiddleware):
    """Request-id propagation + body-size cap + per-IP rate limit (POSTs). Added
    inside the CORS middleware so 413/429 responses still carry CORS headers."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = observability.request_id_var.set(rid)
        try:
            response = await self._guarded(request, call_next)
            response.headers["x-request-id"] = rid
            return response
        finally:
            observability.request_id_var.reset(token)

    async def _guarded(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
            return await call_next(request)
        s = get_settings()
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > s.copilot_max_body_bytes:
            return JSONResponse({"detail": "Request body too large."}, status_code=413)
        if request.method == "POST" and s.copilot_rate_limit_per_min > 0:
            ip = _client_ip(request, s.copilot_trust_proxy)
            if await _rate_limited(ip, s.copilot_rate_limit_per_min):
                return JSONResponse(
                    {"detail": "Rate limit exceeded — slow down and retry."},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)


async def _evict(thread_id: str) -> None:
    """Drop a session and release its MCP subprocesses, ignoring teardown errors."""
    session = _SESSIONS.pop(thread_id, None)
    _AWAITING.discard(thread_id)
    _LAST_USED.pop(thread_id, None)
    _THREAD_LOCKS.pop(thread_id, None)
    if session is not None:
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


async def _evict_all() -> None:
    """Drop every session so future ones rebuild MCP with current credentials.
    Runs as the config gate's exclusive writer, so it drains in-flight turns
    first and never tears a session down mid-investigation."""
    async with _gate.exclusive():
        for thread_id in list(_SESSIONS):
            await _evict(thread_id)


@asynccontextmanager
async def _running_turn(thread_id: str):
    """Scope one agent turn: concurrent across threads (config-gate reader) but
    serialized per-thread, with liveness bookkeeping so eviction never drops a
    session that's mid-turn."""
    async with _gate.turn():
        async with _thread_lock(thread_id):
            _RUNNING.add(thread_id)
            try:
                yield
            finally:
                _RUNNING.discard(thread_id)
                _LAST_USED[thread_id] = time.monotonic()


def _github_status() -> dict:
    return {
        "connected": runtime.github_connected(),
        "repo": runtime.github_repo() or None,
        "mode": "live" if runtime.github_connected() else "offline",
    }


async def _drain_and_close() -> None:
    """Close every open session, releasing its MCP subprocesses."""
    for session in list(_SESSIONS.values()):
        try:
            await session.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — best-effort shutdown
            pass
    _SESSIONS.clear()


# How often the deduplication engine's digest buffer is drained. Well inside the
# shortest digest window the business rules use (1 hour; the longest is 4), so a
# group waits a fraction of its own window rather than most of another one.
_DEDUP_DIGEST_FLUSH_INTERVAL_S = 300.0


async def _dedup_digest_loop() -> None:
    """Drain every org's Layer 6 digest buffer on a timer.

    The same argument as the notification digest loop below, about a different
    buffer. `SuppressionEngine` collects a repeating non-critical alert into a
    digest group instead of dropping it, and that buffer is emptied by the next
    alert that arrives (POST /deduplication/process) or by an explicit POST
    /deduplication/digests/flush. A digest window closes with time, not with
    traffic, so on a deployment that goes quiet the last group of a burst sat
    there until the 24-hour expiry deleted it: alerts deliberately collected for a
    digest that nobody was ever sent. `flush_all_due_digests()` exists for exactly
    this caller — it skips orgs with nothing due and restores each pipeline's own
    tenant before delivering, which is what makes a flush with no request behind
    it safe.
    """
    from app.deduplication.api_router import flush_all_due_digests

    while True:
        await asyncio.sleep(_DEDUP_DIGEST_FLUSH_INTERVAL_S)
        try:
            await flush_all_due_digests()
        except Exception:  # noqa: BLE001 — a bad flush must not stop the timer
            log.exception("deduplication digest flush failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Multi-tenant: ensure the tenant store schema exists before serving.
    if get_settings().copilot_multi_tenant:
        await tenant_auth.get_store().setup()
        log.info("multi-tenant mode: tenant store ready (%s)", get_settings().copilot_tenant_db)
        if get_settings().copilot_tenant_db.startswith(("postgres://", "postgresql://")) is False:
            log.warning("multi-tenant on SQLite is app-level isolated, not RLS-hard-isolated; "
                        "use Postgres + RLS for production tenant isolation")
    # ORM schema (customers, trials, usage events, on-call, organizations, ...).
    # Had no caller at all before: init_db() existed and was never invoked, so on
    # the default SQLite backend every table in app/database/models.py was
    # unreachable and every DB-backed endpoint answered 500 against a missing
    # table. On Postgres this only verifies connectivity — Alembic owns the
    # schema there.
    #
    # `ensure_schema` rather than `init_db` directly: it is the once-per-engine,
    # locked wrapper, so the callers that touch the ORM outside a request (a
    # worker, a script, a background task) can run it too without racing.
    #
    # Failure is logged rather than fatal: the bulk of the app (chat,
    # investigations, incidents) does not touch this database, and refusing to
    # boot over it would be a worse outcome than today's.
    from app.database.engine import close_db, ensure_schema
    try:
        await ensure_schema()
    except Exception:
        log.exception("database init failed; DB-backed endpoints (on-call) will not work")
    # Drain queued audit events to the hash chain. `audit.record` is synchronous
    # (five of its call sites are in sync functions), so it queues and this task
    # writes. Started after ensure_schema so the first drain has a table.
    audit.start_drain()
    # OpenTelemetry tracing (no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set).
    from app import otel
    otel.init_tracing()
    # Background job worker: drains queued (webhook/SLO) investigations with retries
    # + dead-letter, so a transient failure or restart doesn't silently drop them.
    from app.jobqueue import make_job_queue, run_worker
    global _JOBQ
    _JOBQ = make_job_queue(get_settings().copilot_redis_url)
    job_task = asyncio.create_task(run_worker(_JOBQ, _handle_job))
    # Proactive SLO-burn poller (opt-in): auto-open investigations before a page.
    slo_task: asyncio.Task | None = None
    if get_settings().copilot_slo_poller:
        from app.mcp.servers.datadog import server as _dd
        from app.slo_poller import SLOPoller

        poller = SLOPoller(
            services_fn=_dd.list_services,
            burn_fn=_dd.compute_burn_rate,
            trigger_fn=_slo_trigger,
            interval=get_settings().copilot_slo_poll_interval_s,
            cooldown=get_settings().copilot_slo_cooldown_s,
        )
        slo_task = asyncio.create_task(poller.run())
        log.info("proactive SLO poller enabled")
    # On-call escalation sweeper. Without it `escalate_to_next_step` has no
    # caller at all, so every alert stops at step 0 and a policy's later levels
    # are unreachable however long nobody acknowledges.
    escalation_task: asyncio.Task | None = None
    try:
        from app.database.engine import _get_session_factory
        from app.oncall.escalation_service import EscalationSweeper

        escalation_task = asyncio.create_task(
            EscalationSweeper(
                _get_session_factory(),
                interval=get_settings().copilot_escalation_sweep_interval_s,
            ).run()
        )
    except Exception:
        log.exception("escalation sweeper failed to start; alerts will not escalate past step 0")

    # Notification digests. An org that chose "hourly" or "daily" batching has its
    # notifications written to a table instead of sent; without something to empty
    # that table the setting is a way to switch notifications off permanently.
    # In-process next to the sweeper rather than in `app/tasks/worker.py`, whose
    # scheduler is an optional separate process behind a message broker — a digest
    # that only arrives on deployments that happen to run that process is worse
    # than no digest, because the setting still says it is on.
    async def _digest_loop() -> None:
        from datetime import datetime, timezone

        from app.notifications.delivery import flush_due_digests

        while True:
            now = datetime.now(timezone.utc)
            # Wake on the hour: "hourly" and "daily" are both hour-granular, and
            # a loop that slept a fixed hour from startup would put an org's
            # daily digest at whatever minute the process last restarted.
            await asyncio.sleep(3600 - (now.minute * 60 + now.second))
            try:
                await flush_due_digests()
            except Exception:  # noqa: BLE001
                log.exception("notification digest flush failed")

    digest_task = asyncio.create_task(_digest_loop())

    # The deduplication engine's own digest buffer, which nothing else drains on a
    # timer — see `_dedup_digest_loop` above for why traffic is not enough.
    dedup_digest_task = asyncio.create_task(_dedup_digest_loop())

    # Live status page health check polling
    from app.status_page import start_live_status_polling, stop_live_status_polling
    await start_live_status_polling()
    log.info("live status page polling started")

    yield

    # Write whatever audit events are still queued before anything else shuts
    # down; they are the record of what this process did.
    await audit.stop_drain()
    # Stop live status polling first
    await stop_live_status_polling()
    for _task in (digest_task, dedup_digest_task, escalation_task, slo_task, job_task):
        if _task:
            _task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _task
    # Graceful shutdown: the config gate's exclusive writer waits for in-flight
    # turns to drain before tearing sessions down, so we don't kill MCP
    # subprocesses mid-investigation. Bounded so a stuck turn can't hang shutdown.
    try:
        async with asyncio.timeout(30):
            async with _gate.exclusive():
                await _drain_and_close()
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("shutdown drain timed out after 30s; forcing session cleanup")
        await _drain_and_close()
    # Last: return pooled connections so a rolling deploy doesn't leave Postgres
    # holding sessions for pods that are already gone.
    await close_db()


def _auth_header(conn: HTTPConnection) -> str:
    """Return the Authorization header, or its WebSocket equivalent.

    Browsers cannot set an Authorization header on a WebSocket handshake, so the
    token arrives as an `auth-<token>` entry in Sec-WebSocket-Protocol instead
    (kept out of the query string, which gets written to access logs). Normalising
    it to `Bearer <token>` lets both transports run the same checks.
    """
    header = conn.headers.get("authorization", "")
    if header or conn.scope.get("type") != "websocket":
        return header
    for proto in conn.headers.get("sec-websocket-protocol", "").split(","):
        proto = proto.strip()
        if proto.startswith("auth-"):
            return f"Bearer {proto[5:]}"
    return ""


def _reject(conn: HTTPConnection, detail: str) -> Exception:
    """Build the right rejection for the transport.

    An HTTPException raised during a WebSocket handshake has no response to be
    written to, so websockets get a policy-violation close instead.
    """
    if conn.scope.get("type") == "websocket":
        return WebSocketException(code=1008, reason=detail)
    return HTTPException(401, detail)


async def require_auth(conn: HTTPConnection) -> None:
    """Bearer-token gate for every route except probes and CORS preflight.

    When COPILOT_API_TOKEN is unset the API is open (local-dev default); set it
    in production and the frontend must send `Authorization: Bearer <token>`.
    (The static SPA mount is a sub-app, so it bypasses this dependency entirely.)

    Takes an HTTPConnection, not a Request, so it can also gate WebSocket routes:
    FastAPI cannot supply a Request to a websocket handler, and asking for one
    made every /ws/* handshake fail with a TypeError before the route was reached.
    """
    # WebSocket scopes carry no method, and CORS preflight never reaches a handler.
    if conn.scope.get("method") == "OPTIONS" or conn.url.path in _OPEN_PATHS:
        return
    # Auth routes need to be open for OAuth login/callback flows.
    if any(conn.url.path.startswith(p) for p in _AUTH_PATH_PREFIXES):
        return
    # Webhooks authenticate per-route, not with the shared bearer token, because
    # the sending platform cannot be told to present it in the form this gate
    # wants: PagerDuty/GitHub/GitLab sign the body (verified in the handler,
    # fail-closed), and the receivers that have no signature at all —
    # Alertmanager, Datadog, Grafana, /generic — go through
    # `app.webhooks.router._verify_shared_secret`, which accepts the shared
    # secret as X-Webhook-Secret, bearer or basic-auth password. Skipping here
    # moves the credential check, it does not remove it.
    #
    # All three prefixes, because there are three mounts of the same receivers:
    # `POST /webhooks/stripe` is declared on the app directly; app/webhooks/router.py
    # (Alertmanager, Grafana, Datadog, PagerDuty, the generic receiver) carries its
    # own `/webhooks` prefix and is included with `prefix="/api"`, so it really lives
    # at /api/webhooks/*; and app/api/v1/webhooks.py mirrors the GitHub, GitLab,
    # PagerDuty and Stripe receivers at /v1/webhooks/* by calling the very same
    # handlers. Only the first prefix was listed once, so on any deployment with
    # COPILOT_API_TOKEN set every alert delivery was rejected 401 before reaching
    # its signature check, and no alert ever became an incident. The /v1 mirror was
    # in the same state the moment its router was mounted: GitHub cannot be told to
    # send a bearer token, so a signed delivery to /v1/webhooks/github would have
    # been refused before the signature it *does* carry was ever looked at.
    if conn.url.path.startswith(("/webhooks/", "/api/webhooks/", "/v1/webhooks/")):
        return
    # The Slack bot router is mounted at /api/slack and is the same case: Slack
    # signs every request with X-Slack-Signature and never sends an Authorization
    # header, so demanding the shared bearer here makes the integration
    # unreachable by Slack itself. This is not a hole — the router carries a
    # router-wide `enforce_slack_signature` dependency that fails closed (503 when
    # SLACK_SIGNING_SECRET is unset, 401 on a bad or replayed HMAC), so the
    # credential check moves to the signature rather than disappearing.
    if conn.url.path.startswith("/api/slack/"):
        return
    # SCIM provisioning authenticates with its own IdP-issued bearer token, checked
    # in the handler (not the shared app token).
    if conn.url.path.startswith("/scim/"):
        return
    # Operator/back-office endpoints carry COPILOT_OPERATOR_TOKEN instead of a tenant
    # key, and _operator_guard checks it. Skipping here only skips the tenant-key
    # resolution below; the route's own dependency still fails closed.
    if conn.url.path in _OPERATOR_PATHS:
        return
    # Self-serve signup is how a new tenant GETS its first credential, and joining
    # a waitlist is something a visitor does before they have an account at all,
    # so neither can require one. They stay out of _OPEN_PATHS so the IP
    # rate-limiter still applies (abuse guard); /signup's handler additionally
    # enforces multi-tenant + the enabled flag.
    if conn.url.path in _PUBLIC_PATHS:
        return
    # The public status page (see _is_public_status_page). Only when the caller
    # presents no credential at all: the status page store is scoped to the org on
    # the tenant context, so returning early for a request that *did* carry a
    # tenant key would silently serve that tenant the default org's components
    # instead of their own. A credential that is present still has to be valid.
    if not _auth_header(conn) and _is_public_status_page(
        conn.scope.get("method", ""), conn.url.path
    ):
        return

    s = get_settings()
    # Multi-tenant: resolve the per-tenant API key (dcp_…) into the request's
    # TenantConfig + actor on the contextvar. Task-isolated, so it's scoped to
    # this request and read by runtime.py / the agent for the rest of the call.
    if s.copilot_multi_tenant:
        provided = _auth_header(conn)
        token = provided[7:].strip() if provided.startswith("Bearer ") else provided.strip()
        # A `dcp_…` value is a tenant API key; anything with two dots is a JWT.
        # Two kinds of JWT reach us: a Supabase/SSO one, and the session token our
        # own OAuth callback mints (app/auth/oauth.py, HS256 over COPILOT_SECRET_KEY).
        # Both must be accepted — otherwise a user who completed GitHub/Google login
        # holds a credential every route rejects. All resolve to a TenantConfig + actor.
        if token.startswith("dcp_"):
            resolved = await tenant_auth.resolve(token)
        elif token.count(".") == 2:
            resolved = await tenant_auth.resolve_jwt(token) or await tenant_auth.resolve_session(
                token
            )
        else:
            resolved = None
        if resolved is None:
            raise _reject(conn, "Invalid or revoked credential.")
        cfg, actor = resolved
        tenant_context.set_tenant(cfg)
        tenant_context.set_actor(actor)
        return

    expected = s.copilot_api_token
    if not expected:
        return  # auth disabled (dev)
    provided = _auth_header(conn)
    # Constant-time compare so the token can't be recovered via response timing.
    if hmac.compare_digest(provided, f"Bearer {expected}"):
        return
    # A single-tenant deployment can still have OAuth login switched on; the session
    # token it mints is the only credential such a user has, so accept it too rather
    # than leaving signed-in users locked out of every route.
    token = provided[7:].strip() if provided.startswith("Bearer ") else ""
    if token.count(".") == 2:
        from app.auth.oauth import verify_session_token

        if await verify_session_token(token) is not None:
            return
    raise _reject(conn, "Missing or invalid API token.")


def require_perm(action: str):
    """Route dependency factory: enforce the RBAC matrix on the resolved tenant.
    No-op in single-tenant mode (no roles), so the offline demo is unaffected."""

    async def _check() -> None:
        if not get_settings().copilot_multi_tenant:
            return
        cfg = tenant_context.get_tenant()
        role = cfg.role if cfg else "viewer"
        if not tn_models.can(role, action):
            raise HTTPException(403, f"Role '{role}' is not permitted to {action.replace('_', ' ')}.")

    return _check


def _scoped(thread_id: str) -> str:
    """Namespace a thread by tenant so one org can never resume/read another's
    investigation. No-op (returns thread_id) in single-tenant mode."""
    cfg = tenant_context.get_tenant()
    return f"{cfg.org_id}:{thread_id}" if cfg else thread_id


# Fleet-wide spend tracker (global token ceiling across all investigations).
_SPEND: Any = None


def _get_spend() -> Any:
    global _SPEND
    if _SPEND is None:
        from app.spend import make_spend_tracker
        s = get_settings()
        _SPEND = make_spend_tracker(s.copilot_redis_url, s.copilot_global_spend_window_s)
    return _SPEND


async def _record_spend(tokens: int) -> None:
    """Add a turn's tokens to the fleet-wide counter + warn past the alert threshold."""
    s = get_settings()
    if s.copilot_global_token_cap <= 0 or not tokens:
        return
    try:
        from app.spend import alert_due
        total = await _get_spend().record(tokens)
        if alert_due(total, s.copilot_global_token_cap, s.copilot_global_spend_alert):
            log.warning("fleet spend at %d/%d tokens (>= %.0f%% of cap)",
                        total, s.copilot_global_token_cap, s.copilot_global_spend_alert * 100)
    except Exception:  # noqa: BLE001 — spend accounting must never break a request
        log.warning("global spend record failed", exc_info=True)


async def _meter_and_spend(tokens: int, idem: str = "") -> None:
    """Record a completed investigation to both the per-tenant meter and the
    fleet-wide spend counter."""
    await metering.record_investigation(tokens, idem=idem)
    await _record_spend(tokens)
    # ...and to the billing meter the Billing page reads, which otherwise has no
    # writer at all outside the hand-callable POST /billing/track.
    from app.api.billing_router import record_ai_investigation
    await record_ai_investigation(tokens, idem)
    # The graph's nodes price every LLM call and park it on a queue (they run in
    # worker threads and cannot await a write). This is where a turn's dollar
    # cost reaches `llm_usage_records`; without it /usage and /stats report on
    # nothing but hand-POSTed rows. Best-effort inside, like the meters above.
    from app.model_router import usage_ledger
    await usage_ledger.flush()


def _record_slo_fields(
    report: dict | None, final_text: str, tokens_used: int, latency_s: float
) -> None:
    """Record a completed investigation to the SLO metrics (best-effort)."""
    try:
        from app.slo_metrics import metrics
        report = report or {}
        abstained = bool(report.get("abstained"))
        # "success" = produced a report and didn't error (abstention still counts as
        # a healthy, honest outcome).
        success = bool(report) or bool(final_text)
        metrics().record(latency_s, tokens_used, success, abstained)
    except Exception:  # noqa: BLE001 — metrics must never break a request
        log.debug("SLO metric record failed", exc_info=True)


def _record_slo(result: Any, latency_s: float) -> None:
    """Record a completed investigation from a non-streaming TurnResult."""
    _record_slo_fields(
        result.report, result.final_text, result.tokens_used, latency_s
    )


def _record_slo_failure(latency_s: float) -> None:
    """Record a turn that errored out.

    Without this only successful terminal states were ever sampled, so
    `copilot_investigations_failed_total` was pinned at zero and
    `copilot_investigations_total` omitted every errored turn — the exposed
    error rate read a permanent 0% while users were getting error responses
    back, and the CopilotHighFailureRate alert in
    deploy/observability/prometheus-alerts.yaml could not fire under any
    traffic. Nothing else can move that counter: `success` is
    `bool(report) or bool(final_text)`, and a completed turn always has one or
    the other (`_last_ai_text` falls back to a literal placeholder).

    Client-side rejections — an over-long message, a thread paused mid-approval,
    an exhausted quota — are deliberately not recorded: no investigation ran, so
    counting them would make the agent's own error rate track user mistakes.
    """
    _record_slo_fields(None, "", 0, latency_s)


#: Strong references to fire-and-forget tasks. asyncio only holds a weak one, so
#: a task nobody keeps can be garbage-collected mid-await and vanish without
#: running to completion.
_BG_TASKS: set[asyncio.Task] = set()


def _spawn(coro: Any) -> None:
    """Run `coro` detached from the request that started it."""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def _pause_paged(thread_id: str, approval_request: dict | None) -> None:
    """Tell the org that a turn is blocked waiting for a human to approve it.

    A paused investigation is the one state that nobody finds out about on their
    own: the agent stops mid-run and waits, and the only signal is a browser tab
    somebody has to already be looking at. The Slack trigger path posts its own
    approval blocks (`_post_to_slack`), but a turn started from the UI or the API
    posted nothing anywhere, so an agent could sit on a proposed write for hours.

    Fire-and-forget rather than awaited: the SSE stream and the JSON response
    both need to reach the caller now, and a slow webhook must not be what stands
    between an operator and the approval prompt. Failures are logged by
    `deliver`, which never raises.
    """
    req = approval_request or {}
    actions = req.get("actions") or []
    names = ", ".join(str(a.get("tool")) for a in actions if a.get("tool"))
    body = req.get("message") or "The agent is waiting for approval to continue."
    if names:
        body = f"{body}\nProposed: {names}"

    async def _send() -> None:
        from app.notifications.delivery import notify

        await notify(
            "ai_action_pending",
            "An investigation is waiting for approval",
            body,
            data={
                "thread_id": thread_id,
                "actions": actions,
                # A write is the case where waiting is the point, so it is worth
                # carrying through to whatever renders the notification.
                "write": any(a.get("write") for a in actions),
            },
            dedupe_key=f"approval:{thread_id}",
        )

    # A task rather than a BackgroundTasks entry: three of the five pause sites
    # are inside SSE generators, which have no BackgroundTasks to add to.
    _spawn(_send())


async def _record_history(req: Any, result: Any, latency_s: float) -> None:
    """Persist a finished investigation so it shows up on the history page.

    Stores the client's thread id, not the tenant-scoped internal key: the row
    already carries org_id, and the history page links back using the id the
    client knows.

    Writes the narrative columns of the `investigations` row (query, summary,
    report, feedback). `_record_dashboard_history` below upserts the analytics
    columns of the *same* row (outcome, duration, severity, service). They are
    two writers because they are two readers: GET /api/investigations and
    GET /api/dashboard/metrics.
    """
    report = result.report or {}
    await investigation_history.record(
        req.thread_id,
        req.message,
        status=result.status,
        summary=report.get("root_cause") or result.final_text or None,
        report=report or None,
        latency_ms=int(latency_s * 1000),
    )


# Org id used for investigation history when running single-tenant. Unlike
# _current_org_id() this never raises: history is recorded on every deployment,
# not just multi-tenant ones. Shared with the planning and coordinator stores,
# which file their rows under the same key.
_DEFAULT_HISTORY_ORG = tenant_context.DEFAULT_STORAGE_ORG


def _history_org_id() -> str:
    return tenant_context.storage_org_id()


async def _investigation_confidence(thread_id: str) -> tuple[str, str]:
    """The confidence tier an investigation actually reached, read from its row.

    The autonomy gate is documented as "gated on HIGH investigation confidence",
    but the only thing it ever saw was a `confidence` string lifted straight out
    of the remediation request body — so the entire safety check was typing the
    word "high". This reads the tier back off the stored report instead, which is
    the only copy of it nobody outside the graph can write.

    The lookup is scoped to the caller's org, which is also what stops one tenant
    quoting another's investigation to justify a write: an id that belongs to
    somebody else simply does not resolve.

    Returns `(tier, provenance)`. The tier is `autonomy.UNMEASURED` whenever we
    could not read one — no id, no such investigation, a report with no tier, or
    a store we could not reach. That is deliberately not reported as "low":
    nothing measured it, and the caller is told which of those it was.
    """
    from app.autonomy import CONFIDENCE_TIERS, UNMEASURED

    tid = (thread_id or "").strip()
    if not tid:
        return UNMEASURED, (
            "no thread_id was supplied, so there is no investigation to read a confidence tier from"
        )
    try:
        from sqlalchemy import select

        from app.database.engine import get_session
        from app.database.models import Investigation

        async with get_session() as session:
            row = (
                await session.execute(
                    select(Investigation).where(
                        Investigation.org_id == _history_org_id(),
                        Investigation.thread_id == tid,
                    )
                )
            ).scalars().first()
    except Exception:  # noqa: BLE001 — an unreadable store is unmeasured, not zero
        log.exception("could not read investigation %s while gating a remediation", tid)
        return UNMEASURED, (
            f"the investigation store could not be read, so the confidence of '{tid}' is unknown"
        )
    if row is None:
        return UNMEASURED, f"no investigation '{tid}' belongs to this organization"
    report = row.report or {}
    # The calibrated tier is the one the graph stands behind: `_calibrate_confidence`
    # and the adversarial review can both downgrade the model's self-reported
    # `confidence`, and acting on the un-calibrated value would undo that.
    tier = str(report.get("calibrated_confidence") or report.get("confidence") or "").strip().lower()
    if tier not in CONFIDENCE_TIERS:
        return UNMEASURED, (
            f"investigation '{tid}' finished without recording a confidence tier"
        )
    return tier, f"read from the stored report of investigation '{tid}'"


def _outcome_of(result: Any) -> Any:
    """Map a TurnResult onto a dashboard outcome.

    Note what is NOT derivable here: `RESOLVED` means the copilot fixed the
    incident, and nothing in the pipeline observes whether a proposed action
    actually resolved anything. Emitting it would turn every confident diagnosis
    into a claimed fix, so this only ever returns the four outcomes the result
    actually evidences.
    """
    from app.dashboard.metrics import InvestigationOutcome

    if getattr(result, "status", "") == "error":
        return InvestigationOutcome.FAILED
    report = getattr(result, "report", None) or {}
    if report.get("abstained"):
        return InvestigationOutcome.ABSTAINED
    if report.get("root_cause"):
        return InvestigationOutcome.ROOT_CAUSE_FOUND
    return InvestigationOutcome.PARTIAL_DIAGNOSIS


async def _record_dashboard_history(
    thread_id: str, result: Any, latency_s: float
) -> None:
    """Record a completed investigation to the dashboard history (best-effort).

    The analytics half of `_record_history`: same `investigations` row, different
    columns (outcome, duration, severity, service, hypothesis count), read by
    GET /api/dashboard/metrics and GET /api/dashboard/trends.

    Upserts one row per thread, so a multi-turn conversation stays one
    investigation. Everything written is read off the result: `time_saved_minutes`
    is deliberately left at its 0.0 default because nothing measures how long the
    incident would have taken a human, and the dashboard turns that field
    straight into a dollar figure.
    """
    try:
        from datetime import datetime, timedelta, timezone

        from app.dashboard.metrics import record_investigation

        report = getattr(result, "report", None) or {}
        services = report.get("affected_services") or []
        completed_at = datetime.now(timezone.utc)

        await record_investigation(
            org_id=_history_org_id(),
            investigation_id=thread_id,
            started_at=completed_at - timedelta(seconds=latency_s),
            completed_at=completed_at,
            outcome=_outcome_of(result),
            tokens_used=getattr(result, "tokens_used", 0) or 0,
            severity=str(report.get("severity") or "unknown"),
            service=str(services[0]) if services else "",
            hypotheses_generated=len(report.get("hypotheses") or []),
            # Left empty: the trace is human-readable prose per graph node, not a
            # structured tool list, and guessing tool names out of it would put
            # invented data on the dashboard.
            tools_used=None,
        )
    except Exception:  # noqa: BLE001 — analytics must never break a request
        log.debug("investigation history record failed", exc_info=True)


async def _record_dashboard_history_from_stream(
    thread_id: str, ev: dict, latency_s: float
) -> None:
    """`_record_dashboard_history` for the SSE paths, whose completion is a dict event.

    The streaming endpoints ran investigations that never reached the dashboard
    or the SLO metrics at all: only the non-streaming /chat had a recording hook,
    so anything the UI ran through /chat/stream was invisible in both.
    """
    await _record_dashboard_history(
        thread_id,
        TurnResult(
            status="completed",
            final_text=ev.get("final_text", "") or "",
            report=ev.get("report"),
            tokens_used=ev.get("tokens_used", 0) or 0,
        ),
        latency_s,
    )


async def quota_gate() -> None:
    """Route dependency: block a new investigation when the tenant is over its monthly
    plan quota (402), over its own DOLLAR budget (402), or when the FLEET-WIDE token
    cap is reached (429). No-op in single-tenant mode / when the caps are unset."""
    if await metering.over_quota():
        raise HTTPException(
            402, "Monthly investigation quota reached for your plan — upgrade to continue."
        )
    # The model-router budget an org set through /api/model-router/budget/set.
    # It had no callers outside its own HTTP handler, so `can_proceed: false`
    # blocked nothing and a cap could be exceeded indefinitely.
    from app.model_router.usage_store import over_budget
    reason = await over_budget()
    if reason:
        raise HTTPException(402, f"LLM spend budget reached — {reason}.")
    s = get_settings()
    if s.copilot_global_token_cap > 0:
        from app.spend import over_cap
        if over_cap(await _get_spend().total(), s.copilot_global_token_cap):
            raise HTTPException(
                429, "Fleet-wide token budget reached — investigations are paused. "
                "Try again after the budget window rolls over."
            )


# --------------------------------------------------------------------------- #
# OpenAPI Tags for endpoint grouping
# --------------------------------------------------------------------------- #
OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Health and readiness probes for orchestration platforms (Kubernetes, etc.)",
    },
    {
        "name": "Chat",
        "description": "**Core investigation endpoints.** Start AI-powered investigations, stream "
        "progress in real-time, and get structured RCA reports.",
    },
    {
        "name": "Approval",
        "description": "Human-in-the-loop approval workflow. Approve or reject risky actions "
        "proposed by the AI during investigations.",
    },
    {
        "name": "Config",
        "description": "Runtime configuration management. Connect GitHub, configure LLM providers, "
        "and manage data sources.",
    },
    {
        "name": "Metrics",
        "description": "Observability endpoints. Access real-time metrics, SLO data, and usage statistics.",
    },
    {
        "name": "Feedback",
        "description": "Investigation quality feedback. Submit ratings to improve the AI over time.",
    },
    {
        "name": "Audit",
        "description": "Compliance and audit trail. Query events and verify hash-chain integrity.",
    },
    {
        "name": "Admin",
        "description": "**Multi-tenant administration.** Manage organizations, API keys, members, "
        "and integrations. Requires appropriate RBAC permissions.",
    },
    {
        "name": "Webhooks",
        "description": "External service integrations. Receive events from PagerDuty, Slack, and Stripe.",
    },
    {
        "name": "Autonomy",
        "description": "Progressive autonomy endpoints. Execute and monitor autonomous remediations.",
    },
]

app = FastAPI(
    title="OmniPotent API",
    version="1.0.0",
    description="""
# OmniPotent API

AI-powered incident investigation and root cause analysis for DevOps teams.

## Overview

OmniPotent is an intelligent assistant that helps SRE and DevOps teams
investigate incidents faster. It integrates with your observability stack
(Datadog, PagerDuty, Kubernetes, GitHub) and uses LLMs to:

- **Investigate incidents** by querying logs, metrics, and traces
- **Identify root causes** with structured RCA reports
- **Propose fixes** with human-in-the-loop approval for risky actions
- **Create PRs** to remediate issues automatically

## Authentication

All endpoints (except health checks and webhooks) require authentication:

```
Authorization: Bearer <api_key>
```

In single-tenant mode, use `COPILOT_API_TOKEN`. In multi-tenant mode, use
organization API keys (`dcp_...`).

## Streaming

Chat endpoints support Server-Sent Events (SSE) for real-time progress:

- `POST /chat/stream` - Stream investigation progress
- `POST /approve/stream` - Stream approval continuation

## Rate Limits

- Default: 60 requests/minute per IP
- Configurable via `COPILOT_RATE_LIMIT_PER_MIN`

## SDKs

This OpenAPI spec can generate client SDKs. Use `/openapi.json` with your
preferred code generator (openapi-generator, swagger-codegen, etc.).
""",
    contact={
        "name": "OmniPotent Team",
        "url": "https://github.com/ankit25bcs10610/OmniPotent",
        "email": "support@omnipotent.dev",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
    dependencies=[Depends(require_auth)],
    responses={
        401: {"description": "Missing or invalid API token"},
        403: {"description": "Insufficient permissions for this action"},
        429: {"description": "Rate limit exceeded"},
    },
)


@app.get("/sentry-debug", include_in_schema=False)
async def sentry_debug() -> None:
    """Send a development-only exception and log smoke test to Sentry."""
    settings = get_settings()
    if settings.is_production or not settings.sentry_dsn:
        raise HTTPException(status_code=404, detail="Not found")

    import sentry_sdk

    sentry_sdk.logger.info("Sentry debug info log")
    sentry_sdk.logger.warning("Sentry debug warning log")
    sentry_sdk.logger.error("Sentry debug error log")

    log.info("Sentry debug application info log")
    log.warning("Sentry debug application warning log")
    log.error("Sentry debug application error log")

    # Deliberately uncaught: Sentry's FastAPI integration should capture it.
    1 / 0

# Body-size + rate-limit guard. Added FIRST so it ends up INSIDE the CORS
# middleware (add_middleware is LIFO) — that way a 413/429 still gets CORS
# headers and the browser can read it.
app.add_middleware(LimitsMiddleware)

# GZip compression for responses > 1KB (reduces bandwidth ~40%)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow the React dev server plus any origins configured via CORS_ORIGINS.
# The method list has to cover every verb the API actually exposes: with only
# GET/POST/OPTIONS the browser's preflight rejected every PUT, PATCH and DELETE,
# so editing or deleting anything from the UI failed before the request was
# sent. allow_credentials pairs with the explicit (never wildcard) origin list.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Added LAST, so it is the OUTERMOST middleware and stamps every response —
# including the 4xx that CORS and the limiter generate themselves, which the
# inner middleware never gets to touch.
#
# HSTS only in production. It is a browser-side pin: once a browser sees the
# header for a host it refuses plain HTTP to that host until max-age expires,
# and a year of that pointed at localhost breaks every developer who ever runs
# the app over http://localhost.
#
# /frontend-config hands the SPA a Supabase address to authenticate against, so
# the policy has to allow it. It did not: the browser blocked every call to that
# host, sign-in failed with "Failed to fetch", and a stored session could never
# be refreshed — which left the login form disabled behind a spinner with no
# message anywhere but the developer console.
app.add_middleware(
    SecurityHeadersMiddleware,
    enable_hsts=get_settings().copilot_hsts_enabled and get_settings().is_production,
    extra_connect_src=csp_connect_sources(get_settings().supabase_url),
)

# Include WebSocket router for real-time communication (alternative to SSE).
# WebSocket connections bypass the auth dependency (handled in the WS endpoint).
from app.api import websocket as ws_module  # noqa: E402
app.include_router(ws_module.router)

# Include billing router for subscription and payment management.
from app.api import billing_router  # noqa: E402
app.include_router(billing_router.router)

# Include on-call management router for schedules, alerts, and escalations.
from app.oncall import oncall_router  # noqa: E402
app.include_router(oncall_router)

# Include OAuth authentication router.
from app.auth.endpoints import router as auth_router  # noqa: E402
app.include_router(auth_router)

# Include teams/workspaces router for multi-user collaboration.
from app.teams.router import router as teams_router  # noqa: E402
app.include_router(teams_router, prefix="/api")

# Include incidents management router.
from app.incidents import incidents_router  # noqa: E402
app.include_router(incidents_router, prefix="/api")

# Include webhooks router for auto-triggering investigations.
from app.webhooks import webhooks_router  # noqa: E402
from app.webhooks import router as webhook_receivers  # noqa: E402  (result write-back)
app.include_router(webhooks_router, prefix="/api")

# Include Slack bot router for @mention support and slash commands.
from app.slack_bot.router import router as slack_bot_router  # noqa: E402
app.include_router(slack_bot_router, prefix="/api")

# Include runbooks router for runbook management and execution.
from app.runbooks.router import router as runbooks_router  # noqa: E402
app.include_router(runbooks_router, prefix="/api")

# Include knowledge base router for documentation and runbooks.
from app.knowledge_base.api_router import router as knowledge_router  # noqa: E402
app.include_router(knowledge_router, prefix="/api")

# Include event intelligence router for noise reduction and severity prediction.
from app.event_intelligence.api_router import router as event_intelligence_router  # noqa: E402
app.include_router(event_intelligence_router, prefix="/api")

# Include the six-layer deduplication pipeline. Layer depth follows the caller's
# plan flags (advanced_deduplication, cascade_detection, service_dependency_map).
from app.deduplication.api_router import router as deduplication_router  # noqa: E402
app.include_router(deduplication_router, prefix="/api")

# Include the service dependency graph: discovery, blast radius and rendering.
# Gated on service_dependency_map.
from app.dependency_graph.api_router import router as dependency_graph_router  # noqa: E402
app.include_router(dependency_graph_router, prefix="/api")

# Include cost optimization: budgets and alerts, ROI attribution, LLM token
# accounting and GPU fleet cost tracking. Distinct from the model router's cost
# optimizer below, which prices individual LLM calls.
from app.cost_optimizer.api_router import router as cost_optimizer_router  # noqa: E402
app.include_router(cost_optimizer_router, prefix="/api")

# Include model router for adaptive model routing and cost optimization.
from app.model_router.api_router import router as model_router_api  # noqa: E402
app.include_router(model_router_api, prefix="/api")

# Include knowledge graph router for Neo4j-backed infrastructure knowledge.
from app.knowledge_graph.api_router import router as knowledge_graph_router  # noqa: E402
from app.knowledge_graph.api_router import include_graphql_router  # noqa: E402
# GraphQL is attached to the router BEFORE the router is mounted on the app.
# `include_router` copies the routes it finds at the time it is called, so with
# these two lines the other way round the GraphQL sub-router was added to an
# object nothing was reading any more: /graphql/status happily reported
# "available" and handed callers a URL that 404'd, leaving the whole schema in
# app/knowledge_graph/graphql_schema.py unreachable.
include_graphql_router(knowledge_graph_router)  # Add GraphQL if strawberry is installed
app.include_router(knowledge_graph_router, prefix="/api")

# Include planning layer router for task graph management and execution.
from app.planning.api_router import router as planning_router  # noqa: E402
app.include_router(planning_router, prefix="/api")

# Include coordinator router for DAG-based task execution.
from app.coordinator.api_router import router as coordinator_router  # noqa: E402
app.include_router(coordinator_router, prefix="/api")

# Include verification agent router for claim verification and safety checks.
from app.verification.api_router import router as verification_router  # noqa: E402
app.include_router(verification_router, prefix="/api")

# Include SLO tracker router for SLO/SLI management and error budgets.
from app.slo.api_router import router as slo_router  # noqa: E402
app.include_router(slo_router)

# Include public status page router for component status, incidents, and maintenance.
from app.status_page.api_router import router as status_page_router  # noqa: E402
app.include_router(status_page_router)

# Include notification preferences router (channels + per-event routing rules).
from app.notifications.router import router as notifications_router  # noqa: E402
app.include_router(notifications_router, prefix="/api")

# Register global exception handlers for consistent error responses.
from app.middleware.error_handler import register_exception_handlers  # noqa: E402
register_exception_handlers(app)


# Supabase OAuth callback handler - serves the SPA for client-side token handling
@app.get("/auth/callback")
async def supabase_auth_callback():
    """Handle Supabase OAuth callback.

    Supabase redirects here with tokens in the URL hash fragment (#access_token=...).
    The hash is only visible client-side, so we serve the SPA which extracts the token.
    """
    from fastapi.responses import FileResponse

    index_path = ROOT / "frontend" / "dist" / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    # Fallback for dev mode without build
    return {"error": "Frontend not built. Run: cd frontend && npm run build"}


class ChatRequest(BaseModel):
    thread_id: str
    message: str


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    reason: str = ""


class GithubConnectRequest(BaseModel):
    token: str
    repo: str  # "owner/repo"


class ModelConfigRequest(BaseModel):
    provider: str  # anthropic | openai | gemini | groq | deepseek
    api_key: str = ""
    model: str = ""
    fast_model: str = ""


class SourcePathRequest(BaseModel):
    path: str


class FeedbackRequest(BaseModel):
    thread_id: str
    rating: str  # "up" | "down"
    comment: str = ""
    question: str = ""


class RemediateRequest(BaseModel):
    action: str                  # reversible only: rollback_deployment | restart_deployment
    target: str                  # deployment/service name
    # The investigation this remediation follows from. The confidence tier is read
    # off that investigation's stored report, server-side and org-scoped — see
    # `_investigation_confidence`. Without it there is no measured confidence and
    # the autonomy gate refuses.
    thread_id: str = ""
    reason: str = ""
    namespace: str = ""
    # Callers used to declare their own confidence here and the gate believed the
    # string: typing "high" was the entire safety check. The field is kept, and
    # rejected, so that a client still sending it gets told what changed instead
    # of having its declared tier silently ignored.
    confidence: str | None = None


# --- admin / tenant-management request bodies (multi-tenant) --------------- #
class CreateApiKeyRequest(BaseModel):
    name: str = ""
    role: str = "responder"


class AddMemberRequest(BaseModel):
    email: str
    role: str = "viewer"


class SetIntegrationRequest(BaseModel):
    name: str
    value: str


class SetPlanRequest(BaseModel):
    plan: str


class SignupRequest(BaseModel):
    org_name: str
    email: str


class ChatResponse(BaseModel):
    thread_id: str
    status: str  # "completed" | "awaiting_approval" | "error"
    answer: str = ""
    approval_request: dict | None = None
    trace: list[str] = []
    # Structured RCA deliverable (ranked hypotheses, evidence, severity,
    # postmortem), present once an investigation completes.
    report: dict | None = None
    # Total LLM tokens spent on this turn (cost visibility).
    tokens_used: int = 0


def _friendly_error(exc: Exception) -> str:
    """Turn a raw exception into a message safe to show in the UI.
    Never expose raw exception text to avoid leaking internal details."""
    text = str(exc).lower()
    if "rate_limit" in text or "429" in text or "too many requests" in text:
        return (
            "The LLM provider's rate limit was reached. Please try again later "
            "or check your plan/quota."
        )
    if "authentication" in text or "401" in text or "api_key" in text:
        return (
            "The LLM provider rejected the API key. Check the key for the "
            "configured COPILOT_PROVIDER in your .env."
        )
    if "timeout" in text or "timed out" in text:
        return "The request timed out. Please try again."
    if "connection" in text or "network" in text:
        return "A network error occurred. Please check your connection and try again."
    if "tool_use_failed" in text or "not in request.tools" in text:
        return (
            "The AI model tried to use a tool that isn't available. "
            "This can happen with Groq's free tier. Try using Anthropic or OpenAI instead."
        )
    if "413" in text or "payload too large" in text or "tokens per minute" in text:
        return (
            "Request too large for your LLM provider's limits. "
            "Consider upgrading your plan or switching to a provider with higher limits."
        )
    if "maximum number of" in text and "tools" in text:
        return (
            "Too many tools for your LLM provider. Groq has a 128 tool limit. "
            "Switch to Anthropic or OpenAI for full tool support."
        )
    # Log the actual error for debugging, but don't expose it to the client
    log.warning("Agent error (not exposed to client): %s", exc)
    return "Something went wrong while running the agent. Please try again."


def _check_message(message: str) -> None:
    """Reject empty and over-long chat messages (an empty one would burn a full
    agent turn; the length cap bounds prompt/token cost)."""
    if not message or not message.strip():
        raise HTTPException(400, "Message cannot be empty.")
    cap = get_settings().copilot_max_message_chars
    if len(message) > cap:
        raise HTTPException(413, f"Message too long (max {cap} characters).")


def _to_response(thread_id: str, result: TurnResult) -> ChatResponse:
    return ChatResponse(
        thread_id=thread_id,
        status=result.status,
        answer=result.final_text,
        approval_request=result.approval_request,
        trace=result.trace,
        report=result.report,
        tokens_used=result.tokens_used,
    )


def _evictable_thread() -> str | None:
    """Pick a session safe to drop: least-recently-used among those NOT running a
    turn and NOT paused awaiting approval. None if every session is busy."""
    idle = [t for t in _SESSIONS if t not in _RUNNING and t not in _AWAITING]
    if not idle:
        return None
    return min(idle, key=lambda t: _LAST_USED.get(t, 0.0))


async def _get_session(thread_id: str) -> CopilotSession:
    """Return the live session for a thread, building one if absent.

    A rebuilt session shares the file-backed checkpointer, so it transparently
    resumes a thread whose in-memory session was evicted — state survives. The
    in-memory pool is bounded; eviction is LRU and skips busy/awaiting sessions
    so a paused-for-approval thread is never stranded.
    """
    session = _SESSIONS.get(thread_id)
    if session is not None:
        return session
    # Re-check under the lock so two concurrent first-requests don't both build one.
    async with _SESSION_LOCK:
        session = _SESSIONS.get(thread_id)
        if session is None:
            cap = get_settings().copilot_max_sessions
            while cap and len(_SESSIONS) >= cap:
                victim = _evictable_thread()
                if victim is None:  # all sessions busy — allow a temporary over-cap
                    log.warning("session cap %d reached but all sessions are busy", cap)
                    break
                await _evict(victim)
            session = await CopilotSession(thread_id=thread_id).__aenter__()
            _SESSIONS[thread_id] = session
            _LAST_USED[thread_id] = time.monotonic()
        return session


@app.get(
    "/healthz",
    tags=["Health"],
    summary="Liveness probe",
    description="Check if the service is alive and responding. Returns the number of active "
    "investigation sessions. Used by Kubernetes liveness probes.",
    response_description="Service is alive with session count",
    responses={
        200: {
            "description": "Service is healthy",
            "content": {
                "application/json": {
                    "example": {"status": "ok", "active_sessions": 3}
                }
            },
        }
    },
)
async def healthz() -> dict:
    """Liveness probe endpoint.

    Returns:
        dict: Status object with 'ok' status and count of active sessions.

    Note:
        This endpoint bypasses authentication and rate limiting to ensure
        orchestration platforms can always check service health.
    """
    from app.api.websocket import get_connection_stats
    from app.mcp.health import get_health_monitor
    ws_stats = get_connection_stats()

    # Get MCP subprocess health summary
    mcp_health = await get_health_monitor().check_all()

    return {
        "status": "ok",
        "active_sessions": len(_SESSIONS),
        "websocket": ws_stats,
        "mcp": {
            "status": mcp_health.overall_status.value,
            # healthy + degraded + unhealthy == measured, always: the three
            # verdicts partition the rows something is observing right now.
            # Publishing them without `measured` made "no MCP session is open"
            # and "every server is broken and none is healthy" the same payload —
            # three zeros either way — so a reader had no way to tell an
            # unmeasured deployment from a measured verdict of zero.
            "measured": mcp_health.measured_count,
            "healthy": mcp_health.healthy_count,
            "degraded": mcp_health.degraded_count,
            "unhealthy": mcp_health.unhealthy_count,
            # Servers this process has run whose sessions have all closed. Real
            # history, no current observation, so they carry no verdict and are
            # counted apart from the three above rather than folded into them.
            "unmeasured": len(mcp_health.unmeasured),
            # What this build ships, from the same catalog /config serves. It
            # needs no subprocess and no session, so it is the one MCP number
            # that is always available: `configured: 0` says this deployment has
            # no MCP servers at all, which is a different fact from `measured: 0`
            # on a deployment that has them and is merely idle. Marketplace
            # agents add to the running set at session time; reading that here
            # would mean a file read and a module resolve inside a liveness
            # probe, so this counts the built-ins only.
            "configured": len(MCP_CATALOG),
        },
    }


@app.get(
    "/readyz",
    tags=["Health"],
    summary="Readiness probe",
    description="Check if the service is ready to serve investigations. In production, "
    "this requires an LLM API key to be configured. Returns 503 if not ready.",
    response_description="Readiness status with any blocking reasons",
    responses={
        200: {
            "description": "Service is ready",
            "content": {
                "application/json": {
                    "example": {"status": "ready", "reasons": []}
                }
            },
        },
        503: {
            "description": "Service is not ready",
            "content": {
                "application/json": {
                    "example": {
                        "status": "not_ready",
                        "reasons": ["no LLM API key configured for the active provider"]
                    }
                }
            },
        },
    },
)
async def readyz():
    """Readiness probe endpoint for Kubernetes.

    Checks whether the service can actually serve investigations. In production
    mode (not local dev), requires an LLM API key to be configured.

    Returns:
        JSONResponse: Status object with 'ready'/'not_ready' and list of blocking reasons.

    Note:
        In multi-tenant mode, each tenant supplies their own LLM key, so a global
        key is not required for readiness.
    """
    s = get_settings()
    reasons: list[str] = []
    # In multi-tenant mode each tenant supplies its own LLM key, so a global key
    # isn't required for readiness.
    if s.is_production and not s.copilot_multi_tenant and not active_api_key():
        reasons.append("no LLM API key configured for the active provider")
    ready = not reasons
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "reasons": reasons},
        status_code=200 if ready else 503,
    )


@app.get(
    "/me",
    tags=["Config"],
    summary="Get current identity",
    description="Returns the current user's identity, organization, and role. In single-tenant "
    "mode, returns the operator identity. In multi-tenant mode, resolves from the API key.",
    response_description="Current user identity and permissions",
    responses={
        200: {
            "description": "User identity",
            "content": {
                "application/json": {
                    "examples": {
                        "single_tenant": {
                            "summary": "Single-tenant mode",
                            "value": {
                                "multi_tenant": False,
                                "authenticated": True,
                                "org": None,
                                "role": "owner",
                                "label": "Operator"
                            }
                        },
                        "multi_tenant": {
                            "summary": "Multi-tenant authenticated",
                            "value": {
                                "multi_tenant": True,
                                "authenticated": True,
                                "org": "acme-corp",
                                "role": "admin",
                                "plan": "team",
                                "label": "acme-corp"
                            }
                        }
                    }
                }
            },
        }
    },
)
async def me() -> dict:
    """Get the current authenticated identity.

    Returns the user's organization, role, and plan based on their API key.
    Used by the UI to determine what features and actions are available.

    Returns:
        dict: Identity object with multi_tenant flag, auth status, org, role, and label.
    """
    s = get_settings()
    if not s.copilot_multi_tenant:
        return {"multi_tenant": False, "authenticated": True, "org": None,
                "role": "owner", "label": "Operator"}
    cfg = tenant_context.get_tenant()
    if cfg is None:
        return {"multi_tenant": True, "authenticated": False, "org": None,
                "role": None, "label": "Guest"}
    return {"multi_tenant": True, "authenticated": True, "org": cfg.org_id,
            "role": cfg.role, "plan": cfg.plan, "label": cfg.org_id[:16]}


@app.get(
    "/config",
    tags=["Config"],
    summary="Get agent configuration",
    description="Returns the current agent configuration including LLM provider, models, "
    "available MCP servers, GitHub connection status, and configured data sources.",
    response_description="Current agent configuration",
    responses={
        200: {
            "description": "Agent configuration",
            "content": {
                "application/json": {
                    "example": {
                        "provider": "anthropic",
                        # Sourced from the catalog: the literals here had drifted to
                        # a model ID that does not exist.
                        "model": model_catalog.default_model("anthropic"),
                        "fast_model": model_catalog.default_model("anthropic", fast=True),
                        "offline_mode": False,
                        "servers": [
                            {"name": "datadog", "label": "Datadog", "tools": ["search_logs"]}
                        ],
                        "github": {"connected": True, "repo": "acme/app", "mode": "live"},
                        "sources": {"repo_path": "/app/repo", "logs_path": "/app/logs"},
                        "has_key": True
                    }
                }
            },
        }
    },
)
async def config() -> dict:
    """Get the running agent's configuration.

    Returns details about the LLM provider, available MCP tool servers,
    GitHub connection status, and configured data source paths.

    Returns:
        dict: Configuration object with provider, models, servers, and sources.
    """
    main_model, fast_model = resolved_models()
    return {
        "provider": runtime.provider(),
        "model": main_model,
        "fast_model": fast_model,
        "offline_mode": not runtime.github_connected(),
        "servers": MCP_CATALOG,
        "github": _github_status(),
        "sources": {
            "repo_path": str(runtime.repo_path()),
            "logs_path": str(runtime.logs_path()),
        },
        "has_key": bool(active_api_key()),
    }


@app.get(
    "/frontend-config",
    tags=["Config"],
    summary="Get frontend configuration",
    description="Returns configuration needed by the frontend SPA, including Supabase auth settings. "
    "This allows runtime injection of environment variables that weren't available at Docker build time.",
    response_description="Frontend configuration for runtime injection",
    responses={
        200: {
            "description": "Frontend config",
            "content": {
                "application/json": {
                    "example": {
                        "supabase_url": "https://xxx.supabase.co",
                        "supabase_anon_key": "eyJ...",
                        "api_url": "",
                    }
                }
            },
        }
    },
)
async def frontend_config() -> dict:
    """Get frontend configuration for runtime injection.

    Returns Supabase and other settings that the frontend needs but weren't
    available during the Docker build (since Render doesn't pass env vars
    to Docker build args).

    Returns:
        dict: Configuration for the frontend SPA.
    """
    s = get_settings()
    return {
        "supabase_url": s.supabase_url or "",
        "supabase_anon_key": s.supabase_anon_key or "",
        "api_url": "",  # Empty = same-origin
    }


@app.get(
    "/api/models",
    tags=["Config"],
    summary="List selectable LLM models",
    description="Returns the model catalog: every model the UI may offer, with its "
    "provider, context window, capabilities and published price. Prices are USD per "
    "1M tokens and are `null` when the vendor rate has not been verified — show those "
    "as unknown rather than substituting a guess.",
    response_description="Model catalog with per-provider defaults",
    responses={
        200: {
            "description": "Model catalog",
            "content": {
                "application/json": {
                    "example": {
                        "pricing_last_verified": model_catalog.PRICING_LAST_VERIFIED,
                        "pricing_unit": "usd_per_1m_tokens",
                        "defaults": model_catalog.DEFAULT_MODELS,
                        "models": [
                            model_catalog.to_dict(
                                model_catalog.get(
                                    model_catalog.default_model("anthropic", fast=True)
                                )
                            )
                        ],
                    }
                }
            },
        }
    },
)
async def models_catalog() -> dict:
    """List the LLM models a caller may select.

    Single source of truth for the frontend model picker, so the UI can no longer
    offer IDs the backend would reject. Retired models are omitted; they stay in
    the catalog only so historical usage still prices correctly.

    Returns:
        dict: Catalog payload with pricing metadata, per-provider defaults, and
        one entry per selectable model.
    """
    return model_catalog.catalog_payload()


@app.get(
    "/github/status",
    tags=["Config"],
    summary="Get GitHub connection status",
    description="Returns the current GitHub connection status including connected repository "
    "and mode (live or offline).",
    response_description="GitHub connection status",
    responses={
        200: {
            "description": "GitHub status",
            "content": {
                "application/json": {
                    "example": {
                        "connected": True,
                        "repo": "acme/api-server",
                        "mode": "live"
                    }
                }
            },
        }
    },
)
async def github_status() -> dict:
    """Get GitHub connection status.

    Returns:
        dict: Status object with connected flag, repo name, and mode.
    """
    return _github_status()


def _normalize_repo(raw: str) -> str:
    """Accept a full GitHub URL or `owner/repo` and return `owner/repo`.
    Strips https/ssh prefixes, a trailing .git, and any extra path segments —
    so pasting 'https://github.com/acme/app' works, not just 'acme/app'."""
    r = (raw or "").strip()
    r = r.removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/", "git@github.com:"):
        if r.startswith(prefix):
            r = r[len(prefix):]
            break
    parts = [p for p in r.strip("/").split("/") if p]
    return "/".join(parts[:2])  # owner/repo


@app.post(
    "/github/connect",
    tags=["Config"],
    summary="Connect GitHub repository",
    description="Validate a GitHub personal access token and connect to a repository. "
    "Enables live GitHub operations (commits, PRs, workflow status) for investigations.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Successfully connected to GitHub"},
        400: {"description": "Invalid token or repository format"},
        401: {"description": "GitHub rejected the token"},
        404: {"description": "Repository not found or token lacks access"},
        502: {"description": "Could not reach GitHub API"},
    },
)
async def github_connect(req: GithubConnectRequest) -> dict:
    """Connect to a GitHub repository.

    Validates the provided token against GitHub's API and stores the credentials
    in memory for the duration of the session. All active investigation sessions
    are rebuilt to pick up the new credentials.

    Args:
        req: GitHub token and repository (owner/repo or full URL).

    Returns:
        dict: GitHub status with connected repo details.

    Raises:
        HTTPException: If validation fails or GitHub is unreachable.
    """
    token = req.token.strip()
    repo = _normalize_repo(req.repo)  # accept a URL or owner/repo
    if not token or repo.count("/") != 1:
        raise HTTPException(400, "Provide a token and a repo as 'owner/repo' (or a GitHub URL).")

    # Verify the credentials actually work before storing them.
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{repo}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach GitHub: {exc}") from exc

    if resp.status_code == 401:
        raise HTTPException(401, "GitHub rejected the token (check it's valid).")
    if resp.status_code == 404:
        raise HTTPException(404, f"Repo '{repo}' not found or the token lacks access.")
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, f"GitHub error: {resp.text[:200]}")

    runtime.set_github(token, repo)
    await _evict_all()  # rebuild sessions so the MCP server picks up live creds
    data = resp.json()
    status = _github_status()
    status["full_name"] = data.get("full_name", repo)
    status["private"] = data.get("private")
    return status


@app.post(
    "/github/disconnect",
    tags=["Config"],
    summary="Disconnect GitHub",
    description="Disconnect from GitHub and revert to offline demo mode. Clears stored "
    "credentials and rebuilds all active sessions.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Successfully disconnected from GitHub"},
    },
)
async def github_disconnect() -> dict:
    """Disconnect from GitHub.

    Clears GitHub credentials and reverts to offline mode. All active
    investigation sessions are rebuilt.

    Returns:
        dict: Updated GitHub status (offline mode).
    """
    runtime.clear_github()
    await _evict_all()
    return _github_status()


@app.post(
    "/model/configure",
    tags=["Config"],
    summary="Configure LLM provider",
    description="Switch the LLM provider and model at runtime. Supports Anthropic, OpenAI, "
    "Google Gemini, Groq, and DeepSeek. Changes take effect immediately for new investigations.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {
            "description": "Model configured successfully",
            "content": {
                "application/json": {
                    "example": {
                        "provider": "anthropic",
                        "model": model_catalog.default_model("anthropic"),
                        "fast_model": model_catalog.default_model("anthropic", fast=True),
                    }
                }
            },
        },
        400: {"description": "Invalid provider, unknown model ID, or missing API key"},
    },
)
async def model_configure(req: ModelConfigRequest) -> dict:
    """Configure the LLM provider and model.

    Switch providers at runtime without modifying environment variables.
    All active sessions are rebuilt to use the new model.

    Args:
        req: Provider name, API key, and optional model overrides.

    Returns:
        dict: New provider and model configuration.

    Raises:
        HTTPException: If the provider is invalid or API key is missing.
    """
    provider = req.provider.strip().lower()
    if provider not in {"anthropic", "openai", "gemini", "groq", "deepseek"}:
        raise HTTPException(
            400, "provider must be one of: anthropic, openai, gemini, groq, deepseek"
        )

    # runtime.set_model() stores whatever string it is handed, so an unknown ID
    # only surfaced later as a provider 404 mid-investigation. Reject it here,
    # where the caller can still see which IDs are valid. Empty means "use the
    # provider default", which is legitimate.
    #
    # Checked against the catalog *entry*, not just is_known(): that is
    # provider-agnostic, so it accepted "gpt-4o" for provider=anthropic (which
    # then 404s on the first call with the key the user just pasted) and accepted
    # retired IDs the provider no longer serves. Dated snapshots
    # ("claude-sonnet-4-20250514") still resolve to their base entry and pass,
    # which is why this resolves rather than requiring an exact match.
    for field_name, value in (("model", req.model), ("fast_model", req.fast_model)):
        candidate = (value or "").strip()
        if not candidate:
            continue
        info = model_catalog.resolve(candidate)
        if info is None:
            reason = "unknown"
        elif info.provider != provider:
            reason = f"a {info.provider} model, not {provider}"
        elif info.status == "retired":
            reason = "retired and no longer served"
        else:
            continue
        raise HTTPException(
            400,
            f"{field_name} {candidate!r} is {reason}; valid IDs for {provider}: "
            + ", ".join(model_catalog.selectable_ids(provider)),
        )

    snapshot = runtime.model_snapshot()
    runtime.set_model(provider, req.api_key, req.model, req.fast_model)
    if not active_api_key():
        # Roll back to the PRIOR working config (not .env), keeping other overrides.
        runtime.restore_model(snapshot)
        raise HTTPException(400, f"An API key is required for '{provider}'.")

    await _evict_all()  # rebuild sessions so the new model/key is used
    audit.record("config.model_changed", provider=runtime.provider())
    main_model, fast_model = resolved_models()
    return {"provider": runtime.provider(), "model": main_model, "fast_model": fast_model}


def _set_source(path: str, kind: str, setter) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    # Confine to the allowlisted root so a caller can't aim the repo/logs MCP
    # servers (and therefore the agent's file tools) at arbitrary host paths.
    root = get_settings().sources_root
    if p != root and root not in p.parents:
        raise HTTPException(403, f"path must be inside the allowed sources root ({root})")
    if not p.exists():
        raise HTTPException(404, f"path does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"path is not a directory: {p}")
    setter(path.strip())
    return str(p)


@app.post(
    "/sources/repo",
    tags=["Config"],
    summary="Set repository source path",
    description="Configure the local repository path for the repo MCP server. "
    "Path must be within the allowed sources root.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Repository path configured"},
        403: {"description": "Path outside allowed sources root"},
        404: {"description": "Path does not exist"},
    },
)
async def set_repo_source(req: SourcePathRequest) -> dict:
    """Set the repository source path.

    Args:
        req: Path to the local repository directory.

    Returns:
        dict: Resolved repository path.
    """
    resolved = _set_source(req.path, "repo", runtime.set_repo_path)
    await _evict_all()
    return {"repo_path": resolved}


@app.post(
    "/sources/logs",
    tags=["Config"],
    summary="Set logs source path",
    description="Configure the local logs/metrics path for the logs MCP server. "
    "Path must be within the allowed sources root.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Logs path configured"},
        403: {"description": "Path outside allowed sources root"},
        404: {"description": "Path does not exist"},
    },
)
async def set_logs_source(req: SourcePathRequest) -> dict:
    """Set the logs source path.

    Args:
        req: Path to the local logs/metrics directory.

    Returns:
        dict: Resolved logs path and any missing expected files.
    """
    resolved = _set_source(req.path, "logs", runtime.set_logs_path)
    # Soft warning if the expected demo files aren't present.
    p = Path(resolved)
    missing = [f for f in ("app.log", "metrics.json") if not (p / f).exists()]
    await _evict_all()
    return {"logs_path": resolved, "missing_files": missing}


@app.post(
    "/reset",
    tags=["Config"],
    summary="Reset configuration",
    description="Revert all runtime configuration overrides back to the .env defaults. "
    "This includes LLM provider, GitHub connection, and source paths.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Configuration reset to defaults"},
    },
)
async def reset_config() -> dict:
    """Reset all runtime configuration to .env defaults.

    Clears all runtime overrides and rebuilds all active sessions.

    Returns:
        dict: New configuration state after reset.
    """
    runtime.reset()
    await _evict_all()
    main_model, fast_model = resolved_models()
    return {
        "provider": runtime.provider(),
        "model": main_model,
        "fast_model": fast_model,
        "github": _github_status(),
        "sources": {
            "repo_path": str(runtime.repo_path()),
            "logs_path": str(runtime.logs_path()),
        },
    }


@app.post(
    "/chat",
    tags=["Chat"],
    summary="Start or continue an investigation",
    description="""
Start a new AI-powered investigation or continue an existing one.

The agent will:
1. Analyze your question/alert description
2. Query relevant data sources (logs, metrics, traces)
3. Form and test hypotheses about root causes
4. Return findings with a structured RCA report

**Thread Management:**
- Use the same `thread_id` to continue a conversation
- Each thread maintains investigation context and history
- Threads can be paused for approval and resumed

**Response Status:**
- `completed`: Investigation finished, check `report` for structured findings
- `awaiting_approval`: Agent needs approval for risky action, use POST /approve
- `error`: Something went wrong, check `answer` for details
""",
    response_model=ChatResponse,
    dependencies=[Depends(require_perm("run_investigation")), Depends(quota_gate)],
    responses={
        200: {
            "description": "Investigation turn completed",
            "content": {
                "application/json": {
                    "example": {
                        "thread_id": "inv-abc123",
                        "status": "completed",
                        "answer": "The root cause was a memory leak...",
                        "trace": ["Querying logs...", "Found 523 errors"],
                        "report": {"summary": "Memory leak in connection pool", "severity": "SEV2"},
                        "tokens_used": 4521
                    }
                }
            },
        },
        400: {"description": "Empty message"},
        402: {"description": "Monthly quota exceeded"},
        409: {"description": "Thread is paused awaiting approval"},
        413: {"description": "Message too long"},
        429: {"description": "Rate limit or token budget exceeded"},
    },
)
async def chat(req: ChatRequest) -> ChatResponse:
    """Start or continue an investigation.

    Send a message to the AI agent to investigate an incident. The agent
    will query data sources, analyze findings, and return structured results.

    Args:
        req: Thread ID and message to investigate.

    Returns:
        ChatResponse: Investigation results with status, answer, and optional report.

    Raises:
        HTTPException: On validation errors or if thread is in approval state.
    """
    _check_message(req.message)
    tid = _scoped(req.thread_id)  # tenant-namespaced internal key (client sees its own id)
    existed = tid in _SESSIONS
    # Started before the try so the failure path below has a latency to report:
    # a turn that dies inside _get_session never reaches the inner assignment.
    _t0 = time.monotonic()
    session = None
    try:
        async with _running_turn(tid):
            session = await _get_session(tid)
            # Don't run a fresh message on a thread paused mid-approval: it would
            # leave the prior tool_calls unanswered and corrupt the history.
            if await session.pending_interrupt():
                _AWAITING.add(tid)
                raise HTTPException(
                    409,
                    "This thread is paused awaiting approval — approve or reject "
                    "the pending action (POST /approve) before sending a new message.",
                )
            _AWAITING.discard(tid)
            result = await session.ask(req.message)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
                _pause_paged(req.thread_id, result.approval_request)
            elif result.status == "completed":
                await _meter_and_spend(result.tokens_used)
                _record_slo(result, time.monotonic() - _t0)
                await _record_history(req, result, time.monotonic() - _t0)
                # Same `investigations` row, analytics columns. Keyed on the
                # client's thread id (not the tenant-scoped `tid`) so it upserts
                # onto the row `_record_history` just wrote — the row already
                # carries org_id, so the scoped key would only split it in two.
                await _record_dashboard_history(
                    req.thread_id, result, time.monotonic() - _t0
                )
    except HTTPException:
        raise  # 409/4xx should surface as real HTTP errors, not a 200 error body
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
        log.exception("chat turn failed (thread=%s)", tid)
        _record_slo_failure(time.monotonic() - _t0)
        # A failed agent turn may leave an assistant tool call without a matching
        # ToolMessage in the checkpoint. Reset the failed thread so the next
        # request cannot reuse invalid provider history.
        if session is not None:
            try:
                await session.reset_after_failure()
            except Exception:  # noqa: BLE001 — cleanup must not mask the error
                log.warning("could not reset failed thread=%s", tid, exc_info=True)
        if existed or session is not None:
            await _evict(tid)
        return ChatResponse(
            thread_id=req.thread_id, status="error", answer=_friendly_error(exc)
        )
    return _to_response(req.thread_id, result)


@app.post(
    "/approve",
    tags=["Approval"],
    summary="Approve or reject a pending action",
    description="""
Approve or reject a risky action proposed by the AI agent.

When an investigation reaches a point where it wants to perform a write operation
(e.g., rollback a deployment, create a PR), it pauses and requests human approval.
Use this endpoint to continue the investigation.

**Approval Flow:**
1. Agent proposes an action (response has `status: "awaiting_approval"`)
2. Review the `approval_request` details (tool, args, risk level)
3. Call this endpoint with `approved: true` or `approved: false`
4. Agent continues or takes an alternative path

**Audit Trail:**
All approval decisions are logged in the audit trail with the actor identity.
""",
    response_model=ChatResponse,
    dependencies=[Depends(require_perm("approve_action"))],
    responses={
        200: {
            "description": "Approval processed, investigation continues",
            "content": {
                "application/json": {
                    "example": {
                        "thread_id": "inv-abc123",
                        "status": "completed",
                        "answer": "Rollback completed successfully...",
                        "trace": ["Executing rollback...", "Verifying service health"],
                        "tokens_used": 1234
                    }
                }
            },
        },
        404: {"description": "No pending approval for this thread"},
    },
)
async def approve(req: ApproveRequest) -> ChatResponse:
    """Approve or reject a pending action.

    Continue an investigation that is paused awaiting approval. The agent
    will either execute the approved action or find an alternative approach.

    Args:
        req: Thread ID, approval decision, and optional reason.

    Returns:
        ChatResponse: Continuation results after approval decision.

    Raises:
        HTTPException: If no pending approval exists for the thread.
    """
    tid = _scoped(req.thread_id)
    async with _running_turn(tid):
        existed = tid in _SESSIONS
        # Rebuild from the checkpointer if the session was evicted — state survives.
        session = await _get_session(tid)
        if not await session.pending_interrupt():
            if not existed:
                await _evict(tid)  # don't leak a session built just to check
            raise HTTPException(
                404, f"no investigation awaiting approval for thread '{req.thread_id}'"
            )
        audit.record("approval.decided", thread=req.thread_id, approved=req.approved)
        # An investigation that pauses for approval is never sampled at the pause,
        # so without recording here the whole thing goes uncounted — every
        # investigation that touched a risky action was missing from /metrics/slo.
        # The latency measured is the continuation only, deliberately: the human's
        # thinking time is not the agent's.
        _t0 = time.monotonic()
        try:
            result = await session.resume(approved=req.approved, reason=req.reason)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
                # A resumed turn can stop at a *second* gate; without this the
                # first approval is announced and every one after it is silent.
                _pause_paged(req.thread_id, result.approval_request)
            else:
                _AWAITING.discard(tid)
                if result.status == "completed":
                    await _meter_and_spend(result.tokens_used)
                    _record_slo(result, time.monotonic() - _t0)
                    # Upserts onto the row the original /chat turn created, so the
                    # investigation keeps its true start time and counts once.
                    await _record_dashboard_history(
                        req.thread_id, result, time.monotonic() - _t0
                    )
        except Exception as exc:  # noqa: BLE001
            log.exception("approve/resume failed (thread=%s)", tid)
            _record_slo_failure(time.monotonic() - _t0)
            return ChatResponse(
                thread_id=req.thread_id, status="error", answer=_friendly_error(exc)
            )
    return _to_response(req.thread_id, result)


# --------------------------------------------------------------------------- #
# Streaming (SSE) — live trace as the agent investigates
# --------------------------------------------------------------------------- #
def _stream_payload(thread_id: str, ev: dict) -> dict:
    """Map an internal session event to the SSE wire shape the frontend expects."""
    t = ev["type"]
    out: dict = {"type": t, "thread_id": thread_id}
    if t == "trace":
        out["line"] = ev["line"]
    elif t == "approval":
        out.update(
            status="awaiting_approval",
            approval_request=ev["approval_request"],
            trace=ev["trace"],
        )
    elif t == "done":
        out.update(
            status="completed",
            answer=ev["final_text"],
            trace=ev["trace"],
            report=ev.get("report"),
            tokens_used=ev.get("tokens_used", 0),
        )
    return out


def _transport_dead(exc: BaseException, _depth: int = 0) -> bool:
    """True if `exc` (or anything it wraps) is a dead MCP stdio transport.

    A session whose MCP subprocess connection has been closed raises
    ``anyio.ClosedResourceError`` / ``BrokenResourceError`` from inside the
    tools node, usually re-raised through an ExceptionGroup ("During task with
    name 'tools'"). The request never reached the MCP server, so nothing ran and
    the turn is safe to restart on a rebuilt session.
    """
    if _depth > 10:  # cheap cycle guard: __context__ chains can loop
        return False
    if isinstance(exc, (anyio.ClosedResourceError, anyio.BrokenResourceError)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_transport_dead(inner, _depth + 1) for inner in exc.exceptions)
    return any(
        inner is not None and _transport_dead(inner, _depth + 1)
        for inner in (exc.__cause__, exc.__context__)
    )


async def _stream_chat_turn(tid: str, req: "ChatRequest", session: Any):
    """Run one chat turn on `session`, yielding SSE payloads and doing the
    end-of-turn bookkeeping (spend metering, SLO sample, history row)."""
    _t0 = time.monotonic()
    async for ev in session.ask_stream(req.message):
        t = ev.get("type")
        if t == "approval":
            _AWAITING.add(tid)
            _pause_paged(req.thread_id, ev.get("approval_request"))
        elif t == "done":
            _AWAITING.discard(tid)
            await _meter_and_spend(ev.get("tokens_used", 0))
            # This is the path the UI actually uses. It recorded neither an SLO
            # sample nor a history row, which is why the SLO dashboard read as
            # zero investigations and the history page had nothing to list.
            elapsed = time.monotonic() - _t0
            _record_slo_fields(
                ev.get("report"),
                ev.get("final_text", ""),
                ev.get("tokens_used", 0),
                elapsed,
            )
            await investigation_history.record(
                req.thread_id,
                req.message,
                status="completed",
                summary=(ev.get("report") or {}).get("root_cause")
                or ev.get("final_text")
                or None,
                report=ev.get("report"),
                latency_ms=int(elapsed * 1000),
            )
            # ...and the analytics columns of the same row, which is what the
            # dashboard aggregates over. Same key as above (the client's thread
            # id) so both writers upsert one row per investigation.
            await _record_dashboard_history_from_stream(req.thread_id, ev, elapsed)
        yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}


@app.post(
    "/chat/stream",
    tags=["Chat"],
    summary="Stream an investigation (SSE)",
    description="""
Start an investigation with real-time streaming progress via Server-Sent Events.

**Event Types:**
- `trace`: Investigation step completed (contains `line` with step description)
- `approval`: Agent needs approval (contains `approval_request` details)
- `done`: Investigation complete (contains `answer`, `report`, `tokens_used`)
- `error`: Something went wrong (contains error message in `answer`)

**Example SSE Stream:**
```
data: {"type": "trace", "line": "Querying Datadog for error logs..."}

data: {"type": "trace", "line": "Found 523 errors in the last hour"}

data: {"type": "done", "status": "completed", "answer": "..."}
```

**Connection Handling:**
- Abort the request to cancel the investigation
- Server sends keep-alive pings to maintain connection
""",
    dependencies=[Depends(require_perm("run_investigation")), Depends(quota_gate)],
    responses={
        200: {
            "description": "SSE stream of investigation events",
            "content": {
                "text/event-stream": {
                    "example": 'data: {"type": "trace", "line": "Querying logs..."}\n\n'
                }
            },
        },
    },
)
async def chat_stream(req: ChatRequest):
    """Stream an investigation with real-time progress.

    Returns a Server-Sent Events stream with trace events as the agent
    investigates, followed by a terminal done or approval event.

    Args:
        req: Thread ID and message to investigate.

    Returns:
        EventSourceResponse: SSE stream of investigation events.
    """
    _check_message(req.message)  # raise 400/413 before the stream opens
    tid = _scoped(req.thread_id)
    # Capture the tenant here (contextvar reliably set in the auth dependency) and
    # re-establish it inside the generator, which may be iterated after the request
    # scope unwinds (SSE streaming behind middleware).
    cfg = tenant_context.get_tenant()

    async def gen():
        tok = tenant_context.set_tenant(cfg)
        # _stream_chat_turn keeps its own clock for the success sample; this one
        # exists for the failure path, which can be reached before the turn ever
        # starts (a session that fails to build).
        _t0 = time.monotonic()
        try:
            async with _running_turn(tid):
                existed = tid in _SESSIONS
                try:
                    session = await _get_session(tid)
                    if await session.pending_interrupt():
                        _AWAITING.add(tid)
                        yield {
                            "data": json.dumps(
                                {
                                    "type": "error",
                                    "thread_id": req.thread_id,
                                    "status": "error",
                                    "answer": "This thread is paused awaiting approval — "
                                    "approve or reject the pending action first.",
                                }
                            )
                        }
                        return
                    _AWAITING.discard(tid)
                    try:
                        async for payload in _stream_chat_turn(tid, req, session):
                            yield payload
                    except Exception as exc:  # noqa: BLE001 — one retry, then report
                        # A pooled session whose MCP transport died answers every
                        # later message with the same error, which bricked the
                        # thread for good. The failed call never reached the MCP
                        # server, so rebuild the session and re-run the turn once.
                        if not (existed and _transport_dead(exc)):
                            raise
                        log.warning(
                            "chat stream hit a dead MCP transport (thread=%s); "
                            "rebuilding the session and retrying the turn once",
                            tid,
                        )
                        await _evict(tid)
                        yield {
                            "data": json.dumps(
                                {
                                    "type": "trace",
                                    "thread_id": req.thread_id,
                                    "line": "🔌 tool connection dropped — reconnecting "
                                    "and restarting this step",
                                }
                            )
                        }
                        session = await _get_session(tid)
                        async for payload in _stream_chat_turn(tid, req, session):
                            yield payload
                except Exception as exc:  # noqa: BLE001
                    log.exception("chat stream failed (thread=%s)", tid)
                    _record_slo_failure(time.monotonic() - _t0)
                    # Always drop the session, not just a freshly built one: a
                    # turn that raised may have left the MCP transport or the
                    # graph in an unusable state, and keeping it in the pool made
                    # every subsequent message on the thread fail identically.
                    await _evict(tid)
                    yield {
                        "data": json.dumps(
                            {
                                "type": "error",
                                "thread_id": req.thread_id,
                                "status": "error",
                                "answer": _friendly_error(exc),
                            }
                        )
                    }
        finally:
            tenant_context.reset_tenant(tok)

    return EventSourceResponse(gen())


@app.post(
    "/approve/stream",
    tags=["Approval"],
    summary="Stream approval continuation (SSE)",
    description="Process an approval decision and stream the continuation via Server-Sent Events. "
    "Same event format as /chat/stream.",
    dependencies=[Depends(require_perm("approve_action"))],
    responses={
        200: {
            "description": "SSE stream of continuation events",
            "content": {
                "text/event-stream": {
                    "example": 'data: {"type": "trace", "line": "Executing approved action..."}\n\n'
                }
            },
        },
    },
)
async def approve_stream(req: ApproveRequest):
    """Stream the continuation after an approval decision.

    Returns a Server-Sent Events stream as the agent continues
    after the approved/rejected action.

    Args:
        req: Thread ID, approval decision, and optional reason.

    Returns:
        EventSourceResponse: SSE stream of continuation events.
    """
    tid = _scoped(req.thread_id)
    cfg = tenant_context.get_tenant()

    async def gen():
        tok = tenant_context.set_tenant(cfg)
        try:
            async with _running_turn(tid):
                existed = tid in _SESSIONS
                session = await _get_session(tid)  # rebuild from checkpointer if evicted
                if not await session.pending_interrupt():
                    if not existed:
                        await _evict(tid)
                    yield {
                        "data": json.dumps(
                            {
                                "type": "error",
                                "thread_id": req.thread_id,
                                "status": "error",
                                "answer": f"no investigation awaiting approval for thread '{req.thread_id}'",
                            }
                        )
                    }
                    return
                audit.record("approval.decided", thread=req.thread_id, approved=req.approved)
                # See the non-streaming /approve: the pause was never sampled, so
                # this is the only chance to count an investigation that went
                # through an approval gate.
                _t0 = time.monotonic()
                try:
                    async for ev in session.resume_stream(
                        approved=req.approved, reason=req.reason
                    ):
                        t = ev.get("type")
                        if t == "approval":
                            _AWAITING.add(tid)
                            _pause_paged(req.thread_id, ev.get("approval_request"))
                        elif t == "done":
                            _AWAITING.discard(tid)
                            await _meter_and_spend(ev.get("tokens_used", 0))
                            _elapsed = time.monotonic() - _t0
                            _record_slo_fields(
                                ev.get("report"),
                                ev.get("final_text", ""),
                                ev.get("tokens_used", 0),
                                _elapsed,
                            )
                            # Upserts onto the row the original turn created, so
                            # an approval-gated investigation still counts once.
                            await _record_dashboard_history_from_stream(
                                req.thread_id, ev, _elapsed
                            )
                        yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
                except Exception as exc:  # noqa: BLE001
                    log.exception("approve stream failed (thread=%s)", tid)
                    _record_slo_failure(time.monotonic() - _t0)
                    # Drop the session so the thread isn't stuck on a broken one.
                    # The checkpointer keeps the pending interrupt, so the human
                    # can approve again on a rebuilt session. Unlike /chat/stream
                    # this is never retried automatically: the approved action may
                    # have run before the failure, and re-running a write blind is
                    # exactly what the approval gate exists to prevent.
                    await _evict(tid)
                    yield {
                        "data": json.dumps(
                            {
                                "type": "error",
                                "thread_id": req.thread_id,
                                "status": "error",
                                "answer": _friendly_error(exc),
                            }
                        )
                    }
        finally:
            tenant_context.reset_tenant(tok)

    return EventSourceResponse(gen())


@app.get(
    "/metrics",
    tags=["Metrics"],
    summary="Get real-time metrics",
    description="Returns real-time metric series and error summaries from the configured "
    "logs/metrics source. Used by the dashboard for visualizations.",
    responses={
        200: {
            "description": "Metrics data",
            "content": {
                "application/json": {
                    "example": {
                        "services": {
                            "api-server": {
                                "latency_p99": [{"ts": "2024-03-15T10:00:00Z", "value": 125.5}]
                            }
                        },
                        "error_summary": {"total_errors": 523, "breakdown": []}
                    }
                }
            },
        }
    },
)
async def metrics() -> dict:
    """Get real-time metrics from configured sources.

    Returns:
        dict: Services metrics and error summary.
    """
    return metrics_source.read_all()


@app.get(
    "/metrics/slo",
    tags=["Metrics"],
    summary="Get SLO metrics (Prometheus)",
    description="Agent SLO metrics in Prometheus exposition format. Includes throughput, "
    "success/abstention rates, token costs, and latency histograms. Scrape into Prometheus/Grafana.",
    responses={
        200: {
            "description": "Prometheus metrics",
            "content": {
                "text/plain": {
                    "example": "# HELP copilot_investigations_total Total investigations\ncopilot_investigations_total 42"
                }
            },
        }
    },
)
async def slo_metrics_endpoint():
    """Get SLO metrics in Prometheus format.

    Returns:
        PlainTextResponse: Prometheus exposition format metrics.
    """
    from fastapi.responses import PlainTextResponse

    from app.slo_metrics import metrics as slo
    return PlainTextResponse(slo().render(), media_type="text/plain; version=0.0.4")


@app.get(
    "/usage",
    tags=["Metrics"],
    summary="Get usage statistics",
    description="Current billing period usage and quota for the tenant. Shows investigations "
    "used, remaining quota, and token consumption. Only applicable in multi-tenant mode.",
    responses={
        200: {
            "description": "Usage statistics",
            "content": {
                "application/json": {
                    "examples": {
                        "multi_tenant": {
                            "summary": "Multi-tenant usage",
                            "value": {
                                "plan": "team",
                                "period": "2024-03",
                                "investigations_used": 45,
                                "investigations_quota": 100,
                                "investigations_remaining": 55,
                                "tokens_used": 125000
                            }
                        },
                        "single_tenant": {
                            "summary": "Single-tenant (no quotas)",
                            "value": {
                                "multi_tenant": False,
                                "detail": "usage quotas apply in multi-tenant mode only"
                            }
                        }
                    }
                }
            },
        }
    },
)
async def usage() -> dict:
    """Get current usage and quota information.

    Returns:
        dict: Usage summary with quota details, or message if not applicable.
    """
    summary = await metering.usage_summary()
    if summary is None:
        return {"multi_tenant": False, "detail": "usage quotas apply in multi-tenant mode only"}
    return summary


@app.get(
    "/cache/stats",
    tags=["Metrics"],
    summary="Get cache statistics",
    description="Returns cache backend statistics including hit rate, size, and evictions. "
    "Useful for monitoring cache effectiveness.",
    responses={
        200: {
            "description": "Cache statistics",
            "content": {
                "application/json": {
                    "example": {
                        "hits": 1234,
                        "misses": 56,
                        "hit_rate": 0.956,
                        "size": 128,
                        "evictions": 12
                    }
                }
            },
        }
    },
)
async def cache_stats() -> dict:
    """Get cache backend statistics.

    Returns:
        dict: Cache hit rate, size, and eviction counts.
    """
    from app.cache import get_cache
    return get_cache().stats()


@app.get(
    "/api/investigations",
    tags=["Investigations"],
    summary="List investigations",
    description="List investigation history with optional filtering by status. "
    "Returns investigations from the in-memory store (or Supabase if configured).",
    responses={
        200: {
            "description": "List of investigations",
            "content": {
                "application/json": {
                    "example": {
                        "investigations": [
                            {
                                "id": "inv-abc123",
                                "thread_id": "thread-xyz",
                                "query": "Why is API latency high?",
                                "summary": "Root cause: Database connection pool exhaustion",
                                "status": "completed",
                                "tokens_used": 15000,
                                "latency_ms": 45000,
                                "feedback_rating": None,
                                "created_at": "2024-03-15T10:00:00Z",
                                "completed_at": "2024-03-15T10:01:30Z",
                                "report": {}
                            }
                        ]
                    }
                }
            },
        }
    },
)
async def list_investigations(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List investigations with optional filtering.

    Args:
        status: Filter by status (completed, active, error).
        limit: Maximum number of investigations to return.
        offset: Number of investigations to skip.

    Returns:
        dict: List of investigations.
    """
    # Reads the `investigations` table. This used to import a module-level dict
    # from app/api/graphql/schema.py, which imports strawberry — not a
    # dependency — so the history page got a 500 on every load.
    return {
        "investigations": await investigation_history.recent(
            status=status, limit=limit, offset=offset
        )
    }


@app.post(
    "/feedback",
    tags=["Feedback"],
    summary="Submit investigation feedback",
    description="Submit thumbs up/down feedback on an investigation. Negative feedback on real "
    "failures seeds the regression eval suite for continuous improvement.",
    responses={
        200: {
            "description": "Feedback recorded",
            "content": {
                "application/json": {
                    "example": {
                        "status": "recorded",
                        "feedback": {
                            "thread_id": "inv-abc123",
                            "rating": "up",
                            "comment": "Accurate diagnosis"
                        }
                    }
                }
            },
        },
        400: {"description": "Invalid rating value"},
    },
)
async def submit_feedback(req: FeedbackRequest) -> dict:
    """Submit feedback on an investigation.

    Args:
        req: Thread ID, rating (up/down), optional comment and question.

    Returns:
        dict: Confirmation with recorded feedback entry.

    Raises:
        HTTPException: If rating is invalid.
    """
    try:
        entry = feedback.record_feedback(req.thread_id, req.rating, req.comment, req.question)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Also stamp the history row, so the history page can show which
    # investigations were rated. Best-effort: the feedback itself is recorded.
    await investigation_history.set_feedback(req.thread_id, req.rating, req.comment)
    return {"status": "recorded", "feedback": entry}


# --- Slack Integration --- #
class SlackShareRequest(BaseModel):
    query: str
    report: dict
    channel: str = ""


@app.post(
    "/share/slack",
    tags=["Feedback"],
    summary="Share report to Slack",
    description="Post an investigation report to a Slack channel via webhook or bot token.",
    responses={
        200: {"description": "Report shared successfully"},
        400: {"description": "Slack not configured"},
    },
)
async def share_to_slack(req: SlackShareRequest) -> dict:
    """Share an investigation report to Slack."""
    from app.integrations.slack_notify import is_slack_configured, post_to_slack

    if not is_slack_configured():
        raise HTTPException(400, "Slack is not configured. Set SLACK_WEBHOOK_URL or SLACK_BOT_TOKEN.")

    result = await post_to_slack(
        report=req.report,
        query=req.query,
        channel=req.channel or None
    )

    if not result.get("success"):
        raise HTTPException(400, result.get("message", "Failed to post to Slack"))

    return result


@app.get(
    "/integrations/status",
    tags=["Config"],
    summary="Get integration status",
    description="Check which integrations are configured and available.",
)
async def integration_status() -> dict:
    """Get status of configured integrations.

    True means "credentials are present, so this connector talks to the real
    service"; false means it serves the bundled offline fixtures instead. Every
    connector in `MCP_CATALOG` that has a live mode is answered for here, so a
    caller can tell live from demo for all of them rather than for some.

    The sidebar labelled all eleven catalog entries "connected" because the
    catalog is a static list with no state and this endpoint only covered five
    of them; a GitHub row could read "connected" directly above its own
    "Offline demo" widget.
    """
    from app.integrations.slack_notify import is_slack_configured

    settings = get_settings()
    return {
        "slack": is_slack_configured(),
        "github": runtime.github_connected(),
        "pagerduty": bool(settings.pagerduty_api_token),
        "datadog": bool(settings.dd_api_key and settings.dd_app_key),
        "sentry": bool(settings.sentry_api_token),
        "prometheus": bool(settings.prometheus_url),
        "kubernetes": bool(settings.kube_config_path),
        "traces": bool(settings.traces_api_url),
        "deploys": bool(settings.deploys_api_url),
        "argocd": bool(settings.argocd_server_url and settings.argocd_auth_token),
        "terraform": bool(settings.terraform_cloud_token and settings.terraform_organization),
    }


@app.get(
    "/api/metrics/prometheus/query",
    tags=["Metrics"],
    summary="Run an instant PromQL query",
    description="Proxy an instant PromQL query to the configured Prometheus. The browser "
    "cannot reach Prometheus directly (it is not CORS-enabled and is usually not "
    "internet-facing), so the Service Map goes through here. Returns 503 naming the "
    "missing setting when PROMETHEUS_URL is not configured.",
    responses={
        200: {"description": "Prometheus instant-query vector"},
        502: {"description": "Prometheus refused or could not be reached"},
        503: {"description": "PROMETHEUS_URL is not configured"},
    },
)
async def prometheus_instant_query(query: str) -> dict:
    """Proxy one instant PromQL query.

    Args:
        query: PromQL expression.

    Returns:
        dict: ``{"result": [{"metric": str, "labels": {...}, "value": float,
        "timestamp": iso}, ...], "source": url}``.
    """
    if not query.strip():
        raise HTTPException(400, "query is required")
    url = get_settings().prometheus_url.strip()
    if not url:
        raise HTTPException(
            503,
            "Prometheus is not configured. Set PROMETHEUS_URL to your Prometheus "
            "HTTP API base (e.g. http://prometheus:9090).",
        )

    from app.connectors.base import ConnectorConfig
    from app.connectors.prometheus import PrometheusConnector

    connector = PrometheusConnector(ConnectorConfig(url=url))
    try:
        result = await connector.query(query)
    finally:
        await connector.close()
    if not result.success:
        raise HTTPException(502, f"Prometheus query failed: {result.error}")
    return {"result": result.data, "source": url}


@app.get(
    "/audit",
    tags=["Audit"],
    summary="Query audit trail",
    description="Query the audit trail for recent events. Returns approvals, model changes, "
    "prompt-injection detections, and feedback events. Supports filtering by event type prefix. "
    "Requires view_audit permission (admin+).",
    dependencies=[Depends(require_perm("view_audit"))],
    responses={
        200: {
            "description": "Audit events",
            "content": {
                "application/json": {
                    "example": {
                        "events": [{
                            "ts": "2024-03-15T10:30:00Z",
                            "event": "approval.decided",
                            "org_id": "acme-corp",
                            "actor": "user@acme.com",
                            "thread": "inv-abc123",
                            "approved": True
                        }]
                    }
                }
            },
        }
    },
)
async def get_audit(limit: int = 100, event_prefix: str = "") -> dict:
    """Query the audit trail.

    Args:
        limit: Maximum events to return (1-1000, default 100).
        event_prefix: Filter by event type prefix (e.g., "approval.").

    Returns:
        dict: List of audit events, newest first.
    """
    limit = max(1, min(limit, 1000))
    return {"events": await audit.recent(limit=limit, event_prefix=event_prefix)}


@app.get(
    "/audit/verify",
    tags=["Audit"],
    summary="Verify audit chain integrity",
    description="Verify that the audit hash-chain is intact (tamper-evidence check). "
    "Reports the first broken link if any tampering is detected. Scoped to the caller's "
    "org and requires view_audit permission (admin+).",
    dependencies=[Depends(require_perm("view_audit"))],
    responses={
        200: {
            "description": "Verification result",
            "content": {
                "application/json": {
                    "examples": {
                        "valid": {
                            "summary": "Chain intact",
                            "value": {"valid": True, "count": 1542}
                        },
                        "invalid": {
                            "summary": "Tampering detected",
                            "value": {"valid": False, "broken_at": 1234}
                        }
                    }
                }
            },
        }
    },
)
async def audit_verify() -> dict:
    """Verify audit chain integrity.

    Returns:
        dict: Verification result with valid flag and count or broken index.
    """
    # Scoped to the caller's org: an unscoped call reported every other tenant's
    # event count (and, on a broken link, their event name). '-' in single-tenant
    # mode, which matches every entry there.
    #
    # `verify_org_chain` rather than `verify_all_chains`: the stored chains are
    # per org, and this route is reachable by any org admin, so the fleet-wide
    # roll-up would hand one tenant every other tenant's chain summary. Use
    # `audit.verify_all_chains()` from an operator context if you need the fleet
    # view.
    return await audit.verify_org_chain(tenant_context.tenant_id())


# --------------------------------------------------------------------------- #
# Admin / tenant self-management (multi-tenant only; RBAC-gated).
# Org provisioning (the first owner key) is done out-of-band via the CLI:
#   python -m app.cli provision-org --name "Acme" --email owner@acme.com
# --------------------------------------------------------------------------- #
def _current_org_id() -> str:
    cfg = tenant_context.get_tenant()
    if cfg is None:
        raise HTTPException(400, "Admin endpoints require multi-tenant mode + a tenant API key.")
    return cfg.org_id


@app.get(
    "/admin/org",
    tags=["Admin"],
    summary="Get organization details",
    description="Returns details about the current organization including name, plan, "
    "member count, and active API keys. Requires multi-tenant mode.",
    dependencies=[Depends(require_perm("view"))],
    responses={
        200: {
            "description": "Organization details",
            "content": {
                "application/json": {
                    "example": {
                        "id": "org_abc123",
                        "name": "Acme Corp",
                        "plan": "team",
                        "members": 5,
                        "active_api_keys": 3,
                        "integrations": 4
                    }
                }
            },
        },
        400: {"description": "Multi-tenant mode required"},
        404: {"description": "Organization not found"},
    },
)
async def admin_org() -> dict:
    """Get organization details.

    Returns:
        dict: Organization info with member and key counts.
    """
    org_id = _current_org_id()
    store = tenant_auth.get_store()
    org = await store.get_org(org_id)
    if org is None:
        raise HTTPException(404, "org not found")
    return {
        "id": org.id, "name": org.name, "plan": org.plan,
        "members": await store.count_members(org_id),
        "active_api_keys": await store.count_active_api_keys(org_id),
        "integrations": await store.count_integrations(org_id),
    }


@app.get(
    "/admin/api-keys",
    tags=["Admin"],
    summary="List API keys",
    description="List all API keys for the organization. Returns metadata only (not secrets). "
    "Requires manage_api_keys permission.",
    dependencies=[Depends(require_perm("manage_api_keys"))],
    responses={
        200: {
            "description": "List of API keys",
            "content": {
                "application/json": {
                    "example": {
                        "api_keys": [{
                            "id": "key_abc123",
                            "prefix": "dcp_abc",
                            "name": "CI/CD Pipeline",
                            "role": "responder",
                            "created_at": "2024-03-01T00:00:00Z",
                            "last_used_at": "2024-03-15T10:30:00Z",
                            "active": True
                        }]
                    }
                }
            },
        },
    },
)
async def admin_list_api_keys() -> dict:
    """List all API keys for the organization.

    Returns:
        dict: List of API key metadata (not secrets).
    """
    org_id = _current_org_id()
    keys = await tenant_auth.get_store().list_api_keys(org_id)
    return {"api_keys": [
        {"id": k.id, "prefix": k.prefix, "name": k.name, "role": k.role,
         "created_at": k.created_at, "last_used_at": k.last_used_at, "active": k.active}
        for k in keys
    ]}


@app.post(
    "/admin/api-keys",
    tags=["Admin"],
    summary="Create API key",
    description="Create a new API key for the organization. The secret is returned only once. "
    "Subject to plan quota limits. Requires manage_api_keys permission.",
    dependencies=[Depends(require_perm("manage_api_keys"))],
    responses={
        200: {
            "description": "API key created",
            "content": {
                "application/json": {
                    "example": {
                        "api_key": "dcp_xxxxxxxxxxxxxxxxxxxx",
                        "id": "key_abc123",
                        "role": "responder",
                        "name": "CI/CD Pipeline",
                        "note": "Store this now - the secret is shown only once."
                    }
                }
            },
        },
        402: {"description": "API key quota exceeded"},
    },
)
async def admin_create_api_key(req: CreateApiKeyRequest) -> dict:
    """Create a new API key.

    Args:
        req: Key name and role.

    Returns:
        dict: New API key (secret shown only once).
    """
    org_id = _current_org_id()
    store = tenant_auth.get_store()
    cfg = tenant_context.get_tenant()
    plan = cfg.plan if cfg else "free"
    if not tn_models.within_quota(plan, "api_keys", await store.count_active_api_keys(org_id)):
        raise HTTPException(402, "Active API-key quota reached for your plan.")
    plaintext, rec = await store.issue_api_key(
        org_id, req.name, role=tn_models.normalize_role(req.role)
    )
    audit.record("apikey.created", org=org_id, key_id=rec.id, role=rec.role)
    return {"api_key": plaintext, "id": rec.id, "role": rec.role, "name": rec.name,
            "note": "Store this now — the secret is shown only once."}


@app.delete(
    "/admin/api-keys/{key_id}",
    tags=["Admin"],
    summary="Revoke API key",
    description="Revoke an API key immediately. The key will no longer authenticate. "
    "Requires manage_api_keys permission.",
    dependencies=[Depends(require_perm("manage_api_keys"))],
    responses={
        200: {"description": "Key revoked"},
        404: {"description": "Key not found or already revoked"},
    },
)
async def admin_revoke_api_key(key_id: str) -> dict:
    """Revoke an API key.

    Args:
        key_id: The API key ID to revoke.

    Returns:
        dict: Confirmation of revocation.
    """
    org_id = _current_org_id()
    revoked = await tenant_auth.get_store().revoke_api_key(key_id, org_id=org_id)
    if not revoked:
        raise HTTPException(404, "key not found (or already revoked) in this org")
    audit.record("apikey.revoked", org=org_id, key_id=key_id)
    return {"status": "revoked", "id": key_id}


@app.post(
    "/admin/members",
    tags=["Admin"],
    summary="Add organization member",
    description="Add a new member to the organization with the specified role. "
    "Subject to plan seat quota. Requires manage_members permission.",
    dependencies=[Depends(require_perm("manage_members"))],
    responses={
        200: {
            "description": "Member added",
            "content": {
                "application/json": {
                    "example": {"email": "user@acme.com", "role": "responder"}
                }
            },
        },
        402: {"description": "Seat quota exceeded"},
    },
)
async def admin_add_member(req: AddMemberRequest) -> dict:
    """Add a member to the organization.

    Args:
        req: Member email and role.

    Returns:
        dict: Added member details.
    """
    org_id = _current_org_id()
    store = tenant_auth.get_store()
    cfg = tenant_context.get_tenant()
    plan = cfg.plan if cfg else "free"
    if not tn_models.within_quota(plan, "seats", await store.count_members(org_id)):
        raise HTTPException(402, "Seat quota reached for your plan.")
    user = await store.create_user(req.email)
    m = await store.add_member(org_id, user.id, tn_models.normalize_role(req.role))
    audit.record("member.added", org=org_id, role=m.role)
    return {"email": user.email, "role": m.role}


@app.get(
    "/admin/integrations",
    tags=["Admin"],
    summary="List integrations",
    description="List configured integrations for the organization. Returns names only "
    "(never secrets). Requires manage_integrations permission.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {
            "description": "Integration names",
            "content": {
                "application/json": {
                    "example": {"integrations": ["datadog", "pagerduty", "slack"]}
                }
            },
        },
    },
)
async def admin_list_integrations() -> dict:
    """List configured integrations.

    Returns:
        dict: List of integration names (no secrets).
    """
    org_id = _current_org_id()
    secrets = await tenant_auth.get_store().get_integration_secrets(org_id)
    return {"integrations": sorted(secrets.keys())}  # names only — never values


@app.post(
    "/admin/integrations",
    tags=["Admin"],
    summary="Set integration secret",
    description="Configure an integration secret (e.g., Datadog API key, PagerDuty token). "
    "Requires manage_integrations permission.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Integration saved"},
        400: {"description": "Integration name required"},
    },
)
async def admin_set_integration(req: SetIntegrationRequest) -> dict:
    """Set an integration secret.

    Args:
        req: Integration name and secret value.

    Returns:
        dict: Confirmation with integration name.
    """
    org_id = _current_org_id()
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "integration name is required")
    await tenant_auth.get_store().set_integration_secret(org_id, name, req.value)
    audit.record("secret.write", org=org_id, name=name)
    return {"status": "saved", "name": name}


@app.delete(
    "/admin/integrations/{name}",
    tags=["Admin"],
    summary="Disconnect an integration",
    description="Delete the stored secret for an integration, disconnecting it. "
    "Requires manage_integrations permission.",
    dependencies=[Depends(require_perm("manage_integrations"))],
    responses={
        200: {"description": "Integration disconnected"},
        404: {"description": "No such integration is configured"},
    },
)
async def admin_delete_integration(name: str) -> dict:
    """Disconnect an integration by deleting its stored secret.

    There was no delete route at all, so the marketplace's Disconnect button got
    a 405 and a credential could be stored but never withdrawn.
    """
    org_id = _current_org_id()
    key = name.strip()
    if not key:
        raise HTTPException(400, "integration name is required")
    if not await tenant_auth.get_store().delete_integration_secret(org_id, key):
        raise HTTPException(404, f"Integration '{key}' is not configured.")
    audit.record("secret.delete", org=org_id, name=key)
    return {"status": "disconnected", "name": key}


def _operator_guard(request: Request) -> None:
    """Authenticate a deployment-operator request via COPILOT_OPERATOR_TOKEN.

    Used for mutations that decide what a tenant has paid for. A tenant permission
    is not sufficient here: `manage_billing` resolves to the owner role, and every
    self-serve signup issues an owner key, so a role check would let a free account
    grant itself the most expensive plan. Raises 403 when unconfigured, 401 on a bad
    token.
    """
    s = get_settings()
    if not s.copilot_operator_token:
        raise HTTPException(403, "Operator endpoints are not configured on this deployment.")
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not secrets.compare_digest(token, s.copilot_operator_token):
        raise HTTPException(401, "Invalid operator token.")


@app.patch(
    "/admin/plan",
    tags=["Admin"],
    summary="Update organization plan (operator only)",
    description="Change an organization's subscription plan. This is an operator/back-office "
    "endpoint: it requires COPILOT_OPERATOR_TOKEN, not a tenant API key. Customer-driven "
    "plan changes go through billing (Stripe checkout and the webhook), never through here.",
    dependencies=[Depends(_operator_guard)],
    responses={
        200: {
            "description": "Plan updated",
            "content": {
                "application/json": {
                    "example": {"plan": "team"}
                }
            },
        },
        401: {"description": "Invalid operator token"},
        403: {"description": "Operator endpoints not configured"},
    },
)
async def admin_set_plan(req: SetPlanRequest, org_id: str = "") -> dict:
    """Update an organization's plan.

    Args:
        req: New plan tier.
        org_id: Target organization. Defaults to the ambient tenant when omitted,
            which is the single-tenant case.

    Returns:
        dict: Updated plan.
    """
    target = org_id.strip() or _current_org_id()
    plan = tn_models.normalize_plan(req.plan)
    await tenant_auth.get_store().set_plan(target, plan)
    audit.record("plan.changed", org=target, plan=plan, actor="operator")
    return {"plan": plan}


# --------------------------------------------------------------------------- #
# Self-serve onboarding — a new tenant creates an org + owner key with no prior
# credential (the entry point for product signup). Auth-exempt (see require_auth)
# but still IP rate-limited. Always provisions the FREE plan; upgrades go through
# billing/admin so signup can't self-assign a paid tier.
# --------------------------------------------------------------------------- #
async def _scim_guard(request: Request) -> str:
    """Authenticate a SCIM request via COPILOT_SCIM_TOKEN and return the target org.
    Raises 403 when SCIM is unconfigured, 401 on a bad token, 503 when
    COPILOT_SCIM_ORG names an org that does not exist.

    That last check is the point of this being async. The guard only tested that
    the setting was non-empty, so a typo'd org id made every provisioning call
    return 201 to the IdP while writing a membership row pointing at nothing —
    and `resolve_session`/`resolve_jwt` return None when the org lookup misses,
    so each "successfully provisioned" user got 401 on every route. Failing here
    puts the error in front of the administrator who can fix it."""
    s = get_settings()
    if not (s.copilot_scim_token and s.copilot_scim_org):
        raise HTTPException(403, "SCIM provisioning is not configured.")
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not hmac.compare_digest(token, s.copilot_scim_token):
        raise HTTPException(401, "Invalid SCIM token.")
    if await tenant_auth.get_store().get_org(s.copilot_scim_org) is None:
        raise HTTPException(
            503,
            f"COPILOT_SCIM_ORG '{s.copilot_scim_org}' is not a provisioned "
            "organization; users cannot be added to it.",
        )
    return s.copilot_scim_org


@app.post(
    "/scim/v2/Users",
    tags=["Admin"],
    summary="SCIM user provisioning",
    description="SCIM 2.0 endpoint for IdP-pushed user provisioning. Creates a user and adds "
    "them to the configured SCIM organization. Requires COPILOT_SCIM_TOKEN authentication.",
    status_code=201,
    responses={
        201: {"description": "User provisioned"},
        400: {"description": "Invalid SCIM payload"},
        401: {"description": "Invalid SCIM token"},
        403: {"description": "SCIM not configured"},
    },
)
async def scim_create_user(request: Request) -> dict:
    """SCIM 2.0 user provisioning.

    Creates a user and adds them to the SCIM organization with the default role.

    Returns:
        dict: SCIM user resource.
    """
    from app.tenancy import scim
    org_id = await _scim_guard(request)
    payload = scim.parse_scim_user(await request.json())
    if not payload["email"]:
        raise HTTPException(400, "SCIM user requires userName (email).")
    store = tenant_auth.get_store()
    user = await store.get_user_by_email(payload["email"]) or await store.create_user(payload["email"])
    await store.add_member(org_id, user.id, get_settings().copilot_scim_default_role)
    audit.record("scim.user_provisioned", org_id=org_id, email=payload["email"])
    return scim.to_scim_user(user.id, user.email, active=True,
                             role=get_settings().copilot_scim_default_role)


@app.api_route(
    "/scim/v2/Users/{email}",
    methods=["PATCH", "DELETE"],
    tags=["Admin"],
    summary="SCIM user deprovisioning",
    description="SCIM 2.0 endpoint for user deprovisioning. Removes the user's membership from "
    "the SCIM organization. Requires COPILOT_SCIM_TOKEN authentication.",
    responses={
        200: {"description": "User deprovisioned"},
        400: {"description": "Unsupported SCIM operation"},
        401: {"description": "Invalid SCIM token"},
        403: {"description": "SCIM not configured"},
    },
)
async def scim_deprovision_user(email: str, request: Request) -> dict:
    """SCIM 2.0 user deprovisioning.

    Removes the user's membership from the SCIM organization.

    Args:
        email: User email to deprovision.

    Returns:
        dict: Deprovisioning confirmation.
    """
    from app.tenancy import scim
    org_id = await _scim_guard(request)
    # DELETE always deprovisions; PATCH deprovisions when it sets active=false.
    if request.method == "PATCH":
        body = await request.json()
        if not scim.is_deprovision(body):
            raise HTTPException(400, "Unsupported SCIM PATCH (only active=false is handled).")
    store = tenant_auth.get_store()
    user = await store.get_user_by_email(email.strip().lower())
    removed = await store.remove_member(org_id, user.id) if user else False
    audit.record("scim.user_deprovisioned", org_id=org_id, email=email, removed=removed)
    return {"deprovisioned": removed, "email": email}


@app.post(
    "/signup",
    tags=["Admin"],
    summary="Self-serve organization signup",
    description="""
Create a new organization and owner API key without prior authentication.

**Requirements:**
- Multi-tenant mode must be enabled (COPILOT_MULTI_TENANT=true)
- Self-serve signup must be enabled (COPILOT_SIGNUP_ENABLED=true)

**Restrictions:**
- One organization per email address
- Always provisions the FREE plan (upgrade via billing)
- IP rate-limited to prevent abuse
""",
    responses={
        200: {
            "description": "Organization created",
            "content": {
                "application/json": {
                    "example": {
                        "org_id": "org_abc123",
                        "org_name": "Acme Corp",
                        "plan": "free",
                        "role": "owner",
                        "api_key": "dcp_xxxxxxxxxxxxxxxxxxxx",
                        "note": "Store this key now - it is shown only once."
                    }
                }
            },
        },
        400: {"description": "Invalid input or multi-tenant not enabled"},
        403: {"description": "Self-serve signup disabled"},
        409: {"description": "Email already registered"},
    },
)
async def signup(req: SignupRequest) -> dict:
    """Create a new organization (self-serve signup).

    Creates a new organization with the FREE plan and returns an owner API key.
    The API key secret is shown only once.

    Args:
        req: Organization name and owner email.

    Returns:
        dict: New organization details and API key.

    Raises:
        HTTPException: If signup is disabled or email is already registered.
    """
    s = get_settings()
    if not s.copilot_multi_tenant:
        raise HTTPException(400, "Self-serve signup requires multi-tenant mode (COPILOT_MULTI_TENANT=true).")
    if not s.copilot_signup_enabled:
        raise HTTPException(403, "Self-serve signup is disabled on this deployment.")
    name = (req.org_name or "").strip()
    email = (req.email or "").strip().lower()
    if not name or len(name) > 120:
        raise HTTPException(400, "Provide an organization name (1–120 chars).")
    if "@" not in email or "." not in email.split("@")[-1] or len(email) > 254:
        raise HTTPException(400, "Provide a valid email address.")
    store = tenant_auth.get_store()  # schema created at startup (lifespan)
    # One org per email: caps free-tier farming on this unauthenticated path and
    # refuses to bind an already-registered identity to a fresh org. (A never-seen
    # email can still be claimed here — real email verification before owner binding
    # needs mail infra and is deferred; disable signup or require SSO when that
    # threat matters. See docs/COMMERCIALIZATION.md.)
    if await store.get_membership_by_email(email) is not None:
        raise HTTPException(409, "An account already exists for this email.")
    org = await store.create_org(name, plan="free", owner_email=email)
    api_key, rec = await store.issue_api_key(org.id, name="owner-key", role="owner")
    audit.record("org.signup", org=org.id, key_id=rec.id, plan=org.plan)
    return {
        "org_id": org.id,
        "org_name": org.name,
        "plan": org.plan,
        "role": "owner",
        "api_key": api_key,
        "note": "Store this key now — it is shown only once. Use it as a Bearer token.",
    }


# --------------------------------------------------------------------------- #
# Triggers — PagerDuty webhook -> investigate -> Slack approve/reject
# (the product loop: the agent shows up when you're paged, instead of being asked)
# --------------------------------------------------------------------------- #
async def _post_to_slack(thread_id: str, title: str, result: TurnResult) -> None:
    s = get_settings()
    if result.status == "awaiting_approval":
        req = result.approval_request or {}
        detail = "\n".join(
            f"• `{a.get('tool')}`" + (" *(write)*" if a.get("write") else "")
            for a in req.get("actions", [])
        ) or req.get("message", "")
        blocks, text = slack.approval_blocks(thread_id, title, detail), f"Approval needed: {title}"
    elif result.report:
        # Prefer the structured RCA verdict (severity/root-cause/actions at a glance).
        blocks = slack.report_blocks(title, result.report, result.final_text)
        text = f"Investigation: {title}"
    else:
        blocks, text = slack.result_blocks(title, result.final_text), f"Investigation: {title}"
    res = await slack.post_message(s.slack_bot_token, s.slack_channel, text, blocks)
    if not res.get("ok"):
        log.info("slack post skipped/failed (thread=%s): %s", thread_id, res.get("skipped") or res)


# The background job queue (set in lifespan). Investigations are enqueued here so a
# transient failure retries and a restart doesn't silently drop them.
_JOBQ: Any = None


async def _handle_job(job) -> None:
    """Dispatch a queued job by kind. Raises on failure so the worker retries."""
    if job.kind == "investigate":
        await _run_triggered_investigation(job.payload)
    else:
        log.warning("unknown job kind: %s", job.kind)


async def _enqueue_investigation(incident: dict) -> None:
    """Queue a triggered/proactive investigation (falls back to a detached task if the
    worker isn't up, e.g. under TestClient without lifespan).

    The org the incident belongs to is stamped onto the payload here, while the
    request that triggered it is still on the stack. The worker drains the queue
    from the lifespan task, which has no request context at all, so whatever is
    not captured at this moment is gone by the time the investigation runs.
    """
    from app.jobqueue import Job

    cfg = tenant_context.get_tenant()
    if cfg is not None and not str(incident.get("org") or "").strip():
        incident = {**incident, "org": cfg.org_id}
    if _JOBQ is not None:
        await _JOBQ.enqueue(Job(kind="investigate", payload=incident, id=str(incident.get("id", ""))))
    else:
        asyncio.create_task(_run_triggered_investigation(incident))


async def _tenant_for_org(org_id: str) -> tenant_context.TenantConfig | None:
    """Rebuild a tenant's config (plan + decrypted credentials) from its org id.

    Only the org id travels on the job payload: the queue is a Redis list on the
    production path, and a tenant's decrypted integration secrets have no business
    sitting in it. The credentials are re-read from the tenant store here, at the
    moment the job runs, which also means a revoked or rotated secret takes effect
    on the next job rather than at enqueue time.

    The role is fixed at `responder` — the least privilege that may run an
    investigation. A background job has no human actor whose seat could be
    consulted, and inheriting `owner` from whoever clicked Investigate would hand
    the job more authority than the work needs.
    """
    try:
        store = tenant_auth.get_store()
        org = await store.get_org(org_id)
        if org is None:
            return None
        secrets = await store.get_integration_secrets(org_id)
        return tenant_auth.build_tenant_config(org, "responder", secrets)
    except Exception:  # noqa: BLE001 — a resolution failure must not run as the host
        log.exception("could not rebuild tenant context for org %s", org_id)
        return None


async def _run_triggered_investigation(incident: dict) -> None:
    """Re-establish the incident's tenant, then investigate it.

    The worker that calls this runs as a lifespan task, so `current_tenant` is
    None no matter which org's request queued the job. Everything downstream
    reads that contextvar: `runtime.github_token()` and `provider_key()` fall
    back to the host's .env, and `tenant_context.storage_org_id()` files the
    history row under "default". Left unset, org A's incident was investigated
    with the operator's GitHub token against the operator's repo, the RCA quoted
    another party's private code, the row never appeared on A's history page, and
    `metering.record_investigation` returned early so the run was never billed.

    So the tenant named on the payload is re-established for the whole run, the
    same way `EscalationSweeper._announce_escalation` does it for the timer path.
    If it cannot be re-established the job is refused: running it with the host's
    credentials is the failure being fixed, not a degraded success.
    """
    org = str(incident.get("org") or "").strip()
    multi_tenant = get_settings().copilot_multi_tenant
    if not multi_tenant or not org or org == "-":
        # Single-tenant, or a deployment-level trigger such as the SLO poller,
        # which belongs to no org: there is no tenant to restore and the host's
        # own configuration is the right one. On a multi-tenant deployment say so
        # out loud, because there the run really is unattributed.
        if multi_tenant:
            log.warning(
                "triggered investigation for incident %s carries no org — it will run "
                "with the deployment's own credentials and be filed under %s",
                incident.get("id"),
                tenant_context.DEFAULT_STORAGE_ORG,
            )
        await _investigate_incident(incident)
        return

    cfg = await _tenant_for_org(org)
    if cfg is None:
        log.error(
            "refusing triggered investigation for incident %s: org %s could not be "
            "resolved, and running it with no tenant would use the deployment's "
            "credentials and file the result under the wrong org",
            incident.get("id"),
            org,
        )
        await webhook_receivers.record_incident_outcome(
            incident, None, error=f"unknown organisation '{org}' — investigation not run"
        )
        return
    await tenant_context.run_with_tenant(cfg, _investigate_incident(incident))


async def _investigate_incident(incident: dict) -> None:
    # "pd" unless the trigger says otherwise — alert webhooks and CI receivers
    # feed this too, and a thread named pd-<x> for a GitLab pipeline is a lie.
    source = str(incident.get("source") or "pd")
    thread_id = f"{source}-{incident['id']}"
    title = incident.get("title") or thread_id
    origin = {"pd": "A PagerDuty incident was triggered"}.get(
        source, f"An alert was ingested from {source}"
    )
    seed = (
        f"{origin}: {title}"
        + (f" (service: {incident['service']})" if incident.get("service") else "")
        + ". Investigate the root cause from logs, metrics, code, and recent changes, "
        "then propose a fix."
    )
    _t0 = time.monotonic()
    # The internal session key is tenant-namespaced, exactly as the interactive
    # /chat path does it: two orgs whose providers both number an incident 123
    # would otherwise resume each other's checkpointed thread. History and the
    # dashboard keep the plain id — those rows already carry org_id.
    tid = _scoped(thread_id)
    try:
        async with _running_turn(tid):
            session = await _get_session(tid)
            result = await session.ask(seed)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
                # Also announced on the org's own channels, not just via
                # `_post_to_slack` below: that posts with the deployment's bot
                # token to the deployment's channel, which on a multi-tenant
                # install is not the org's channel and may not be configured at
                # all. This is the path with nobody watching a browser tab.
                _pause_paged(thread_id, result.approval_request)
            elif result.status == "completed":
                # Meter the triggered path the same as the interactive one (no-op
                # without a tenant); idem-keyed by the incident so a re-run can't double-bill.
                await _meter_and_spend(result.tokens_used, idem=thread_id)
                # Alert-triggered investigations are the ones an operator most
                # wants counted in MTTR — they start when the page fires, not when
                # someone gets to a keyboard.
                elapsed = time.monotonic() - _t0
                _record_slo(result, elapsed)
                report = result.report or {}
                await investigation_history.record(
                    thread_id,
                    seed,
                    status=result.status,
                    summary=report.get("root_cause") or result.final_text or None,
                    report=report or None,
                    latency_ms=int(elapsed * 1000),
                )
                await _record_dashboard_history(thread_id, result, elapsed)
        # The scoped key, because the approval block puts it on the button and
        # `_resume_triggered` has to find this exact session again.
        await _post_to_slack(tid, title, result)
        # An alert-ingested incident carries the row it came from: write the real
        # verdict back onto it, so the stored investigation reflects this run
        # rather than the placeholder values it was created with.
        await webhook_receivers.record_incident_outcome(incident, result)
    except Exception as exc:  # noqa: BLE001 — best-effort background trigger
        log.exception("triggered investigation failed (incident=%s)", incident.get("id"))
        _record_slo_failure(time.monotonic() - _t0)
        await webhook_receivers.record_incident_outcome(
            incident, None, error=f"{type(exc).__name__}: {exc}"
        )


async def _slo_trigger(service: str, burn: dict) -> None:
    """Adapt an SLO burn alert into the shared triggered-investigation path."""
    verdict = burn.get("verdict", "burn-rate alert")
    incident = {
        "id": f"slo-{service}",
        "title": f"SLO burn alert: {service} — {verdict}",
        "service": service,
    }
    log.info("SLO poller opening investigation for %s (%s)", service, verdict)
    await _enqueue_investigation(incident)


@app.post(
    "/remediate",
    tags=["Autonomy"],
    summary="Execute autonomous remediation",
    description="""
Execute an autonomous remediation action with observation and auto-revert.

**Safety Gates:**
1. COPILOT_AUTONOMY must be enabled
2. The caller must hold `approve_action` (responder or above) — a viewer seat cannot remediate
3. Confidence is read from the investigation named by `thread_id`, server-side, and must be `high`.
   A `confidence` value in the request body is ignored; it is not a credential.
4. Only reversible actions allowed (rollback, restart)
5. Multi-tenant: the namespace is pinned to the organization's own `KUBE_NAMESPACE`
6. Dry-run mode by default (COPILOT_AUTONOMY_DRYRUN=true)

**Observation Loop:**
After executing, watches metrics for regression. If burn rate increases,
automatically reverts to the previous state.

**Available Actions:**
- `rollback_deployment`: Roll back to previous revision
- `restart_deployment`: Restart deployment pods
""",
    dependencies=[Depends(require_perm("approve_action"))],
    responses={
        200: {
            "description": "Remediation executed",
            "content": {
                "application/json": {
                    "example": {
                        "status": "applied",
                        "action": "rollback_deployment",
                        "target": "api-server",
                        "dry_run": False,
                        "observation": {"burn_rate": 0.02, "verdict": "healthy"}
                    }
                }
            },
        },
        400: {
            "description": "Ineligible for remediation, or the body declared a `confidence` "
            "(the tier is read from the investigation named by `thread_id`)"
        },
        403: {"description": "Autonomous remediation disabled, or the caller's role may not remediate"},
        409: {"description": "The organization has no namespace this remediation could act in"},
        501: {"description": "The tenant's own cluster credential cannot be honoured by this process"},
    },
)
async def remediate(req: RemediateRequest) -> dict:
    """Execute an autonomous remediation.

    Applies a reversible fix, observes metrics, and auto-reverts on regression.

    Three things used to be missing, and all three are enforced here now. The
    route carried no RBAC at all, so a read-only `viewer` seat could fire a
    cluster write while merely *approving* one needed `approve_action`. The
    confidence gate read `req.confidence` — a request-body string — so the
    documented "confidence must be high" check was satisfied by typing the word;
    the tier is now read off the stored investigation and a body that still
    declares one is refused rather than silently ignored.
    And `namespace` was an unvalidated free string on a deployment whose
    Kubernetes client is process-wide, so one tenant could name another's
    namespace.

    Args:
        req: Action, target, the investigation to draw confidence from, and an
            optional namespace.

    Returns:
        dict: Remediation result, plus the confidence tier used and where it came
            from — so the caller can see which investigation authorised this.

    Raises:
        HTTPException: If autonomy is disabled, the role may not remediate, the
            action is ineligible, or the namespace cannot be resolved safely.
    """
    from app.autonomy import AutonomyController, RemediationPlan, is_eligible
    from app.mcp.servers.datadog import server as _dd
    from app.mcp.servers.kubernetes import server as _k8s

    settings = get_settings()
    if not settings.copilot_autonomy:
        raise HTTPException(403, "Autonomous remediation is disabled (set COPILOT_AUTONOMY=true).")

    # A caller that still declares its own confidence is refused outright rather
    # than having the field quietly dropped. Ignoring it would let an integration
    # keep sending confidence='high' and keep believing that string is what
    # authorises the write, and the one time the stored investigation disagrees it
    # would look like an unexplained refusal. The error says where the tier comes
    # from instead.
    if req.confidence is not None:
        raise HTTPException(
            400,
            f"`confidence` is no longer accepted in the request body (got {req.confidence!r}). "
            "The tier is read server-side from the stored investigation named by `thread_id`; "
            "resend without the field.",
        )

    tier, provenance = await _investigation_confidence(req.thread_id)
    ok, why = is_eligible(req.action, tier, settings.copilot_autonomy)
    if not ok:
        raise HTTPException(400, f"Ineligible for autonomous remediation: {why} ({provenance}).")

    namespace = _remediation_namespace(req.namespace, _k8s)

    action_fns = {
        "rollback_deployment": _k8s.rollback_deployment,
        "restart_deployment": _k8s.restart_deployment,
    }

    async def _execute(action: str, target: str) -> dict:
        return action_fns[action](target, namespace)

    async def _revert(action: str, target: str) -> dict:
        return action_fns[action](target, namespace)

    controller = AutonomyController(
        observe_fn=lambda svc: _dd.compute_burn_rate(svc),
        execute_fn=_execute,
        revert_fn=_revert,
        dry_run=settings.copilot_autonomy_dryrun,
        watch_s=settings.copilot_autonomy_watch_s,
    )
    # A rollback's compensating action is a restart-to-current; a restart has none.
    revert = "restart_deployment" if req.action == "rollback_deployment" else None
    plan = RemediationPlan(
        action=req.action, target=req.target, reason=req.reason,
        revert_action=revert, namespace=namespace,
    )
    result = await controller.remediate(plan)
    audit.record("autonomy.remediation", action=req.action, target=req.target,
                 namespace=namespace, thread_id=req.thread_id, confidence=tier,
                 status=result.get("status"), dry_run=settings.copilot_autonomy_dryrun)
    # Report the evidence the write rested on, not just that it happened: the
    # tier and the investigation it was read from are the audit trail a reviewer
    # needs to tell an authorised remediation from a guessed one.
    return {**result, "confidence": tier, "confidence_source": provenance}


def _remediation_namespace(requested: str, k8s_server: Any) -> str:
    """Resolve the namespace a remediation may act in, refusing rather than guessing.

    Single-tenant deployments keep whatever the caller asked for: the cluster and
    the operator are the same party. Multi-tenant is the case that was broken —
    `app/mcp/servers/kubernetes/server.py` binds its client to the *process*
    KUBE_CONFIG_PATH at import time, so this route always talks to the
    deployment's own cluster no matter whose key is on the request. With
    `namespace` a free string, tenant A could restart tenant B's workload.

    So on a multi-tenant deployment the namespace is pinned to the one the
    operator configured for that org, and an org with none configured is refused
    outright. If the org has its own KUBE_CONFIG_PATH we refuse too: this process
    cannot honour a per-tenant kubeconfig, and silently acting on the operator's
    cluster instead would be the wrong cluster reported as a success.
    """
    requested = (requested or "").strip()
    if not get_settings().copilot_multi_tenant:
        return requested

    org_kubeconfig = tenant_context.integration_secret("KUBE_CONFIG_PATH", "").strip()
    if org_kubeconfig and org_kubeconfig != getattr(k8s_server, "KUBE_CONFIG_PATH", ""):
        raise HTTPException(
            501,
            "This deployment runs one process-wide Kubernetes client, so it cannot execute a "
            "remediation against your organization's own kubeconfig. Remediation is unavailable "
            "for this organization rather than being run against the wrong cluster.",
        )
    allowed = tenant_context.integration_secret("KUBE_NAMESPACE", "").strip()
    if not allowed:
        raise HTTPException(
            409,
            "No Kubernetes namespace is configured for this organization, so there is no scope "
            "this remediation could safely act in. Set KUBE_NAMESPACE on the organization's "
            "integrations first.",
        )
    if requested and requested != allowed:
        raise HTTPException(
            403,
            f"This organization may only remediate in its own namespace '{allowed}'; "
            f"'{requested}' belongs to someone else.",
        )
    return allowed


async def _resume_triggered(thread_id: str, approved: bool) -> None:
    """Resume a paused investigation from a Slack button press.

    The button carries the internal session key, which on a multi-tenant
    deployment is tenant-namespaced (`<org>:<thread>`). Slack presents no
    credential we could map to an org, so the tenant is restored from that prefix
    before the graph runs again: the resumed turn executes the approved tool call,
    and doing that with the deployment's own tokens would act on the operator's
    systems instead of the org's.
    """
    if get_settings().copilot_multi_tenant and ":" in thread_id:
        org = thread_id.split(":", 1)[0]
        cfg = await _tenant_for_org(org)
        if cfg is None:
            log.error("refusing Slack resume of %s: org %s could not be resolved", thread_id, org)
            return
        await tenant_context.run_with_tenant(cfg, _resume_triggered_now(thread_id, approved))
        return
    await _resume_triggered_now(thread_id, approved)


async def _resume_triggered_now(thread_id: str, approved: bool) -> None:
    try:
        async with _running_turn(thread_id):
            session = await _get_session(thread_id)
            if not await session.pending_interrupt():
                return
            audit.record("approval.decided", thread=thread_id, approved=approved, via="slack")
            result = await session.resume(approved=approved, reason="decided via Slack")
            if result.status == "awaiting_approval":
                _AWAITING.add(thread_id)
                _pause_paged(thread_id, result.approval_request)
            else:
                _AWAITING.discard(thread_id)
        await _post_to_slack(thread_id, thread_id, result)
    except Exception:  # noqa: BLE001
        log.exception("slack-triggered resume failed (thread=%s)", thread_id)


# Webhook delivery idempotency: dedup a provider redelivery (identical signed body)
# within a TTL window so an incident isn't investigated — or resumed — twice.
_SEEN_DELIVERIES: dict[str, float] = {}
_DELIVERY_TTL = 600  # seconds


def _claim_delivery(raw: bytes) -> bool:
    """True if this webhook body is new (claims it); False if a duplicate within TTL."""
    now = time.time()
    digest = hashlib.sha256(raw).hexdigest()
    if len(_SEEN_DELIVERIES) > 5000:  # bound memory: drop expired entries
        for k in [k for k, t in _SEEN_DELIVERIES.items() if now - t > _DELIVERY_TTL]:
            del _SEEN_DELIVERIES[k]
    prev = _SEEN_DELIVERIES.get(digest)
    if prev is not None and now - prev < _DELIVERY_TTL:
        return False
    _SEEN_DELIVERIES[digest] = now
    return True


@app.post(
    "/webhooks/pagerduty",
    tags=["Webhooks"],
    summary="PagerDuty webhook receiver",
    description="Receive PagerDuty v3 webhook events. On incident.triggered, automatically "
    "starts an AI investigation. Requires valid HMAC signature (PAGERDUTY_WEBHOOK_SECRET).",
    responses={
        200: {
            "description": "Webhook processed",
            "content": {
                "application/json": {
                    "examples": {
                        "accepted": {"value": {"status": "accepted", "incident": "PD-12345"}},
                        "ignored": {"value": {"status": "ignored", "event": "incident.resolved"}},
                        "duplicate": {"value": {"status": "duplicate", "incident": "PD-12345"}}
                    }
                }
            },
        },
        400: {"description": "Invalid JSON body"},
        401: {"description": "Invalid webhook signature"},
    },
)
async def pagerduty_webhook(request: Request):
    """Receive PagerDuty webhook events.

    Verifies the HMAC signature and starts an investigation for triggered incidents.

    Returns:
        dict: Processing status and incident ID.
    """
    raw = await request.body()
    s = get_settings()
    sig = request.headers.get("x-pagerduty-signature", "")
    if not s.pagerduty_webhook_secret or not pd_webhook.verify_signature(
        s.pagerduty_webhook_secret, raw, sig
    ):
        raise HTTPException(401, "invalid or unconfigured PagerDuty webhook signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid JSON body") from exc
    incident = pd_webhook.parse_incident(payload)
    if not incident or not incident.get("id"):
        return {"status": "ignored"}
    if not incident["type"].endswith("triggered"):
        return {"status": "ignored", "event": incident["type"]}
    if not active_api_key():
        log.warning("PagerDuty incident %s received but no LLM key configured", incident["id"])
        return {"status": "accepted_no_llm", "incident": incident["id"]}
    if not _claim_delivery(raw):
        return {"status": "duplicate", "incident": incident["id"]}  # redelivery — already running
    await _enqueue_investigation(incident)
    return {"status": "accepted", "incident": incident["id"]}


async def _record_stripe_delivery(raw: bytes, signature: str) -> dict[str, str]:
    """Hand a verified Stripe delivery to the billing store as well as the tenant plan.

    Two different records are made from one payment. `orgs.plan` (below) is the
    quota bracket the app enforces; `billing_subscriptions` is the subscription
    itself — the Stripe ids, the period, the status and the catalog tier that was
    actually bought. Only the first was ever written: `BillingAPI.handle_webhook`
    is the sole entry point to the second and no route reached it, so after a real
    payment `GET /billing/subscription` answered null and change/cancel/reactivate
    raised "No subscription for org". The money moved and nothing recorded it.

    Returns what happened rather than raising. A store this receiver cannot reach
    must not turn a delivery Stripe successfully signed into a 500 — Stripe would
    retry the whole event, including the tenant-plan half that already landed. The
    caller reports this alongside the tenant-plan outcome so a delivery that moved
    only one of the two is visible instead of reading as a clean success.

    `BillingAPI.handle_webhook` re-verifies the signature through the SDK (which is
    why it takes the raw body and the header, not the parsed event) and raises
    BillingUnavailableError when Stripe is unconfigured.
    """
    from app.billing import runtime as billing_runtime
    from app.billing.api import BillingUnavailableError

    try:
        service = billing_runtime.get_stripe_service()
    except Exception as exc:  # noqa: BLE001 — a broken billing config is not a bad delivery
        log.exception("could not build the Stripe billing service for a webhook delivery")
        return {"status": "unavailable", "detail": f"{type(exc).__name__}: {exc}"}
    if service is None:
        # Not an error and not a success: STRIPE_API_KEY is unset (or the SDK is
        # missing), so this deployment has no billing store leg at all and
        # `billing_subscriptions` stays empty by configuration.
        return {
            "status": "not_configured",
            "detail": "no Stripe API key is configured, so billing_subscriptions was not written",
        }
    try:
        result = await billing_runtime.get_billing_api().handle_webhook(raw, signature)
    except BillingUnavailableError as exc:
        return {"status": "not_configured", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 — reported to the caller, never a 500 here
        log.exception("billing store rejected a verified Stripe delivery")
        return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
    return {
        "status": str(result.get("status") or "unknown"),
        "event_type": str(result.get("event_type") or ""),
    }


@app.post(
    "/webhooks/stripe",
    tags=["Webhooks"],
    summary="Stripe webhook receiver",
    description="Receive Stripe subscription events. Maps subscription created/updated/deleted "
    "to tenant plan changes and records the subscription itself in the billing store. "
    "Requires valid signature (STRIPE_WEBHOOK_SECRET). `status` is the tenant-plan outcome; "
    "`billing_store` reports the same delivery's effect on billing_subscriptions "
    "(`processed`, `ignored`, `not_configured`, `unavailable` or `failed`).",
    responses={
        200: {
            "description": "Webhook processed",
            "content": {
                "application/json": {
                    "examples": {
                        "applied": {"value": {
                            "status": "applied", "org_id": "org_123", "plan": "team",
                            "billing_store": {
                                "status": "processed",
                                "event_type": "customer.subscription.updated",
                            },
                        }},
                        "ignored": {"value": {
                            "status": "ignored", "type": "payment_intent.created",
                            "billing_store": {"status": "ignored", "event_type": "payment_intent.created"},
                        }},
                    }
                }
            },
        },
        400: {"description": "Malformed event"},
        401: {"description": "Invalid signature"},
    },
)
async def stripe_webhook(request: Request) -> dict:
    """Receive Stripe webhook events.

    Verifies the signature, then files the delivery in both places a payment has
    to land: the tenant's quota plan (`orgs.plan`, via `set_plan`) and the billing
    store's subscription row (via `_record_stripe_delivery`). They are two records
    with two readers, and the reply names both — `status` is the tenant-plan
    outcome the receiver has always reported, `billing_store` is the other half.
    A delivery that moved one and not the other now says so instead of answering
    a bare "applied".

    Returns:
        dict: Tenant-plan status, org ID, new plan, and the billing store's own
            outcome for the same delivery.
    """
    import json as _json
    import time as _time

    from app import billing
    s = get_settings()
    raw = await request.body()
    signature = request.headers.get("stripe-signature", "")
    if not billing.verify_stripe_signature(
        raw, signature, s.stripe_webhook_secret, now=int(_time.time()),
    ):
        raise HTTPException(401, "Invalid Stripe signature.")
    try:
        event = _json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Malformed Stripe event.") from None
    # Before the tenant-plan mapping, not after: the billing store handles event
    # types this mapping has nothing to say about (checkout.session.completed,
    # invoice.paid, customer.updated), and those all return early below. Every
    # signature-verified delivery reaches the store; the store decides what it
    # has a handler for.
    store_leg = await _record_stripe_delivery(raw, signature)
    try:
        mapping = billing.plan_from_stripe_event(event)
    except billing.UnknownPlan as exc:
        # 400, not 200-ignored: Stripe surfaces the failure in its dashboard and
        # retries, which is the only way anyone finds out that a price was
        # created with metadata the product does not recognise.
        raise HTTPException(400, f"Unknown plan in subscription metadata: {exc.plan!r}") from None
    if mapping is None:
        return {"status": "ignored", "type": event.get("type"), "billing_store": store_leg}
    org_id, plan = mapping
    if not await tenant_auth.get_store().set_plan(org_id, plan):
        # Report the failure so Stripe retries rather than recording a change
        # that never landed on any row.
        raise HTTPException(404, f"No such organization: {org_id}")
    audit.record("billing.plan_changed", org_id=org_id, plan=plan, via="stripe")
    return {"status": "applied", "org_id": org_id, "plan": plan, "billing_store": store_leg}


@app.post(
    "/webhooks/slack/interactions",
    tags=["Webhooks"],
    summary="Slack interactions receiver",
    description="Receive Slack interactive component callbacks (button clicks). Maps Approve/Reject "
    "buttons to investigation resume actions. Requires valid signature (SLACK_SIGNING_SECRET).",
    responses={
        200: {
            "description": "Interaction processed",
            "content": {
                "application/json": {
                    "example": {"text": "Approved - continuing the investigation."}
                }
            },
        },
        400: {"description": "Invalid payload"},
        401: {"description": "Invalid signature"},
    },
)
async def slack_interactions(request: Request):
    """Receive Slack interactive component callbacks.

    Processes approval button clicks and resumes paused investigations.

    Returns:
        dict: Slack response message.
    """
    raw = await request.body()
    s = get_settings()
    ts = request.headers.get("x-slack-request-timestamp", "")
    sig = request.headers.get("x-slack-signature", "")
    if not s.slack_signing_secret or not slack.verify_signature(
        s.slack_signing_secret, ts, raw, sig
    ):
        raise HTTPException(401, "invalid or unconfigured Slack signature")
    form = parse_qs(raw.decode())
    try:
        payload = json.loads(form.get("payload", ["{}"])[0])
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid interaction payload") from exc
    actions = payload.get("actions") or []
    if not actions:
        return {"text": "No action."}
    thread_id = actions[0].get("value") or ""
    approved = actions[0].get("action_id") == "approve"
    if not _claim_delivery(raw):
        return {"text": "Already processed."}  # duplicate Slack delivery
    if active_api_key() and thread_id:
        asyncio.create_task(_resume_triggered(thread_id, approved))
    return {"text": f"{'Approved' if approved else 'Rejected'} — continuing the investigation."}


# --------------------------------------------------------------------------- #
# GitHub Webhook — auto-triage issues, PRs, deployments, security alerts
# --------------------------------------------------------------------------- #
def _verify_github_signature(secret: str, payload: bytes, signature: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not secret:
        log.warning(
            "GitHub webhook rejected: no shared secret configured "
            "(set GITHUB_WEBHOOK_SECRET)"
        )
        return False
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _triage_event(
    result: dict[str, object], raw: bytes, *, source: str, key: str, reason: str,
    title: str, service: str,
) -> None:
    """Open a real investigation for a triage-worthy SCM/CI event.

    The GitHub and GitLab receivers used to "auto-triage" by writing a string
    into the response body (`result["auto_triage"] = "incident_detected"`) and
    returning. Nothing read that string — not the queue, not the incident store,
    not the UI — so a failed deployment or an incident-labelled issue produced
    exactly one thing: a 200 with a word in it.

    `auto_triage` is now only set when an investigation was actually queued;
    when it can't be, the response says why instead of implying triage happened.
    """
    if not active_api_key():
        result["auto_triage_skipped"] = (
            f"{reason}: no LLM provider key configured, nothing was investigated"
        )
        return
    if not _claim_delivery(raw):
        result["auto_triage_skipped"] = f"{reason}: duplicate delivery"
        return
    await _enqueue_investigation(
        {"id": key, "source": source, "title": title, "service": service}
    )
    result["auto_triage"] = reason
    result["investigation_thread"] = f"{source}-{key}"


@app.post(
    "/webhooks/github",
    tags=["Webhooks"],
    summary="GitHub webhook receiver",
    description="Receive GitHub webhook events. Handles push, pull_request, issues, "
    "deployment_status, and security_advisory events for auto-triage.",
    responses={
        200: {"description": "Event processed"},
        401: {"description": "Invalid or unconfigured webhook signature"},
    },
)
async def github_webhook(request: Request) -> dict[str, object]:
    """Receive GitHub webhook events for auto-triage.

    Supported events:
    - push: Track deployments
    - pull_request: Auto-review security implications
    - issues: Auto-triage bug reports
    - deployment_status: Track deployment health
    - security_advisory: Alert on vulnerabilities
    """
    raw = await request.body()
    s = get_settings()
    sig = request.headers.get("x-hub-signature-256", "")

    # Fail closed, unconditionally. This used to be `if secret and not verified`,
    # so with no secret configured — the default — the receiver accepted anything
    # anyone posted at it, which is also what decides whether LLM spend is
    # triggered. The unconfigured case is split out from the bad-signature case
    # only so the 401 names the setting the operator has to fill in;
    # `_verify_github_signature` rejects (and logs) it either way.
    if not s.github_webhook_secret:
        raise HTTPException(
            401, "GITHUB_WEBHOOK_SECRET is not configured; refusing unverified GitHub delivery"
        )
    if not _verify_github_signature(s.github_webhook_secret, raw, sig):
        raise HTTPException(401, "invalid GitHub webhook signature")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid JSON payload") from exc

    event_type = request.headers.get("x-github-event", "unknown")
    delivery_id = request.headers.get("x-github-delivery", "")

    log.info("GitHub webhook: event=%s delivery=%s", event_type, delivery_id)

    result: dict[str, object] = {
        "event": event_type,
        "delivery_id": delivery_id,
        "processed": True,
    }
    repo_name = (payload.get("repository") or {}).get("full_name") or "unknown"

    if event_type == "push":
        result["action"] = "deployment_tracked"
        result["ref"] = payload.get("ref", "")
        result["commits"] = len(payload.get("commits") or [])
        result["repository"] = repo_name

    elif event_type == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request") or {}
        result["action"] = action
        result["pr_number"] = pr.get("number")
        result["pr_title"] = pr.get("title", "")
        result["pr_state"] = pr.get("state", "")

        # A big PR is worth flagging, but it is not an incident — it does not
        # open an investigation, so it does not claim to have been triaged.
        if action in ("opened", "synchronize") and (pr.get("changed_files") or 0) > 50:
            result["review_flags"] = ["large_pr"]

    elif event_type == "issues":
        action = payload.get("action", "")
        issue = payload.get("issue") or {}
        result["action"] = action
        result["issue_number"] = issue.get("number")
        result["issue_title"] = issue.get("title", "")

        if action == "opened":
            labels = [
                str((lbl or {}).get("name", "")).lower() for lbl in (issue.get("labels") or [])
            ]
            if "bug" in labels or "incident" in labels:
                await _triage_event(
                    result, raw,
                    source="gh-issue",
                    key=str(issue.get("number") or delivery_id or "unknown"),
                    reason="incident_detected",
                    title=f"GitHub issue #{issue.get('number')}: {issue.get('title', '')}".strip(),
                    service=str(repo_name),
                )

    elif event_type == "deployment_status":
        deployment = payload.get("deployment") or {}
        result["deployment_id"] = deployment.get("id")
        result["environment"] = deployment.get("environment", "")
        result["state"] = (payload.get("deployment_status") or {}).get("state", "")

        if result["state"] == "failure":
            await _triage_event(
                result, raw,
                source="gh-deploy",
                key=str(deployment.get("id") or delivery_id or "unknown"),
                reason="deployment_failure_detected",
                title=(
                    f"Deployment failed in {deployment.get('environment') or 'unknown'} "
                    f"({repo_name or 'unknown repo'})"
                ),
                service=str(repo_name),
            )

    elif event_type == "security_advisory":
        advisory = payload.get("security_advisory") or {}
        result["severity"] = advisory.get("severity", "")
        result["summary"] = advisory.get("summary", "")
        await _triage_event(
            result, raw,
            source="gh-advisory",
            key=str(advisory.get("ghsa_id") or delivery_id or "unknown"),
            reason="security_alert",
            title=f"GitHub security advisory ({advisory.get('severity', 'unknown')}): "
                  f"{advisory.get('summary', '')}".strip(),
            service=str(repo_name),
        )

    return result


# --------------------------------------------------------------------------- #
# GitLab Webhook — auto-triage merge requests, issues, pipelines
# --------------------------------------------------------------------------- #
def _verify_gitlab_token(secret: str, token: str) -> bool:
    """Verify the GitLab webhook secret token (constant-time).

    Fails closed when no secret is configured: this endpoint is unauthenticated
    apart from the token, so "no secret, allow" let anyone on the internet inject
    fake pipeline/deployment events and drive auto-triage. Mirrors
    ``_verify_github_signature`` and the PagerDuty webhook, which already reject
    an unconfigured secret.
    """
    if not secret:
        log.warning(
            "GitLab webhook rejected: no shared secret configured "
            "(set GITLAB_WEBHOOK_SECRET)"
        )
        return False
    if not token:
        return False
    return hmac.compare_digest(secret, token)


@app.post(
    "/webhooks/gitlab",
    tags=["Webhooks"],
    summary="GitLab webhook receiver",
    description="Receive GitLab webhook events. Handles push, merge_request, issue, "
    "pipeline, and deployment events for auto-triage.",
    responses={
        200: {"description": "Event processed"},
        401: {"description": "Invalid or unconfigured webhook token"},
    },
)
async def gitlab_webhook(request: Request) -> dict[str, object]:
    """Receive GitLab webhook events for auto-triage.

    Supported events:
    - Push Hook: Track deployments
    - Merge Request Hook: Auto-review
    - Issue Hook: Auto-triage bug reports
    - Pipeline Hook: Track CI/CD status
    - Deployment Hook: Track deployment health
    """
    raw = await request.body()
    s = get_settings()
    token = request.headers.get("x-gitlab-token", "")

    # Fail closed, as for GitHub: an unset GITLAB_WEBHOOK_SECRET used to skip the
    # check entirely rather than refuse the delivery. The unconfigured case is
    # split out only so the 401 names the setting; `_verify_gitlab_token`
    # rejects (and logs) it either way.
    if not s.gitlab_webhook_secret:
        raise HTTPException(
            401, "GITLAB_WEBHOOK_SECRET is not configured; refusing unverified GitLab delivery"
        )
    if not _verify_gitlab_token(s.gitlab_webhook_secret, token):
        raise HTTPException(401, "invalid GitLab webhook token")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "invalid JSON payload") from exc

    event_type = payload.get("object_kind", request.headers.get("x-gitlab-event", "unknown"))

    log.info("GitLab webhook: event=%s", event_type)

    result: dict[str, object] = {
        "event": event_type,
        "processed": True,
    }
    project = (payload.get("project") or {}).get("path_with_namespace") or "unknown"
    # `or {}`, not `.get(key, {})`: GitLab sends `"object_attributes": null` on
    # some event kinds, and the default only applies when the key is absent —
    # every one of these branches was a 500 on a real null.
    attrs = payload.get("object_attributes") or {}

    if event_type == "push":
        result["ref"] = payload.get("ref", "")
        result["commits"] = len(payload.get("commits") or [])
        result["project"] = project

    elif event_type == "merge_request":
        result["action"] = attrs.get("action", "")
        result["mr_iid"] = attrs.get("iid")
        result["mr_title"] = attrs.get("title", "")
        result["mr_state"] = attrs.get("state", "")

        # An opened MR is not an incident; flag it, don't claim to have triaged it.
        if attrs.get("action") == "open" and payload.get("changes"):
            result["review_flags"] = ["mr_opened"]

    elif event_type == "issue":
        result["action"] = attrs.get("action", "")
        result["issue_iid"] = attrs.get("iid")
        result["issue_title"] = attrs.get("title", "")

        if attrs.get("action") == "open":
            labels = [
                str((lbl or {}).get("title", "")).lower() for lbl in (payload.get("labels") or [])
            ]
            if "bug" in labels or "incident" in labels:
                await _triage_event(
                    result, raw,
                    source="gl-issue",
                    key=str(attrs.get("iid") or attrs.get("id") or "unknown"),
                    reason="incident_detected",
                    title=f"GitLab issue #{attrs.get('iid')}: {attrs.get('title', '')}".strip(),
                    service=str(project),
                )

    elif event_type == "pipeline":
        result["pipeline_id"] = attrs.get("id")
        result["status"] = attrs.get("status", "")
        result["ref"] = attrs.get("ref", "")

        if attrs.get("status") == "failed":
            await _triage_event(
                result, raw,
                source="gl-pipeline",
                key=str(attrs.get("id") or "unknown"),
                reason="pipeline_failure_detected",
                title=(
                    f"GitLab pipeline {attrs.get('id')} failed on "
                    f"{attrs.get('ref') or 'unknown ref'} ({project})"
                ),
                service=str(project),
            )

    elif event_type == "deployment":
        result["deployment_id"] = payload.get("deployment_id")
        result["environment"] = payload.get("environment", "")
        result["status"] = payload.get("status", "")

        if payload.get("status") == "failed":
            await _triage_event(
                result, raw,
                source="gl-deploy",
                key=str(payload.get("deployment_id") or "unknown"),
                reason="deployment_failure_detected",
                title=(
                    f"GitLab deployment to {payload.get('environment') or 'unknown'} "
                    f"failed ({project})"
                ),
                service=str(project),
            )

    return result


# --------------------------------------------------------------------------- #
# Waitlist & Early Access — capture leads and track conversions
# --------------------------------------------------------------------------- #
class WaitlistSignupRequest(BaseModel):
    """Waitlist signup request."""
    email: str
    company: str = ""
    role: str = ""
    team_size: str = ""
    use_case: str = ""
    source: str = "landing_page"
    # The referral code of whoever sent this person here — `REF1A2B3C4D`, as
    # printed in that person's share link. NOT the code issued to this signup,
    # which is derived from their own email and returned in the response.
    referral_code: str = ""
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""


@app.post(
    "/waitlist",
    tags=["Waitlist"],
    summary="Join the waitlist",
    description="Sign up for early access to OmniPotent. "
    "Returns your position and referral code. Open to anonymous visitors — a "
    "waitlist that demanded an API key could only be joined by people who "
    "already had an account — but per-IP rate limited like any other POST.",
    responses={
        200: {
            "description": "Signup successful",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Successfully joined waitlist",
                        "position": 42,
                        "referral_code": "REF1A2B3C4D",
                    }
                }
            },
        },
        400: {"description": "Invalid email or already registered"},
    },
)
async def waitlist_signup(req: WaitlistSignupRequest, request: Request):
    """Add email to the waitlist."""
    from app.waitlist import add_to_waitlist, SignupSource

    source_map = {
        "landing_page": SignupSource.LANDING_PAGE,
        "product_hunt": SignupSource.PRODUCT_HUNT,
        "hacker_news": SignupSource.HACKER_NEWS,
        "twitter": SignupSource.TWITTER,
        "linkedin": SignupSource.LINKEDIN,
        "github": SignupSource.GITHUB,
        "referral": SignupSource.REFERRAL,
        "direct": SignupSource.DIRECT,
    }
    source = source_map.get(req.source.lower(), SignupSource.OTHER)

    client_ip = ""
    if request.client:
        client_ip = request.client.host

    entry, message = await add_to_waitlist(
        email=req.email,
        source=source,
        company=req.company,
        role=req.role,
        team_size=req.team_size,
        use_case=req.use_case,
        referred_by=req.referral_code,
        utm_source=req.utm_source,
        utm_medium=req.utm_medium,
        utm_campaign=req.utm_campaign,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent", ""),
    )

    if entry is None:
        raise HTTPException(400, message)

    audit.record(
        "waitlist.signup",
        source=source.value,
        company=req.company or "(none)",
    )

    return {
        "success": True,
        "message": message,
        "position": entry.position,
        "referral_code": entry.referral_code,
        "tier": entry.tier.value,
    }


@app.get(
    "/waitlist/position",
    tags=["Waitlist"],
    summary="Check waitlist position",
    description="Check your position on the waitlist using your email.",
)
async def waitlist_position(email: str):
    """Get waitlist position for an email."""
    from app.waitlist import WaitlistManager

    manager = WaitlistManager()
    result = await manager.get_position(email)

    if not result.get("found"):
        raise HTTPException(404, "Email not found in waitlist")

    return result


@app.get(
    "/waitlist/stats",
    tags=["Waitlist"],
    summary="Get waitlist statistics (operator only)",
    description="Aggregated waitlist statistics. This is a back-office endpoint: it "
    "requires COPILOT_OPERATOR_TOKEN, not a tenant API key. It was behind "
    "`require_auth`, which on a multi-tenant deployment means any tenant key — so "
    "every customer could read the deployment's lead funnel.",
    dependencies=[Depends(_operator_guard)],
)
async def waitlist_stats():
    """Get waitlist statistics."""
    from app.waitlist import get_waitlist_stats

    return await get_waitlist_stats()


# --------------------------------------------------------------------------- #
# Customer & Trial Management — track conversions and engagement
#
# Operator-only, every one of them. This is the deployment's own CRM: contact
# details, health scores and MRR for its customers. It sat behind `require_auth`,
# which on a multi-tenant deployment is satisfied by any tenant API key, so the
# cheapest self-serve signup could list every other customer and their revenue.
# --------------------------------------------------------------------------- #
class CustomerCreateRequest(BaseModel):
    """Customer creation request."""
    email: str
    company: str
    name: str = ""
    role: str = ""
    team_size: str = ""
    industry: str = ""
    use_case: str = ""
    start_trial: bool = True
    trial_days: int = 14


@app.post(
    "/customers",
    tags=["Customers"],
    summary="Create a customer (operator only)",
    description="Create a new customer and optionally start a trial. Requires "
    "COPILOT_OPERATOR_TOKEN.",
    dependencies=[Depends(_operator_guard)],
    responses={
        200: {
            "description": "Customer created",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "customer": {"id": "cust_abc123", "company": "Acme Inc"},
                        "trial": {"id": "trial_xyz789", "days_remaining": 14},
                    }
                }
            },
        },
    },
)
async def create_customer_endpoint(req: CustomerCreateRequest):
    """Create a new customer."""
    from app.customers import CustomerManager

    manager = CustomerManager()
    result = await manager.create(
        email=req.email,
        company=req.company,
        name=req.name,
        role=req.role,
        team_size=req.team_size,
        industry=req.industry,
        use_case=req.use_case,
        start_trial=req.start_trial,
        trial_days=req.trial_days,
    )

    if not result.get("success"):
        raise HTTPException(400, result.get("error", "Failed to create customer"))

    audit.record(
        "customer.created",
        company=req.company,
        trial=req.start_trial,
    )

    return result


@app.get(
    "/customers/stats",
    tags=["Customers"],
    summary="Get customer statistics (operator only)",
    description="Get customer and trial statistics for the dashboard. Requires "
    "COPILOT_OPERATOR_TOKEN.",
    dependencies=[Depends(_operator_guard)],
)
async def customer_stats():
    """Get customer statistics."""
    from app.customers import get_customer_stats

    return await get_customer_stats()


@app.get(
    "/customers/dashboard",
    tags=["Customers"],
    summary="Get customer dashboard (operator only)",
    description="Get full customer dashboard including stats, trials, and recent "
    "customers. Requires COPILOT_OPERATOR_TOKEN.",
    dependencies=[Depends(_operator_guard)],
)
async def customer_dashboard():
    """Get customer dashboard data."""
    from app.customers import CustomerManager

    manager = CustomerManager()
    return await manager.get_dashboard()


@app.get(
    "/customers/trials/active",
    tags=["Customers"],
    summary="Get active trials (operator only)",
    description="Get list of all active trials. Requires COPILOT_OPERATOR_TOKEN.",
    dependencies=[Depends(_operator_guard)],
)
async def active_trials():
    """Get active trials."""
    from app.customers import get_active_trials

    trials = await get_active_trials()
    return {
        "count": len(trials),
        "trials": [t.to_dict() for t in trials],
    }


class RecordUsageRequest(BaseModel):
    """Usage recording request."""
    customer_id: str
    event_type: str
    investigation_id: str = ""
    tokens_used: int = 0
    duration_seconds: float = 0.0


@app.post(
    "/customers/usage",
    tags=["Customers"],
    summary="Record customer usage (operator only)",
    description="Record a usage event for a customer (investigation, etc). Requires "
    "COPILOT_OPERATOR_TOKEN.",
    dependencies=[Depends(_operator_guard)],
)
async def record_customer_usage(req: RecordUsageRequest):
    """Record customer usage."""
    from app.customers import record_usage

    success = await record_usage(
        customer_id=req.customer_id,
        event_type=req.event_type,
        investigation_id=req.investigation_id,
        tokens_used=req.tokens_used,
        duration_seconds=req.duration_seconds,
    )

    if not success:
        raise HTTPException(404, "Customer not found")

    return {"success": True}


class ConvertTrialRequest(BaseModel):
    """Trial conversion request."""
    customer_id: str
    tier: str = "team"
    annual: bool = False
    stripe_customer_id: str = ""
    # Only read for the Enterprise tier, which is quoted per contract and so has
    # no catalog price to derive MRR from. Every other tier ignores it: this
    # field used to be `mrr_cents`, applied to every tier, which made this
    # endpoint an API for writing an arbitrary number into the revenue total.
    contracted_mrr_cents: int | None = None


@app.post(
    "/customers/convert",
    tags=["Customers"],
    summary="Convert trial to paid (operator only)",
    description="Convert a trial customer to a paid subscription. The recorded MRR "
    "comes from the pricing catalog entry for `tier`, not from the request body; "
    "`contracted_mrr_cents` is read only for `enterprise`, which has no list price. "
    "Requires COPILOT_OPERATOR_TOKEN. 400 for a tier the catalog does not sell.",
    dependencies=[Depends(_operator_guard)],
)
async def convert_trial_endpoint(req: ConvertTrialRequest):
    """Convert a trial to paid subscription."""
    from app.customers import convert_trial, CustomerTier

    try:
        tier = CustomerTier(req.tier.strip().lower())
    except ValueError:
        # Silently coercing an unknown tier to Starter is how a Growth
        # conversion could be recorded as a free plan.
        valid = ", ".join(t.value for t in CustomerTier)
        raise HTTPException(400, f"Unknown tier {req.tier!r}; expected one of {valid}.") from None

    success, message = await convert_trial(
        customer_id=req.customer_id,
        tier=tier,
        annual=req.annual,
        stripe_customer_id=req.stripe_customer_id,
        contracted_mrr_cents=req.contracted_mrr_cents,
    )

    if not success:
        raise HTTPException(400, message)

    audit.record(
        "customer.converted",
        customer_id=req.customer_id,
        tier=tier.value,
        annual=req.annual,
    )

    return {"success": True, "message": message}


# --------------------------------------------------------------------------- #
# Investigation analytics — MTTR, ROI and outcome mix over recorded history.
# --------------------------------------------------------------------------- #
# The read side of the `investigations` table that /chat, /chat/stream, /approve
# and the alert-triggered path all write to. Scoped to the calling tenant; in
# single-tenant mode everything is recorded under one org.
#
# Under /api like every other feature router: `dashboard` is a client-side route
# in App.tsx and the SPA mount below refuses to serve any top-level segment the
# API has claimed, so a bare /dashboard/* route here would 404 the page a
# logged-in user lands on after login.
@app.get(
    "/api/dashboard/metrics",
    tags=["Dashboard"],
    summary="Investigation analytics for the current organization",
    description="""
MTTR, ROI, usage and outcome statistics computed from recorded investigations.

**On empty results:** a deployment that has run no investigations gets zeroes,
not a sample. Two fields need reading carefully:

- `mttr.improvement_percent` is `null` and `mttr.comparison_available` is `false`
  until there is a *previous* period of real investigations to compare against.
  No industry-baseline default is substituted.
- `roi.*` is derived from measured time-saved, which the pipeline does not yet
  record, so it reports 0 rather than an estimate.

`health_score` is out of `health_score_max`, which drops to 75 when the MTTR
trend cannot be judged.
""",
    dependencies=[Depends(require_perm("view"))],
)
async def dashboard_metrics(period_days: int = 30) -> dict:
    """Investigation analytics for the calling organization."""
    if not 1 <= period_days <= 365:
        raise HTTPException(422, "period_days must be between 1 and 365")

    from app.dashboard.metrics import get_dashboard_metrics

    metrics = await get_dashboard_metrics(
        org_id=_history_org_id(), period_days=period_days
    )
    return metrics.to_dict()


@app.get(
    "/api/dashboard/trends",
    tags=["Dashboard"],
    summary="Trend analysis over recorded investigations",
    description="""
Direction, anomaly and forecast per metric, computed by bucketing recorded
investigations into days over the requested period.

`direction` is stated in terms of health, not of the number: for MTTR and
duration a falling value is `improving`. Each trend reports the `polarity` it was
judged under so that reading is auditable.
""",
    dependencies=[Depends(require_perm("view"))],
)
async def dashboard_trends(period_days: int = 30) -> dict:
    """Daily trends for investigation volume, duration and token spend."""
    if not 1 <= period_days <= 365:
        raise HTTPException(422, "period_days must be between 1 and 365")

    from collections import defaultdict

    from app.dashboard.metrics import get_investigation_series
    from app.dashboard.trends import MetricPolarity, calculate_trends, generate_trend_report

    series = await get_investigation_series(
        org_id=_history_org_id(), period_days=period_days
    )

    daily_count: dict[Any, int] = defaultdict(int)
    daily_duration: dict[Any, list[float]] = defaultdict(list)
    daily_tokens: dict[Any, int] = defaultdict(int)
    for started_at, duration_seconds, tokens in series:
        day = started_at.replace(hour=0, minute=0, second=0, microsecond=0)
        daily_count[day] += 1
        daily_duration[day].append(duration_seconds / 60)
        daily_tokens[day] += tokens

    metrics = {
        "investigations_per_day": sorted(
            (d, float(n)) for d, n in daily_count.items()
        ),
        "mttr_minutes": sorted(
            (d, sum(v) / len(v)) for d, v in daily_duration.items()
        ),
        "tokens_per_day": sorted((d, float(n)) for d, n in daily_tokens.items()),
    }

    trends = calculate_trends(
        metrics,
        polarities={
            # Stated rather than inferred from the name: more investigations is
            # not self-evidently good or bad (it tracks incident volume as much as
            # adoption), and token spend rising is bad even though "tokens" reads
            # neutral.
            "investigations_per_day": MetricPolarity.HIGHER_IS_BETTER,
            "mttr_minutes": MetricPolarity.LOWER_IS_BETTER,
            "tokens_per_day": MetricPolarity.LOWER_IS_BETTER,
        },
    )
    return generate_trend_report(trends)


# Include the versioned /v1 surface: chat, config, admin and webhook mirrors of
# the root routes, plus the decision engine, the policy engine and the digital
# twin, which exist ONLY under /v1 and have no root equivalent at all.
#
# The package was written, given its own RBAC dependencies and never included,
# so `app.api.v1` had exactly one reference in the whole tree — its own
# `__init__`. `app.openapi()` reported 303 paths, none of them starting with
# /v1: the 20 digital-twin endpoints and the 29 decision/policy endpoints were
# unreachable, and every `Depends(require_perm(...))` and `Depends(quota_gate)`
# in v1/chat.py, v1/config.py and v1/admin.py was dead code guarding a router
# nothing served. A `GET /v1/health` still answered 200 in a TestClient, which
# is what hid this: the SPA catch-all below serves index.html for unknown paths,
# so the reply was HTML from the static mount, not the route.
#
# Included here rather than up with the other routers at the top of this file
# because v1/chat.py, v1/config.py and v1/admin.py delegate to the root handlers
# and import them from this module at import time. Those handlers are defined
# above; importing the package any earlier would resolve against a half-built
# `app.api.main`. It also has to land BEFORE the SPA mount, so that "v1" is in
# `_API_SEGMENTS` and a mistyped /v1 path keeps answering 404 JSON.
#
# No prefix argument: the router carries prefix="/v1" itself, and auth comes
# from the app-level Depends(require_auth), same as every other included router.
from app.api.v1 import router as v1_router  # noqa: E402

app.include_router(v1_router)


# --------------------------------------------------------------------------- #
# Static SPA — serve the built frontend so the whole app is one deployable.
# --------------------------------------------------------------------------- #
# Mounted LAST: the explicit API routes above are matched first; everything else
# (/, /assets/*, etc.) falls through to here. StaticFiles is a sub-application,
# so it bypasses the require_auth dependency — the browser loads the HTML/JS
# freely, then sends the bearer token (if any) on the API calls. In dev (no
# build) the dist dir is absent and this mount is simply skipped.
_FRONTEND_DIST = ROOT / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():

    def _served_paths(routes: Iterable[Any], prefix: str = "") -> Iterator[str]:
        """Every path the app serves, including the ones behind include_router.

        FastAPI 0.137 stopped flattening `include_router` into `app.routes` and
        now leaves one `_IncludedRouter` per call instead — an object with no
        `.path`. Reading only the top level therefore saw 85 of the 347 paths
        this app actually serves, and `_API_SEGMENTS` below silently lost
        `oncall`, `billing`, `teams`, `ws` and every other included namespace.
        The SPA fallback then answered `200 text/html` for a mistyped call under
        any of them, which is exactly the failure this whole block exists to
        prevent. tests/test_route_smoke.py hit the same wall and documents it;
        the production side was never updated.

        Recursing through `original_router` rather than asking for
        `effective_candidates()` on purpose: the latter is HTTP-only and returns
        a bare "" for the WebSocket router, so /ws/* would still have fallen
        through to index.html.
        """
        for route in routes:
            path = getattr(route, "path", None)
            if path is not None:
                yield prefix + path
                continue
            included = getattr(route, "original_router", None)
            if included is not None:
                context = getattr(route, "include_context", None)
                yield from _served_paths(
                    included.routes, prefix + (getattr(context, "prefix", "") or "")
                )

    # First path segment of every route registered above. An unknown path under
    # one of these is a mistyped API call, not a client-side route, and should
    # keep answering 404 JSON rather than 200 HTML — otherwise a typo'd endpoint
    # looks like a success to any client that only checks the status code.
    _API_SEGMENTS = {
        p.split("/")[1]
        for p in _served_paths(app.routes)
        if p.count("/") >= 1 and len(p.split("/")) > 1 and p.split("/")[1]
    }

    # Client-side routes that live under an API segment anyway. Only one: the
    # OAuth provider redirects the *browser* here, and it must render the SPA.
    # There is no backend route at this path — `auth` is a segment because of
    # /auth/providers, /auth/{provider}/login and friends.
    _SPA_PATHS = {"auth/callback"}

    def _a_real_route_owns(scope, path: str) -> bool:
        """Does any route other than this catch-all mount answer this path?

        Starlette tries routes in order, and a Mount at "/" matches everything —
        so a request whose *path* matches a real route but whose *method* does
        not never reaches that route to collect its 405. It lands in this mount
        instead. `POST /healthz` is the case in point: the route exists, the verb
        is wrong, and 405 is the honest answer. Asking the routes directly is the
        only way to tell that apart from a path nothing serves at all, which must
        be 404.
        """
        probe = dict(scope)
        probe["path"] = "/" + path
        probe["root_path"] = ""
        for route in app.routes:
            if getattr(route, "name", None) == "spa":
                continue
            if route.matches(probe)[0] is not Match.NONE:
                return True
        return False

    class _SPAStaticFiles(StaticFiles):
        """StaticFiles that falls back to index.html for unknown paths.

        `html=True` only serves index.html for *directory* paths, so a deep link
        like /dashboard, /login or /settings/billing returned a 404 JSON body.
        Stripe's default success_url is `{frontend_url}/settings/billing?...`,
        so a user finishing checkout landed on a hard 404. Handing those to
        index.html lets the client-side router take over, which is what every
        SPA deployment needs.
        """

        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 405:
                    # StaticFiles answers 405 for any verb other than GET/HEAD
                    # *before* it looks for the file, so this mount — which is
                    # the catch-all, not a resource — claimed that every
                    # mistyped path exists. `POST /api/knowledge-graph/graphql`
                    # came back 405 with the schema not mounted at all, and any
                    # caller that distinguishes "gone" from "wrong verb" by
                    # checking for 404 read that as reachable. 405 stays only
                    # when the file is really there.
                    _full, stat_result = await anyio.to_thread.run_sync(
                        self.lookup_path, path
                    )
                    if stat_result is not None or _a_real_route_owns(scope, path):
                        # Either the file is really there or a real route owns
                        # the path — the resource exists and the verb is what is
                        # wrong, so 405 is the truth. Answering 404 here would be
                        # the same lie in reverse.
                        raise
                    raise StarletteHTTPException(status_code=404) from None
                if exc.status_code != 404:
                    raise
                # Real missing assets should still 404 — falling back for them
                # would hand JS/CSS requests an HTML body and produce a
                # confusing MIME-type error in the console instead.
                if "." in path.rsplit("/", 1)[-1]:
                    raise
                if path.split("/")[0] in _API_SEGMENTS and path not in _SPA_PATHS:
                    raise
                return await super().get_response("index.html", scope)

    app.mount("/", _SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True), name="spa")
    log.info("serving built frontend from %s (SPA fallback enabled)", _FRONTEND_DIST)
