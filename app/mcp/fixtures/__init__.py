"""Centralized fixture system for multi-scenario offline demos.

Supports multiple incident scenarios that can be selected via:
  - COPILOT_DEMO_SCENARIO env var (default: "checkout-discount")
  - --scenario flag in CLI tools

Available scenarios:
  - checkout-discount: Bad discount code deploy causes 5xx errors (default)
  - memory-leak: Payment service OOM kills from memory leak
  - cascading-failure: Auth service timeout causes downstream failures
  - config-drift: Terraform config mismatch causes 500s
  - false-alarm: High CPU from expected batch job (agent should NOT escalate)

Each scenario provides consistent fixtures across ALL MCP servers so the agent
sees a coherent incident narrative regardless of which tools it calls.
"""

from __future__ import annotations

import os
from typing import Any

SCENARIO = os.environ.get("COPILOT_DEMO_SCENARIO", "checkout-discount").strip()

SCENARIOS = {
    "checkout-discount",
    "memory-leak",
    "cascading-failure",
    "config-drift",
    "false-alarm",
}


def get_scenario() -> str:
    """Return the active scenario name."""
    return SCENARIO if SCENARIO in SCENARIOS else "checkout-discount"


def get_fixture(server: str, key: str) -> Any:
    """Get a fixture for a specific server and key.

    Args:
        server: MCP server name (datadog, kubernetes, pagerduty, github, etc.)
        key: Fixture key (pods, incidents, logs, etc.)

    Returns:
        The fixture data for the active scenario.
    """
    scenario = get_scenario()
    fixtures = _FIXTURES.get(scenario, {}).get(server, {})
    return fixtures.get(key)


# --------------------------------------------------------------------------- #
# Scenario 1: checkout-discount (default - the existing demo)
# Bad discount code deploy causes CrashLoopBackOff and 5xx errors
# Root cause: Negative discount percentage causes division-by-zero
# --------------------------------------------------------------------------- #
_CHECKOUT_DISCOUNT = {
    "pagerduty": {
        "incident": {
            "id": "PINC4242",
            "number": 4242,
            "title": "Elevated 5xx error rate on checkout-svc",
            "status": "triggered",
            "urgency": "high",
            "service": "checkout-svc",
            "created_at": "2026-06-23T14:05:00Z",
            "html_url": "https://acme.pagerduty.com/incidents/PINC4242",
            "description": "checkout-svc 5xx error rate crossed the alert threshold.",
        },
        "alerts": [
            {
                "id": "PALERT1",
                "status": "triggered",
                "summary": "checkout-svc 5xx error rate > 5% (Datadog monitor)",
                "created_at": "2026-06-23T14:05:00Z",
                "body": "error_rate_5xx=0.71 exceeded threshold 0.05 for service:checkout-svc "
                "over the last 5m. Source monitor: 'checkout-svc 5xx'.",
            }
        ],
    },
    "kubernetes": {
        "pods": [
            {
                "name": "checkout-svc-7d9f4c8b6-q2m4x", "namespace": "default", "status": "CrashLoopBackOff",
                "ready": "0/1", "restarts": 8, "age": "12m", "node": "ip-10-0-1-23",
                "reason": "Back-off restarting failed container (exit code 1)",
            },
            {
                "name": "checkout-svc-7d9f4c8b6-h7k9p", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 5, "age": "12m", "node": "ip-10-0-1-44",
                "reason": "",
            },
            {
                "name": "checkout-svc-6b1a2c3d4-zz000", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "6d", "node": "ip-10-0-1-7",
                "reason": "previous stable revision",
            },
            {
                "name": "inventory-svc-5c4b3a2-abc12", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "9d", "node": "ip-10-0-1-9",
                "reason": "",
            },
        ],
        "events": [
            {"type": "Normal", "reason": "ScalingReplicaSet", "object": "deployment/checkout-svc",
             "age": "13m", "message": "Scaled up replica set checkout-svc-7d9f4c8b6 to 3"},
            {"type": "Normal", "reason": "Pulled", "object": "pod/checkout-svc-7d9f4c8b6-q2m4x",
             "age": "12m", "message": "Successfully pulled image checkout-svc:1.8.0"},
            {"type": "Warning", "reason": "BackOff", "object": "pod/checkout-svc-7d9f4c8b6-q2m4x",
             "age": "11m", "message": "Back-off restarting failed container checkout in pod"},
            {"type": "Warning", "reason": "Unhealthy", "object": "pod/checkout-svc-7d9f4c8b6-q2m4x",
             "age": "11m", "message": "Readiness probe failed: HTTP probe returned 500"},
        ],
        "deployment": {
            "name": "checkout-svc", "namespace": "default",
            "desired": 3, "ready": 1, "available": 1, "unavailable": 2, "updated": 3,
            "image": "checkout-svc:1.8.0",
            "conditions": [
                {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"},
                {"type": "Progressing", "status": "False",
                 "reason": "ProgressDeadlineExceeded", "message": "ReplicaSet checkout-svc-7d9f4c8b6 has timed out progressing"},
            ],
            "rollout": "stuck — 2/3 replicas unavailable since the 1.8.0 rollout",
        },
        "rollout_history": [
            {"revision": 1, "image": "checkout-svc:1.6.2", "change_cause": "Initial deploy"},
            {"revision": 2, "image": "checkout-svc:1.7.0", "change_cause": "Refactor cart total calculation"},
            {"revision": 3, "image": "checkout-svc:1.8.0",
             "change_cause": "Add percentage discount support to checkout (commit abc1234)"},
        ],
    },
    "github": {
        "commits": [
            {
                "sha": "abc1234",
                "message": "Add percentage discount support to checkout",
                "author": "alice",
                "date": "2026-06-23T13:45:00Z",
                "files": ["src/checkout/discount.py", "tests/test_discount.py"],
            },
            {
                "sha": "def5678",
                "message": "Fix lint warnings",
                "author": "bob",
                "date": "2026-06-23T12:30:00Z",
                "files": ["src/checkout/utils.py"],
            },
            {
                "sha": "ghi9012",
                "message": "Update dependencies",
                "author": "alice",
                "date": "2026-06-22T16:00:00Z",
                "files": ["requirements.txt"],
            },
        ],
        "diff_abc1234": """diff --git a/src/checkout/discount.py b/src/checkout/discount.py
--- a/src/checkout/discount.py
+++ b/src/checkout/discount.py
@@ -10,6 +10,15 @@ def apply_discount(total: float, code: str) -> float:
     if code == "SAVE10":
         return total * 0.9
+    # NEW: Percentage discounts from promo campaigns
+    if code.startswith("PCT"):
+        pct = int(code[3:])  # BUG: No validation for negative or >100
+        return total * (100 - pct) / 100  # Division by zero if pct=100
     return total
""",
    },
    "sentry": {
        "issues": [
            {
                "id": "SENTRY-42",
                "title": "ZeroDivisionError: division by zero",
                "culprit": "checkout.discount.apply_discount",
                "count": 1847,
                "first_seen": "2026-06-23T14:02:00Z",
                "last_seen": "2026-06-23T14:35:00Z",
            }
        ],
        "latest_event": {
            "issue_id": "SENTRY-42",
            "exception": "ZeroDivisionError: division by zero",
            "stacktrace": [
                {"file": "src/checkout/discount.py", "line": 16, "function": "apply_discount",
                 "context": "return total * (100 - pct) / 100"},
            ],
            "tags": {"service": "checkout-svc", "release": "1.8.0", "environment": "production"},
        },
    },
    "datadog": {
        "services": ["checkout-svc", "inventory-svc", "payment-svc", "auth-svc"],
        "error_summary": {
            "service": "checkout-svc",
            "total_errors": 1847,
            "distinct_errors": 3,
            "breakdown": [
                {"message": "ZeroDivisionError: division by zero in apply_discount", "count": 1502},
                {"message": "ValueError: Invalid discount code format", "count": 298},
                {"message": "ConnectionError: upstream timeout", "count": 47},
            ],
        },
    },
    "argocd": {
        "apps": [
            {"name": "checkout-svc", "status": "Degraded", "health": "Degraded",
             "sync": "Synced", "revision": "abc1234", "message": "Deployment checkout-svc has minimum availability"},
            {"name": "inventory-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "xyz7890", "message": ""},
            {"name": "payment-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "mno3456", "message": ""},
        ],
    },
    "traces": {
        "traces": [
            {
                "trace_id": "tr-checkout-err-001", "root_service": "api-gateway",
                "operation": "POST /api/checkout", "duration_ms": 512, "status": "error",
                "start": "2026-06-23T14:01:15Z", "span_count": 4,
                "spans": [
                    {"span_id": "s1", "service": "api-gateway", "operation": "POST /api/checkout",
                     "duration_ms": 512, "status": "error", "parent": None},
                    {"span_id": "s2", "service": "checkout-svc", "operation": "checkout",
                     "duration_ms": 480, "status": "error", "parent": "s1"},
                    {"span_id": "s3", "service": "checkout-svc", "operation": "applyDiscount",
                     "duration_ms": 2, "status": "error", "parent": "s2",
                     "error": "ZeroDivisionError: division by zero at apply_discount (discount.py:16)"},
                    {"span_id": "s4", "service": "inventory-svc", "operation": "GET /stock",
                     "duration_ms": 44, "status": "ok", "parent": "s2"},
                ],
            },
            {
                "trace_id": "tr-checkout-ok-002", "root_service": "api-gateway",
                "operation": "POST /api/checkout", "duration_ms": 233, "status": "ok",
                "start": "2026-06-23T13:59:11Z", "span_count": 4,
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
        ],
    },
    "deploys": {
        "deploys": [
            {
                "id": "dep-9001", "service": "checkout-svc", "version": "1.8.0", "sha": "abc1234",
                "status": "succeeded", "env": "production", "deployer": "ci-bot",
                "deployed_at": "2026-06-23T13:58:30Z",
                "change_cause": "Add percentage discount support to checkout (commit abc1234)",
            },
            {
                "id": "dep-8800", "service": "checkout-svc", "version": "1.7.0", "sha": "9f8e7d6",
                "status": "succeeded", "env": "production", "deployer": "ci-bot",
                "deployed_at": "2026-06-22T16:12:00Z",
                "change_cause": "Refactor cart total calculation",
            },
            {
                "id": "dep-8120", "service": "inventory-svc", "version": "2.3.1", "sha": "5c4b3a2",
                "status": "succeeded", "env": "production", "deployer": "ci-bot",
                "deployed_at": "2026-06-20T11:00:00Z",
                "change_cause": "Cache stock lookups",
            },
        ],
    },
    "terraform": {
        "workspaces": [
            {
                "id": "ws-checkout-prod", "name": "checkout-prod",
                "description": "Production checkout service infrastructure",
                "environment": "production", "terraform_version": "1.6.0",
                "auto_apply": False, "vcs_repo": "acme/checkout-infra",
                "locked": False, "resource_count": 24, "run_status": "errored",
                "last_run_at": "2026-06-23T14:00:00Z",
            },
        ],
        "runs": [
            {
                "id": "run-abc123", "status": "errored", "source": "tfe-ui",
                "trigger_reason": "VCS commit abc1234",
                "message": "Add percentage discount support",
                "created_at": "2026-06-23T14:00:00Z",
                "has_changes": True, "resource_changes": 2,
                "error_message": "Error: ECS service failed health checks",
            },
        ],
    },
}


# --------------------------------------------------------------------------- #
# Scenario 2: memory-leak
# Payment service OOM kills from memory leak in cache layer
# Root cause: Unbounded cache growth after removing TTL in config change
# --------------------------------------------------------------------------- #
_MEMORY_LEAK = {
    "pagerduty": {
        "incident": {
            "id": "PINC5001",
            "number": 5001,
            "title": "payment-svc pods repeatedly OOMKilled",
            "status": "triggered",
            "urgency": "high",
            "service": "payment-svc",
            "created_at": "2026-07-15T09:30:00Z",
            "html_url": "https://acme.pagerduty.com/incidents/PINC5001",
            "description": "Multiple payment-svc pods killed due to memory exhaustion.",
        },
        "alerts": [
            {
                "id": "PALERT2",
                "status": "triggered",
                "summary": "payment-svc OOMKilled 5+ times in 30m",
                "created_at": "2026-07-15T09:30:00Z",
                "body": "Container payment exceeded memory limit 512Mi. "
                "Kubernetes killed pod payment-svc-8a7b6c5d-xyz01.",
            }
        ],
    },
    "kubernetes": {
        "pods": [
            {
                "name": "payment-svc-8a7b6c5d-xyz01", "namespace": "default", "status": "OOMKilled",
                "ready": "0/1", "restarts": 7, "age": "45m", "node": "ip-10-0-2-15",
                "reason": "OOMKilled (exit code 137)",
            },
            {
                "name": "payment-svc-8a7b6c5d-abc02", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 3, "age": "45m", "node": "ip-10-0-2-16",
                "reason": "Memory: 489Mi/512Mi (95%)",
            },
            {
                "name": "payment-svc-7z6y5x4w-old01", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "5d", "node": "ip-10-0-2-10",
                "reason": "previous stable revision",
            },
            {
                "name": "checkout-svc-stable-001", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "3d", "node": "ip-10-0-1-9",
                "reason": "",
            },
        ],
        "events": [
            {"type": "Normal", "reason": "ScalingReplicaSet", "object": "deployment/payment-svc",
             "age": "46m", "message": "Scaled up replica set payment-svc-8a7b6c5d to 3"},
            {"type": "Warning", "reason": "OOMKilling", "object": "pod/payment-svc-8a7b6c5d-xyz01",
             "age": "40m", "message": "Container payment exceeded memory limit 512Mi"},
            {"type": "Warning", "reason": "OOMKilling", "object": "pod/payment-svc-8a7b6c5d-xyz01",
             "age": "32m", "message": "Container payment exceeded memory limit 512Mi"},
            {"type": "Warning", "reason": "OOMKilling", "object": "pod/payment-svc-8a7b6c5d-xyz01",
             "age": "18m", "message": "Container payment exceeded memory limit 512Mi"},
        ],
        "deployment": {
            "name": "payment-svc", "namespace": "default",
            "desired": 3, "ready": 1, "available": 1, "unavailable": 2, "updated": 3,
            "image": "payment-svc:2.3.0",
            "conditions": [
                {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"},
            ],
            "rollout": "unstable — pods OOMKilled repeatedly",
        },
        "rollout_history": [
            {"revision": 1, "image": "payment-svc:2.1.0", "change_cause": "Initial deploy"},
            {"revision": 2, "image": "payment-svc:2.2.0", "change_cause": "Add retry logic"},
            {"revision": 3, "image": "payment-svc:2.3.0",
             "change_cause": "Performance optimization: disable cache TTL (commit mem123)"},
        ],
    },
    "github": {
        "commits": [
            {
                "sha": "mem123",
                "message": "Performance optimization: disable cache TTL for faster lookups",
                "author": "charlie",
                "date": "2026-07-15T08:45:00Z",
                "files": ["src/payment/cache.py", "config/settings.yaml"],
            },
            {
                "sha": "mem456",
                "message": "Add payment retry logic",
                "author": "diana",
                "date": "2026-07-14T16:00:00Z",
                "files": ["src/payment/processor.py"],
            },
        ],
        "diff_mem123": """diff --git a/src/payment/cache.py b/src/payment/cache.py
--- a/src/payment/cache.py
+++ b/src/payment/cache.py
@@ -5,7 +5,8 @@ class TransactionCache:
     def __init__(self):
         self._cache = {}
-        self._ttl = 300  # 5 minute TTL
+        # PERF: Disable TTL for faster lookups
+        self._ttl = None  # BUG: Cache grows unbounded without eviction

     def get(self, txn_id):
         entry = self._cache.get(txn_id)
-        if entry and time.time() - entry['ts'] < self._ttl:
+        if entry:  # No TTL check = entries never expire
             return entry['data']
""",
    },
    "sentry": {
        "issues": [
            {
                "id": "SENTRY-55",
                "title": "MemoryError: unable to allocate array",
                "culprit": "payment.cache.TransactionCache.get",
                "count": 89,
                "first_seen": "2026-07-15T09:15:00Z",
                "last_seen": "2026-07-15T10:05:00Z",
            }
        ],
        "latest_event": {
            "issue_id": "SENTRY-55",
            "exception": "MemoryError: unable to allocate 128 MiB for an array",
            "stacktrace": [
                {"file": "src/payment/cache.py", "line": 12, "function": "get",
                 "context": "return entry['data']"},
            ],
            "tags": {"service": "payment-svc", "release": "2.3.0", "environment": "production"},
        },
    },
    "datadog": {
        "services": ["checkout-svc", "inventory-svc", "payment-svc", "auth-svc"],
        "error_summary": {
            "service": "payment-svc",
            "total_errors": 89,
            "distinct_errors": 2,
            "breakdown": [
                {"message": "MemoryError: unable to allocate array", "count": 67},
                {"message": "ConnectionError: payment gateway timeout", "count": 22},
            ],
        },
        "metrics": {
            "payment-svc": {
                "memory_usage_mb": [
                    {"ts": 1720000000, "value": 120},
                    {"ts": 1720000300, "value": 180},
                    {"ts": 1720000600, "value": 290},
                    {"ts": 1720000900, "value": 410},
                    {"ts": 1720001200, "value": 498},
                    {"ts": 1720001500, "value": 512},  # OOM
                ],
            },
        },
    },
    "argocd": {
        "apps": [
            {"name": "checkout-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "stable01", "message": ""},
            {"name": "payment-svc", "status": "Degraded", "health": "Degraded",
             "sync": "Synced", "revision": "mem123", "message": "Pods OOMKilled repeatedly"},
            {"name": "inventory-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "xyz7890", "message": ""},
        ],
    },
    "traces": {
        "traces": [
            {
                "trace_id": "mem999trace",
                "service": "payment-svc",
                "operation": "POST /api/payment/process",
                "duration_ms": 3500,
                "status": "ERROR",
                "spans": [
                    {"service": "payment-svc", "operation": "cache.get", "duration_ms": 3200, "error": True},
                ],
            }
        ],
    },
}


# --------------------------------------------------------------------------- #
# Scenario 3: cascading-failure
# Auth service timeout causes downstream failures in orders and checkout
# Root cause: Database connection pool exhaustion after traffic spike
# --------------------------------------------------------------------------- #
_CASCADING_FAILURE = {
    "pagerduty": {
        "incident": {
            "id": "PINC6100",
            "number": 6100,
            "title": "Multiple services failing: auth, orders, checkout",
            "status": "triggered",
            "urgency": "high",
            "service": "platform",
            "created_at": "2026-07-20T11:15:00Z",
            "html_url": "https://acme.pagerduty.com/incidents/PINC6100",
            "description": "Cascading failures across auth, orders, and checkout services.",
        },
        "alerts": [
            {
                "id": "PALERT3A",
                "status": "triggered",
                "summary": "auth-svc latency p99 > 5s",
                "created_at": "2026-07-20T11:12:00Z",
                "body": "auth-svc p99_latency_ms=8200 exceeded threshold 5000ms.",
            },
            {
                "id": "PALERT3B",
                "status": "triggered",
                "summary": "orders-svc 5xx error rate > 30%",
                "created_at": "2026-07-20T11:14:00Z",
                "body": "orders-svc error_rate=0.42, all errors show 'upstream auth timeout'.",
            },
            {
                "id": "PALERT3C",
                "status": "triggered",
                "summary": "checkout-svc 5xx error rate > 30%",
                "created_at": "2026-07-20T11:15:00Z",
                "body": "checkout-svc error_rate=0.38, most errors show 'orders-svc unavailable'.",
            },
        ],
    },
    "kubernetes": {
        "pods": [
            {
                "name": "auth-svc-9c8d7e6f-aaa01", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "2d", "node": "ip-10-0-3-11",
                "reason": "Slow: DB connection pool exhausted",
            },
            {
                "name": "orders-svc-5d4e3f2g-bbb02", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "2d", "node": "ip-10-0-3-12",
                "reason": "Waiting on auth-svc",
            },
            {
                "name": "checkout-svc-1a2b3c4d-ccc03", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "2d", "node": "ip-10-0-3-13",
                "reason": "Waiting on orders-svc",
            },
        ],
        "events": [
            {"type": "Normal", "reason": "Scheduled", "object": "pod/auth-svc-9c8d7e6f-aaa01",
             "age": "2d", "message": "Successfully assigned to node"},
            {"type": "Warning", "reason": "Unhealthy", "object": "pod/auth-svc-9c8d7e6f-aaa01",
             "age": "8m", "message": "Readiness probe failed: connection timeout"},
        ],
        "deployment": {
            "name": "auth-svc", "namespace": "default",
            "desired": 3, "ready": 3, "available": 3, "unavailable": 0, "updated": 3,
            "image": "auth-svc:3.1.0",
            "conditions": [
                {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
            ],
            "rollout": "complete — but service is slow due to DB pool exhaustion",
        },
        "rollout_history": [
            {"revision": 1, "image": "auth-svc:3.0.0", "change_cause": "Initial deploy"},
            {"revision": 2, "image": "auth-svc:3.1.0", "change_cause": "Security patch"},
        ],
    },
    "github": {
        "commits": [
            {
                "sha": "casc001",
                "message": "Security patch for auth-svc",
                "author": "security-bot",
                "date": "2026-07-19T22:00:00Z",
                "files": ["src/auth/handler.py"],
            },
        ],
        "diff_casc001": """diff --git a/src/auth/handler.py b/src/auth/handler.py
--- a/src/auth/handler.py
+++ b/src/auth/handler.py
@@ -1,3 +1,4 @@
+# Security patch: no functional change
 def handle_auth(request):
     return validate_token(request.token)
""",
    },
    "sentry": {
        "issues": [
            {
                "id": "SENTRY-70",
                "title": "TimeoutError: DB connection pool exhausted",
                "culprit": "auth.db.get_connection",
                "count": 2341,
                "first_seen": "2026-07-20T11:10:00Z",
                "last_seen": "2026-07-20T11:45:00Z",
            },
            {
                "id": "SENTRY-71",
                "title": "HTTPError: 503 from auth-svc",
                "culprit": "orders.client.authenticate",
                "count": 1892,
                "first_seen": "2026-07-20T11:12:00Z",
                "last_seen": "2026-07-20T11:45:00Z",
            },
        ],
        "latest_event": {
            "issue_id": "SENTRY-70",
            "exception": "TimeoutError: Timed out waiting for DB connection (pool size: 10, waiting: 47)",
            "stacktrace": [
                {"file": "src/auth/db.py", "line": 25, "function": "get_connection",
                 "context": "return await self.pool.acquire(timeout=5)"},
            ],
            "tags": {"service": "auth-svc", "release": "3.1.0", "environment": "production"},
        },
    },
    "datadog": {
        "services": ["auth-svc", "orders-svc", "checkout-svc", "inventory-svc"],
        "error_summary": {
            "service": "auth-svc",
            "total_errors": 2341,
            "distinct_errors": 1,
            "breakdown": [
                {"message": "TimeoutError: DB connection pool exhausted", "count": 2341},
            ],
        },
        "metrics": {
            "auth-svc": {
                "db_pool_waiting": [
                    {"ts": 1720000000, "value": 0},
                    {"ts": 1720000300, "value": 5},
                    {"ts": 1720000600, "value": 23},
                    {"ts": 1720000900, "value": 47},
                    {"ts": 1720001200, "value": 52},
                ],
                "p99_latency_ms": [
                    {"ts": 1720000000, "value": 120},
                    {"ts": 1720000300, "value": 450},
                    {"ts": 1720000600, "value": 2100},
                    {"ts": 1720000900, "value": 5800},
                    {"ts": 1720001200, "value": 8200},
                ],
            },
        },
    },
    "argocd": {
        "apps": [
            {"name": "auth-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "casc001", "message": ""},
            {"name": "orders-svc", "status": "Degraded", "health": "Degraded",
             "sync": "Synced", "revision": "ord001", "message": "Upstream auth failures"},
            {"name": "checkout-svc", "status": "Degraded", "health": "Degraded",
             "sync": "Synced", "revision": "chk001", "message": "Upstream orders failures"},
        ],
    },
    "traces": {
        "traces": [
            {
                "trace_id": "cascade001",
                "service": "checkout-svc",
                "operation": "POST /api/checkout",
                "duration_ms": 15200,
                "status": "ERROR",
                "spans": [
                    {"service": "checkout-svc", "operation": "call orders-svc", "duration_ms": 15000, "error": True},
                    {"service": "orders-svc", "operation": "call auth-svc", "duration_ms": 14800, "error": True},
                    {"service": "auth-svc", "operation": "db.get_connection", "duration_ms": 14500, "error": True},
                ],
            }
        ],
    },
}


# --------------------------------------------------------------------------- #
# Scenario 4: config-drift
# Terraform config mismatch causes 500 errors (env var missing after apply)
# Root cause: Terraform apply removed a required env var
# --------------------------------------------------------------------------- #
_CONFIG_DRIFT = {
    "pagerduty": {
        "incident": {
            "id": "PINC7200",
            "number": 7200,
            "title": "inventory-svc returning 500 on all requests",
            "status": "triggered",
            "urgency": "high",
            "service": "inventory-svc",
            "created_at": "2026-07-22T16:20:00Z",
            "html_url": "https://acme.pagerduty.com/incidents/PINC7200",
            "description": "inventory-svc 100% error rate after Terraform apply.",
        },
        "alerts": [
            {
                "id": "PALERT4",
                "status": "triggered",
                "summary": "inventory-svc error rate 100%",
                "created_at": "2026-07-22T16:20:00Z",
                "body": "All requests to inventory-svc returning 500. Started immediately after "
                "Terraform apply at 16:15.",
            }
        ],
    },
    "kubernetes": {
        "pods": [
            {
                "name": "inventory-svc-4e5f6g7h-inv01", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "5m", "node": "ip-10-0-4-21",
                "reason": "Config error: REDIS_URL env var missing",
            },
            {
                "name": "inventory-svc-3d4e5f6g-old01", "namespace": "default", "status": "Terminating",
                "ready": "0/1", "restarts": 0, "age": "8d", "node": "ip-10-0-4-22",
                "reason": "Previous version being terminated",
            },
        ],
        "events": [
            {"type": "Normal", "reason": "ScalingReplicaSet", "object": "deployment/inventory-svc",
             "age": "6m", "message": "Scaled up replica set inventory-svc-4e5f6g7h to 3"},
            {"type": "Normal", "reason": "Pulled", "object": "pod/inventory-svc-4e5f6g7h-inv01",
             "age": "5m", "message": "Successfully pulled image inventory-svc:4.2.0"},
            {"type": "Warning", "reason": "Unhealthy", "object": "pod/inventory-svc-4e5f6g7h-inv01",
             "age": "4m", "message": "Readiness probe failed: HTTP 500"},
        ],
        "deployment": {
            "name": "inventory-svc", "namespace": "default",
            "desired": 3, "ready": 3, "available": 3, "unavailable": 0, "updated": 3,
            "image": "inventory-svc:4.2.0",
            "conditions": [
                {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
            ],
            "rollout": "complete — but app crashes on startup due to missing env var",
        },
        "rollout_history": [
            {"revision": 1, "image": "inventory-svc:4.1.0", "change_cause": "Initial deploy"},
            {"revision": 2, "image": "inventory-svc:4.2.0",
             "change_cause": "Terraform apply (workspace: prod-us-east)"},
        ],
    },
    "github": {
        "commits": [
            {
                "sha": "drift001",
                "message": "Refactor inventory-svc deployment config",
                "author": "terraform-bot",
                "date": "2026-07-22T16:10:00Z",
                "files": ["terraform/modules/inventory/main.tf"],
            },
        ],
        "diff_drift001": """diff --git a/terraform/modules/inventory/main.tf b/terraform/modules/inventory/main.tf
--- a/terraform/modules/inventory/main.tf
+++ b/terraform/modules/inventory/main.tf
@@ -15,10 +15,8 @@ resource "kubernetes_deployment" "inventory" {
         env {
           name  = "DATABASE_URL"
           value = var.database_url
         }
-        env {
-          name  = "REDIS_URL"  # REMOVED - BUG: Still needed by app
-          value = var.redis_url
-        }
         env {
           name  = "LOG_LEVEL"
""",
    },
    "sentry": {
        "issues": [
            {
                "id": "SENTRY-80",
                "title": "KeyError: 'REDIS_URL'",
                "culprit": "inventory.config.load_config",
                "count": 892,
                "first_seen": "2026-07-22T16:16:00Z",
                "last_seen": "2026-07-22T16:45:00Z",
            }
        ],
        "latest_event": {
            "issue_id": "SENTRY-80",
            "exception": "KeyError: 'REDIS_URL' - Required environment variable not set",
            "stacktrace": [
                {"file": "src/inventory/config.py", "line": 8, "function": "load_config",
                 "context": "redis_url = os.environ['REDIS_URL']"},
            ],
            "tags": {"service": "inventory-svc", "release": "4.2.0", "environment": "production"},
        },
    },
    "datadog": {
        "services": ["checkout-svc", "inventory-svc", "payment-svc", "auth-svc"],
        "error_summary": {
            "service": "inventory-svc",
            "total_errors": 892,
            "distinct_errors": 1,
            "breakdown": [
                {"message": "KeyError: 'REDIS_URL'", "count": 892},
            ],
        },
    },
    "terraform": {
        "workspaces": [
            {
                "id": "ws-prod-us-east",
                "name": "prod-us-east",
                "status": "applied",
                "last_run": "2026-07-22T16:15:00Z",
                "resources_changed": 3,
            },
        ],
        "runs": [
            {
                "id": "run-drift001",
                "workspace": "prod-us-east",
                "status": "applied",
                "plan_summary": "+0 ~1 -1",
                "message": "Removed unused REDIS_URL variable",
                "created_at": "2026-07-22T16:14:00Z",
            }
        ],
    },
    "argocd": {
        "apps": [
            {"name": "inventory-svc", "status": "Degraded", "health": "Degraded",
             "sync": "OutOfSync", "revision": "drift001", "message": "Config mismatch detected"},
            {"name": "checkout-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "chk001", "message": ""},
        ],
    },
    "traces": {
        "traces": [
            {
                "trace_id": "drift999",
                "service": "inventory-svc",
                "operation": "GET /api/inventory/check",
                "duration_ms": 5,
                "status": "ERROR",
                "spans": [
                    {"service": "inventory-svc", "operation": "config.load", "duration_ms": 2, "error": True},
                ],
            }
        ],
    },
}


# --------------------------------------------------------------------------- #
# Scenario 5: false-alarm
# High CPU from expected batch job — agent should NOT escalate
# Root cause: This is NORMAL behavior, not an incident
# --------------------------------------------------------------------------- #
_FALSE_ALARM = {
    "pagerduty": {
        "incident": {
            "id": "PINC8000",
            "number": 8000,
            "title": "analytics-svc CPU > 90%",
            "status": "triggered",
            "urgency": "low",
            "service": "analytics-svc",
            "created_at": "2026-07-24T03:05:00Z",
            "html_url": "https://acme.pagerduty.com/incidents/PINC8000",
            "description": "analytics-svc CPU utilization exceeded 90% threshold.",
        },
        "alerts": [
            {
                "id": "PALERT5",
                "status": "triggered",
                "summary": "analytics-svc CPU > 90%",
                "created_at": "2026-07-24T03:05:00Z",
                "body": "analytics-svc CPU utilization at 94%. Triggered at 03:05 UTC.",
            }
        ],
    },
    "kubernetes": {
        "pods": [
            {
                "name": "analytics-svc-batch-job-72h", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "2h", "node": "ip-10-0-5-31",
                "reason": "Scheduled batch job (runs daily at 03:00 UTC)",
            },
            {
                "name": "analytics-svc-api-1a2b3c", "namespace": "default", "status": "Running",
                "ready": "1/1", "restarts": 0, "age": "5d", "node": "ip-10-0-5-32",
                "reason": "",
            },
        ],
        "events": [
            {"type": "Normal", "reason": "Scheduled", "object": "pod/analytics-svc-batch-job-72h",
             "age": "2h", "message": "Successfully scheduled batch job"},
            {"type": "Normal", "reason": "Started", "object": "pod/analytics-svc-batch-job-72h",
             "age": "2h", "message": "Started container analytics-batch"},
        ],
        "deployment": {
            "name": "analytics-svc", "namespace": "default",
            "desired": 2, "ready": 2, "available": 2, "unavailable": 0, "updated": 2,
            "image": "analytics-svc:1.5.0",
            "conditions": [
                {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
            ],
            "rollout": "healthy — batch job running as scheduled",
        },
        "rollout_history": [
            {"revision": 1, "image": "analytics-svc:1.5.0", "change_cause": "Weekly analytics batch job"},
        ],
    },
    "github": {
        "commits": [],  # No recent commits
        "diff_none": "",
    },
    "sentry": {
        "issues": [],  # No errors
        "latest_event": None,
    },
    "datadog": {
        "services": ["analytics-svc", "checkout-svc", "inventory-svc"],
        "error_summary": {
            "service": "analytics-svc",
            "total_errors": 0,
            "distinct_errors": 0,
            "breakdown": [],
        },
        "metrics": {
            "analytics-svc": {
                "cpu_percent": [
                    {"ts": 1720000000, "value": 15},  # Before batch
                    {"ts": 1720003600, "value": 92},  # Batch running (03:00)
                    {"ts": 1720007200, "value": 94},  # Still running
                ],
                "error_rate_5xx": [
                    {"ts": 1720000000, "value": 0.0},
                    {"ts": 1720003600, "value": 0.0},
                    {"ts": 1720007200, "value": 0.0},
                ],
            },
        },
    },
    "argocd": {
        "apps": [
            {"name": "analytics-svc", "status": "Healthy", "health": "Healthy",
             "sync": "Synced", "revision": "analytics01", "message": ""},
        ],
    },
    "traces": {
        "traces": [
            {
                "trace_id": "batch001",
                "service": "analytics-svc",
                "operation": "batch/daily-aggregation",
                "duration_ms": 7200000,  # 2 hours - expected
                "status": "OK",
                "spans": [
                    {"service": "analytics-svc", "operation": "aggregate_daily", "duration_ms": 7200000, "error": False},
                ],
            }
        ],
    },
    "cron_jobs": {
        "jobs": [
            {
                "name": "daily-analytics-batch",
                "schedule": "0 3 * * *",  # Every day at 03:00 UTC
                "last_run": "2026-07-24T03:00:00Z",
                "status": "running",
                "expected_duration": "2-3 hours",
            }
        ],
    },
}


# --------------------------------------------------------------------------- #
# Master fixtures registry
# --------------------------------------------------------------------------- #
_FIXTURES: dict[str, dict[str, dict[str, Any]]] = {
    "checkout-discount": _CHECKOUT_DISCOUNT,
    "memory-leak": _MEMORY_LEAK,
    "cascading-failure": _CASCADING_FAILURE,
    "config-drift": _CONFIG_DRIFT,
    "false-alarm": _FALSE_ALARM,
}
