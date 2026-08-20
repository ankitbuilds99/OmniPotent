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
from typing import Any
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import (
    audit,
    feedback,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Multi-tenant: ensure the tenant store schema exists before serving.
    if get_settings().copilot_multi_tenant:
        await tenant_auth.get_store().setup()
        log.info("multi-tenant mode: tenant store ready (%s)", get_settings().copilot_tenant_db)
        if get_settings().copilot_tenant_db.startswith(("postgres://", "postgresql://")) is False:
            log.warning("multi-tenant on SQLite is app-level isolated, not RLS-hard-isolated; "
                        "use Postgres + RLS for production tenant isolation")
    # ORM schema (customers, trials, usage events, on-call). Had no caller at all
    # before: init_db() existed and was never invoked, so on the default SQLite
    # backend every table in app/database/models.py was unreachable. On Postgres
    # this only verifies connectivity — Alembic owns the schema there.
    from app.database.engine import close_db, ensure_schema
    await ensure_schema()
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
    for _task in (slo_task, job_task):
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


async def require_auth(request: Request) -> None:
    """Bearer-token gate for every route except probes and CORS preflight.

    When COPILOT_API_TOKEN is unset the API is open (local-dev default); set it
    in production and the frontend must send `Authorization: Bearer <token>`.
    (The static SPA mount is a sub-app, so it bypasses this dependency entirely.)
    """
    if request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
        return
    # Auth routes need to be open for OAuth login/callback flows.
    if any(request.url.path.startswith(p) for p in _AUTH_PATH_PREFIXES):
        return
    # Webhooks authenticate via their own provider signatures (HMAC), not the
    # shared bearer token — PagerDuty/Slack don't send it.
    if request.url.path.startswith("/webhooks/"):
        return
    # SCIM provisioning authenticates with its own IdP-issued bearer token, checked
    # in the handler (not the shared app token).
    if request.url.path.startswith("/scim/"):
        return
    # Self-serve signup is how a new tenant GETS its first credential, so it can't
    # require one. It stays out of _OPEN_PATHS so the IP rate-limiter still applies
    # (abuse guard); the handler itself enforces multi-tenant + the enabled flag.
    if request.url.path == "/signup":
        return

    s = get_settings()
    # Multi-tenant: resolve the per-tenant API key (dcp_…) into the request's
    # TenantConfig + actor on the contextvar. Task-isolated, so it's scoped to
    # this request and read by runtime.py / the agent for the rest of the call.
    if s.copilot_multi_tenant:
        provided = request.headers.get("authorization", "")
        token = provided[7:].strip() if provided.startswith("Bearer ") else provided.strip()
        # A `dcp_…` value is a tenant API key; anything with two dots is a JWT
        # (Supabase/SSO login). Both resolve to the same TenantConfig + actor.
        if token.startswith("dcp_"):
            resolved = await tenant_auth.resolve(token)
        elif token.count(".") == 2:
            resolved = await tenant_auth.resolve_jwt(token)
        else:
            resolved = None
        if resolved is None:
            raise HTTPException(401, "Invalid or revoked credential.")
        cfg, actor = resolved
        tenant_context.set_tenant(cfg)
        tenant_context.set_actor(actor)
        return

    expected = s.copilot_api_token
    if not expected:
        return  # auth disabled (dev)
    provided = request.headers.get("authorization", "")
    # Constant-time compare so the token can't be recovered via response timing.
    if not hmac.compare_digest(provided, f"Bearer {expected}"):
        raise HTTPException(401, "Missing or invalid API token.")


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


def _record_slo(result: Any, latency_s: float) -> None:
    """Record a completed investigation to the SLO metrics (best-effort)."""
    try:
        from app.slo_metrics import metrics
        report = result.report or {}
        abstained = bool(report.get("abstained"))
        # "success" = produced a report and didn't error (abstention still counts as
        # a healthy, honest outcome).
        success = bool(report) or bool(result.final_text)
        metrics().record(latency_s, result.tokens_used, success, abstained)
    except Exception:  # noqa: BLE001 — metrics must never break a request
        log.debug("SLO metric record failed", exc_info=True)


# Org id used for investigation history when running single-tenant. Unlike
# _current_org_id() this never raises: history is recorded on every deployment,
# not just multi-tenant ones. Shared with the planning and coordinator stores,
# which file their rows under the same key.
_DEFAULT_HISTORY_ORG = tenant_context.DEFAULT_STORAGE_ORG


def _history_org_id() -> str:
    return tenant_context.storage_org_id()


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


async def _record_history(thread_id: str, result: Any, latency_s: float) -> None:
    """Record a completed investigation to the dashboard history (best-effort).

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


async def _record_history_from_stream(thread_id: str, ev: dict, latency_s: float) -> None:
    """`_record_history` for the SSE paths, whose completion is a dict event.

    The streaming endpoints ran investigations that never reached the dashboard
    or the SLO metrics at all: only the non-streaming /chat had a recording hook,
    so anything the UI ran through /chat/stream was invisible in both.
    """
    await _record_history(
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
    plan quota (402), or when the FLEET-WIDE token cap is reached (429). No-op in
    single-tenant mode / when the global cap is off."""
    if await metering.over_quota():
        raise HTTPException(
            402, "Monthly investigation quota reached for your plan — upgrade to continue."
        )
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

# Body-size + rate-limit guard. Added FIRST so it ends up INSIDE the CORS
# middleware (add_middleware is LIFO) — that way a 413/429 still gets CORS
# headers and the browser can read it.
app.add_middleware(LimitsMiddleware)

# GZip compression for responses > 1KB (reduces bandwidth ~40%)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Allow the React dev server plus any origins configured via CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
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
app.include_router(knowledge_graph_router, prefix="/api")
include_graphql_router(knowledge_graph_router)  # Add GraphQL if strawberry is installed

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
    confidence: str = "low"      # investigation confidence; must be "high" to act
    reason: str = ""
    namespace: str = ""


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
            "healthy": mcp_health.healthy_count,
            "degraded": mcp_health.degraded_count,
            "unhealthy": mcp_health.unhealthy_count,
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
            _t0 = time.monotonic()
            result = await session.ask(req.message)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
            elif result.status == "completed":
                await _meter_and_spend(result.tokens_used)
                _record_slo(result, time.monotonic() - _t0)
                await _record_history(tid, result, time.monotonic() - _t0)
    except HTTPException:
        raise  # 409/4xx should surface as real HTTP errors, not a 200 error body
    except Exception as exc:  # noqa: BLE001 — surface a clean error to the UI
        log.exception("chat turn failed (thread=%s)", tid)
        # If this session was just created and its first turn failed, drop it so
        # a retry rebuilds cleanly instead of reusing a half-initialized session.
        if not existed:
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
        try:
            _t0 = time.monotonic()
            result = await session.resume(approved=req.approved, reason=req.reason)
            if result.status == "awaiting_approval":
                _AWAITING.add(tid)
            else:
                _AWAITING.discard(tid)
                if result.status == "completed":
                    await _meter_and_spend(result.tokens_used)
                    _record_slo(result, time.monotonic() - _t0)
                    # Upserts onto the row the original /chat turn created, so the
                    # investigation keeps its true start time and counts once.
                    await _record_history(tid, result, time.monotonic() - _t0)
        except Exception as exc:  # noqa: BLE001
            log.exception("approve/resume failed (thread=%s)", tid)
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
                    _t0 = time.monotonic()
                    async for ev in session.ask_stream(req.message):
                        t = ev.get("type")
                        if t == "approval":
                            _AWAITING.add(tid)
                        elif t == "done":
                            _AWAITING.discard(tid)
                            await _meter_and_spend(ev.get("tokens_used", 0))
                            await _record_history_from_stream(
                                tid, ev, time.monotonic() - _t0
                            )
                        yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
                except Exception as exc:  # noqa: BLE001
                    log.exception("chat stream failed (thread=%s)", tid)
                    if not existed:
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
                try:
                    _t0 = time.monotonic()
                    async for ev in session.resume_stream(
                        approved=req.approved, reason=req.reason
                    ):
                        t = ev.get("type")
                        if t == "approval":
                            _AWAITING.add(tid)
                        elif t == "done":
                            _AWAITING.discard(tid)
                            await _meter_and_spend(ev.get("tokens_used", 0))
                            await _record_history_from_stream(
                                tid, ev, time.monotonic() - _t0
                            )
                        yield {"data": json.dumps(_stream_payload(req.thread_id, ev))}
                except Exception as exc:  # noqa: BLE001
                    log.exception("approve stream failed (thread=%s)", tid)
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
    from app.api.graphql.schema import _investigations

    results = list(_investigations.values())

    if status:
        results = [r for r in results if r.get("status") == status]

    results.sort(key=lambda x: x.get("started_at", ""), reverse=True)

    investigations = []
    for r in results[offset:offset + limit]:
        investigations.append({
            "id": r.get("id", ""),
            "thread_id": r.get("thread_id", ""),
            "query": r.get("incident_summary", ""),
            "summary": r.get("root_cause"),
            "status": r.get("status", "unknown"),
            "tokens_used": r.get("tokens_used", 0),
            "latency_ms": None,
            "feedback_rating": None,
            "created_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "report": {
                "root_cause": r.get("root_cause"),
                "confidence": r.get("confidence"),
                "hypotheses": r.get("hypotheses", []),
                "evidence": r.get("evidence", []),
                "affected_services": r.get("affected_services", []),
            } if r.get("root_cause") else None,
        })

    return {"investigations": investigations}


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
    """Get status of configured integrations."""
    from app.integrations.slack_notify import is_slack_configured

    return {
        "slack": is_slack_configured(),
        "github": runtime.github_connected(),
        "pagerduty": bool(get_settings().pagerduty_api_token),
        "datadog": bool(get_settings().dd_api_key and get_settings().dd_app_key),
        "sentry": bool(get_settings().sentry_api_token),
        "kubernetes": bool(get_settings().kube_config_path),
    }


@app.get(
    "/audit",
    tags=["Audit"],
    summary="Query audit trail",
    description="Query the audit trail for recent events. Returns approvals, model changes, "
    "prompt-injection detections, and feedback events. Supports filtering by event type prefix.",
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
    "Reports the first broken link if any tampering is detected.",
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
    return await audit.verify_all_chains()


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


@app.patch(
    "/admin/plan",
    tags=["Admin"],
    summary="Update organization plan",
    description="Change the organization's subscription plan. Typically called by "
    "billing webhooks. Requires manage_billing permission.",
    dependencies=[Depends(require_perm("manage_billing"))],
    responses={
        200: {
            "description": "Plan updated",
            "content": {
                "application/json": {
                    "example": {"plan": "team"}
                }
            },
        },
    },
)
async def admin_set_plan(req: SetPlanRequest) -> dict:
    """Update the organization plan.

    Args:
        req: New plan tier.

    Returns:
        dict: Updated plan.
    """
    org_id = _current_org_id()
    plan = tn_models.normalize_plan(req.plan)
    await tenant_auth.get_store().set_plan(org_id, plan)
    audit.record("plan.changed", org=org_id, plan=plan)
    return {"plan": plan}


# --------------------------------------------------------------------------- #
# Self-serve onboarding — a new tenant creates an org + owner key with no prior
# credential (the entry point for product signup). Auth-exempt (see require_auth)
# but still IP rate-limited. Always provisions the FREE plan; upgrades go through
# billing/admin so signup can't self-assign a paid tier.
# --------------------------------------------------------------------------- #
def _scim_guard(request: Request) -> str:
    """Authenticate a SCIM request via COPILOT_SCIM_TOKEN and return the target org.
    Raises 403 when SCIM is unconfigured, 401 on a bad token."""
    s = get_settings()
    if not (s.copilot_scim_token and s.copilot_scim_org):
        raise HTTPException(403, "SCIM provisioning is not configured.")
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token != s.copilot_scim_token:
        raise HTTPException(401, "Invalid SCIM token.")
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
    org_id = _scim_guard(request)
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
    org_id = _scim_guard(request)
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
    worker isn't up, e.g. under TestClient without lifespan)."""
    from app.jobqueue import Job
    if _JOBQ is not None:
        await _JOBQ.enqueue(Job(kind="investigate", payload=incident, id=str(incident.get("id", ""))))
    else:
        asyncio.create_task(_run_triggered_investigation(incident))


async def _run_triggered_investigation(incident: dict) -> None:
    thread_id = f"pd-{incident['id']}"
    title = incident.get("title") or thread_id
    seed = (
        f"A PagerDuty incident was triggered: {title}"
        + (f" (service: {incident['service']})" if incident.get("service") else "")
        + ". Investigate the root cause from logs, metrics, code, and recent changes, "
        "then propose a fix."
    )
    try:
        async with _running_turn(thread_id):
            session = await _get_session(thread_id)
            _t0 = time.monotonic()
            result = await session.ask(seed)
            if result.status == "awaiting_approval":
                _AWAITING.add(thread_id)
            elif result.status == "completed":
                # Meter the triggered path the same as the interactive one (no-op
                # without a tenant); idem-keyed by the incident so a re-run can't double-bill.
                await _meter_and_spend(result.tokens_used, idem=thread_id)
                # Alert-triggered investigations are the ones an operator most
                # wants counted in MTTR — they start when the page fires, not when
                # someone gets to a keyboard.
                _record_slo(result, time.monotonic() - _t0)
                await _record_history(thread_id, result, time.monotonic() - _t0)
        await _post_to_slack(thread_id, title, result)
    except Exception:  # noqa: BLE001 — best-effort background trigger
        log.exception("triggered investigation failed (incident=%s)", incident.get("id"))


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
2. Confidence must be "high" for execution
3. Only reversible actions allowed (rollback, restart)
4. Dry-run mode by default (COPILOT_AUTONOMY_DRYRUN=true)

**Observation Loop:**
After executing, watches metrics for regression. If burn rate increases,
automatically reverts to the previous state.

**Available Actions:**
- `rollback_deployment`: Roll back to previous revision
- `restart_deployment`: Restart deployment pods
""",
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
        400: {"description": "Ineligible for remediation"},
        403: {"description": "Autonomous remediation disabled"},
    },
)
async def remediate(req: RemediateRequest) -> dict:
    """Execute an autonomous remediation.

    Applies a reversible fix, observes metrics, and auto-reverts on regression.

    Args:
        req: Action, target, confidence, and optional namespace.

    Returns:
        dict: Remediation result with status and observations.

    Raises:
        HTTPException: If autonomy is disabled or action is ineligible.
    """
    from app.autonomy import AutonomyController, RemediationPlan, is_eligible
    from app.mcp.servers.datadog import server as _dd
    from app.mcp.servers.kubernetes import server as _k8s

    settings = get_settings()
    if not settings.copilot_autonomy:
        raise HTTPException(403, "Autonomous remediation is disabled (set COPILOT_AUTONOMY=true).")
    ok, why = is_eligible(req.action, req.confidence, settings.copilot_autonomy)
    if not ok:
        raise HTTPException(400, f"Ineligible for autonomous remediation: {why}")

    action_fns = {
        "rollback_deployment": _k8s.rollback_deployment,
        "restart_deployment": _k8s.restart_deployment,
    }

    async def _execute(action: str, target: str) -> dict:
        return action_fns[action](target, req.namespace)

    async def _revert(action: str, target: str) -> dict:
        return action_fns[action](target, req.namespace)

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
        revert_action=revert, namespace=req.namespace,
    )
    result = await controller.remediate(plan)
    audit.record("autonomy.remediation", action=req.action, target=req.target,
                 status=result.get("status"), dry_run=settings.copilot_autonomy_dryrun)
    return result


async def _resume_triggered(thread_id: str, approved: bool) -> None:
    try:
        async with _running_turn(thread_id):
            session = await _get_session(thread_id)
            if not await session.pending_interrupt():
                return
            audit.record("approval.decided", thread=thread_id, approved=approved, via="slack")
            result = await session.resume(approved=approved, reason="decided via Slack")
            if result.status == "awaiting_approval":
                _AWAITING.add(thread_id)
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


@app.post(
    "/webhooks/stripe",
    tags=["Webhooks"],
    summary="Stripe webhook receiver",
    description="Receive Stripe subscription events. Maps subscription created/updated/deleted "
    "to tenant plan changes. Requires valid signature (STRIPE_WEBHOOK_SECRET).",
    responses={
        200: {
            "description": "Webhook processed",
            "content": {
                "application/json": {
                    "examples": {
                        "applied": {"value": {"status": "applied", "org_id": "org_123", "plan": "team"}},
                        "ignored": {"value": {"status": "ignored", "type": "payment_intent.created"}}
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

    Verifies the signature and updates tenant plans based on subscription events.

    Returns:
        dict: Processing status, org ID, and new plan.
    """
    import json as _json
    import time as _time

    from app import billing
    s = get_settings()
    raw = await request.body()
    if not billing.verify_stripe_signature(
        raw, request.headers.get("stripe-signature", ""), s.stripe_webhook_secret,
        now=int(_time.time()),
    ):
        raise HTTPException(401, "Invalid Stripe signature.")
    try:
        event = _json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Malformed Stripe event.") from None
    mapping = billing.plan_from_stripe_event(event)
    if mapping is None:
        return {"status": "ignored", "type": event.get("type")}
    org_id, plan = mapping
    await tenant_auth.get_store().set_plan(org_id, plan)
    audit.record("billing.plan_changed", org_id=org_id, plan=plan, via="stripe")
    return {"status": "applied", "org_id": org_id, "plan": plan}


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

    # Unconditional: `if secret and not verify(...)` meant a deployment that never
    # set GITHUB_WEBHOOK_SECRET accepted unsigned events from anyone, which is the
    # same hole the helper is written to close.
    if not _verify_github_signature(s.github_webhook_secret, raw, sig):
        raise HTTPException(401, "invalid or unconfigured GitHub webhook signature")

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

    if event_type == "push":
        result["action"] = "deployment_tracked"
        result["ref"] = payload.get("ref", "")
        result["commits"] = len(payload.get("commits", []))
        result["repository"] = payload.get("repository", {}).get("full_name", "")

    elif event_type == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        result["action"] = action
        result["pr_number"] = pr.get("number")
        result["pr_title"] = pr.get("title", "")
        result["pr_state"] = pr.get("state", "")

        if action in ("opened", "synchronize") and active_api_key():
            changes = pr.get("changed_files", 0)
            if changes > 50:
                result["auto_triage"] = "large_pr_flagged"

    elif event_type == "issues":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        result["action"] = action
        result["issue_number"] = issue.get("number")
        result["issue_title"] = issue.get("title", "")

        if action == "opened" and active_api_key():
            labels = [lbl.get("name", "").lower() for lbl in issue.get("labels", [])]
            if "bug" in labels or "incident" in labels:
                result["auto_triage"] = "incident_detected"

    elif event_type == "deployment_status":
        deployment = payload.get("deployment", {})
        result["deployment_id"] = deployment.get("id")
        result["environment"] = deployment.get("environment", "")
        result["state"] = payload.get("deployment_status", {}).get("state", "")

        if result["state"] == "failure" and active_api_key():
            result["auto_triage"] = "deployment_failure_detected"

    elif event_type == "security_advisory":
        advisory = payload.get("security_advisory", {})
        result["severity"] = advisory.get("severity", "")
        result["summary"] = advisory.get("summary", "")
        result["auto_triage"] = "security_alert"

    return result


# --------------------------------------------------------------------------- #
# GitLab Webhook — auto-triage merge requests, issues, pipelines
# --------------------------------------------------------------------------- #
def _verify_gitlab_token(secret: str, token: str) -> bool:
    """Verify the GitLab webhook secret token.

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

    if not _verify_gitlab_token(s.gitlab_webhook_secret, token):
        raise HTTPException(401, "invalid or unconfigured GitLab webhook token")

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

    if event_type == "push":
        result["ref"] = payload.get("ref", "")
        result["commits"] = len(payload.get("commits", []))
        result["project"] = payload.get("project", {}).get("path_with_namespace", "")

    elif event_type == "merge_request":
        attrs = payload.get("object_attributes", {})
        result["action"] = attrs.get("action", "")
        result["mr_iid"] = attrs.get("iid")
        result["mr_title"] = attrs.get("title", "")
        result["mr_state"] = attrs.get("state", "")

        if attrs.get("action") == "open" and active_api_key():
            changes = payload.get("changes", {})
            if changes:
                result["auto_triage"] = "mr_opened"

    elif event_type == "issue":
        attrs = payload.get("object_attributes", {})
        result["action"] = attrs.get("action", "")
        result["issue_iid"] = attrs.get("iid")
        result["issue_title"] = attrs.get("title", "")

        if attrs.get("action") == "open" and active_api_key():
            labels = [lbl.get("title", "").lower() for lbl in payload.get("labels", [])]
            if "bug" in labels or "incident" in labels:
                result["auto_triage"] = "incident_detected"

    elif event_type == "pipeline":
        attrs = payload.get("object_attributes", {})
        result["pipeline_id"] = attrs.get("id")
        result["status"] = attrs.get("status", "")
        result["ref"] = attrs.get("ref", "")

        if attrs.get("status") == "failed" and active_api_key():
            result["auto_triage"] = "pipeline_failure_detected"

    elif event_type == "deployment":
        result["deployment_id"] = payload.get("deployment_id")
        result["environment"] = payload.get("environment", "")
        result["status"] = payload.get("status", "")

        if payload.get("status") == "failed" and active_api_key():
            result["auto_triage"] = "deployment_failure_detected"

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
    "Returns your position and referral code.",
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
    summary="Get waitlist statistics",
    description="Get aggregated waitlist statistics (admin only).",
    dependencies=[Depends(require_auth)],
)
async def waitlist_stats():
    """Get waitlist statistics."""
    from app.waitlist import get_waitlist_stats

    return await get_waitlist_stats()


# --------------------------------------------------------------------------- #
# Customer & Trial Management — track conversions and engagement
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
    summary="Create a customer",
    description="Create a new customer and optionally start a trial.",
    dependencies=[Depends(require_auth)],
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
    summary="Get customer statistics",
    description="Get customer and trial statistics for the dashboard.",
    dependencies=[Depends(require_auth)],
)
async def customer_stats():
    """Get customer statistics."""
    from app.customers import get_customer_stats

    return await get_customer_stats()


@app.get(
    "/customers/dashboard",
    tags=["Customers"],
    summary="Get customer dashboard",
    description="Get full customer dashboard including stats, trials, and recent customers.",
    dependencies=[Depends(require_auth)],
)
async def customer_dashboard():
    """Get customer dashboard data."""
    from app.customers import CustomerManager

    manager = CustomerManager()
    return await manager.get_dashboard()


@app.get(
    "/customers/trials/active",
    tags=["Customers"],
    summary="Get active trials",
    description="Get list of all active trials.",
    dependencies=[Depends(require_auth)],
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
    summary="Record customer usage",
    description="Record a usage event for a customer (investigation, etc).",
    dependencies=[Depends(require_auth)],
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
    tier: str = "starter"
    mrr_cents: int = 0
    stripe_customer_id: str = ""


@app.post(
    "/customers/convert",
    tags=["Customers"],
    summary="Convert trial to paid",
    description="Convert a trial customer to a paid subscription.",
    dependencies=[Depends(require_auth)],
)
async def convert_trial_endpoint(req: ConvertTrialRequest):
    """Convert a trial to paid subscription."""
    from app.customers import convert_trial, CustomerTier

    tier_map = {
        "free": CustomerTier.FREE,
        "starter": CustomerTier.STARTER,
        "pro": CustomerTier.PRO,
        "enterprise": CustomerTier.ENTERPRISE,
    }
    tier = tier_map.get(req.tier.lower(), CustomerTier.STARTER)

    success, message = await convert_trial(
        customer_id=req.customer_id,
        tier=tier,
        mrr_cents=req.mrr_cents,
        stripe_customer_id=req.stripe_customer_id,
    )

    if not success:
        raise HTTPException(400, message)

    audit.record(
        "customer.converted",
        customer_id=req.customer_id,
        tier=tier.value,
        mrr_cents=req.mrr_cents,
    )

    return {"success": True, "message": message}


# --------------------------------------------------------------------------- #
# Investigation analytics — MTTR, ROI and outcome mix over recorded history.
# --------------------------------------------------------------------------- #
# The read side of the `investigations` table that /chat, /chat/stream, /approve
# and the alert-triggered path all write to. Scoped to the calling tenant; in
# single-tenant mode everything is recorded under one org.
@app.get(
    "/dashboard/metrics",
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
    "/dashboard/trends",
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
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="spa")
    log.info("serving built frontend from %s", _FRONTEND_DIST)
