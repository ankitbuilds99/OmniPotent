"""ArgoCD MCP server — GitOps deployment management and sync status.

Provides tools for interacting with ArgoCD applications: listing apps, checking
sync status, viewing deployment history, and triggering sync/rollback operations.
When `ARGOCD_AUTH_TOKEN` is set it talks to the real ArgoCD API; otherwise it runs
in OFFLINE DEMO mode with fixtures that tie into the bundled checkout-svc incident
— the same live/offline pattern as the other MCP servers.

Read tools (allow): list_applications, get_application, get_application_history,
get_application_resources, get_sync_status.
Write tools (gated via app/policy.py -> human approval): sync_application,
rollback_application.

Run standalone for debugging:
    ARGOCD_SERVER_URL=https://argocd.example.com ARGOCD_AUTH_TOKEN=... \
    python -m app.mcp.servers.argocd.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

ARGOCD_SERVER_URL = os.environ.get("ARGOCD_SERVER_URL", "").strip().rstrip("/")
ARGOCD_AUTH_TOKEN = os.environ.get("ARGOCD_AUTH_TOKEN", "").strip()
OFFLINE = not ARGOCD_AUTH_TOKEN or not ARGOCD_SERVER_URL

mcp = FastMCP("argocd")


def _as_int(value, default: int) -> int:
    """Coerce a tool argument to int; LLMs often send numbers as strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict:
    """Build headers for ArgoCD API requests."""
    return {
        "Authorization": f"Bearer {ARGOCD_AUTH_TOKEN}",
        "Content-Type": "application/json",
    }


# --------------------------------------------------------------------------- #
# Offline fixtures — multi-scenario support via centralized fixture system
# --------------------------------------------------------------------------- #
def _get_apps() -> list:
    from app.mcp.fixtures import get_fixture
    apps = get_fixture("argocd", "apps")
    if apps:
        return apps
    return _FALLBACK_APPS


_FALLBACK_APPS = [
    {
        "name": "checkout-svc",
        "namespace": "argocd",
        "project": "default",
        "repo_url": "https://github.com/acme/checkout-svc.git",
        "path": "deploy/k8s",
        "target_revision": "main",
        "sync_status": "OutOfSync",
        "health_status": "Degraded",
        "operation_state": None,
        "created_at": "2026-05-01T10:00:00Z",
    },
    {
        "name": "inventory-svc",
        "namespace": "argocd",
        "project": "default",
        "repo_url": "https://github.com/acme/inventory-svc.git",
        "path": "deploy/k8s",
        "target_revision": "main",
        "sync_status": "Synced",
        "health_status": "Healthy",
        "operation_state": None,
        "created_at": "2026-04-15T08:30:00Z",
    },
    {
        "name": "payment-svc",
        "namespace": "argocd",
        "project": "default",
        "repo_url": "https://github.com/acme/payment-svc.git",
        "path": "deploy/k8s",
        "target_revision": "main",
        "sync_status": "Synced",
        "health_status": "Healthy",
        "operation_state": None,
        "created_at": "2026-04-10T14:20:00Z",
    },
]

_DEMO_APPLICATION_DETAIL = {
    "name": "checkout-svc",
    "namespace": "argocd",
    "project": "default",
    "repo_url": "https://github.com/acme/checkout-svc.git",
    "path": "deploy/k8s",
    "target_revision": "main",
    "sync_status": "OutOfSync",
    "health_status": "Degraded",
    "sync_revision": "abc1234",
    "target_revision_resolved": "def5678",
    "operation_state": {
        "phase": "Failed",
        "message": "one or more synchronization tasks completed unsuccessfully",
        "sync_result": {
            "revision": "abc1234",
            "resources": [
                {"kind": "Deployment", "name": "checkout-svc", "status": "SyncFailed",
                 "message": "Deployment.apps checkout-svc is invalid: spec.template.spec.containers[0].image: Required value"},
            ],
        },
    },
    "conditions": [
        {"type": "SyncError", "message": "Failed to sync: one or more objects failed to apply",
         "last_transition_time": "2026-06-23T14:10:00Z"},
    ],
    "created_at": "2026-05-01T10:00:00Z",
}

_DEMO_HISTORY = [
    {
        "id": 3,
        "revision": "abc1234",
        "deployed_at": "2026-06-23T14:05:00Z",
        "source": {"repo_url": "https://github.com/acme/checkout-svc.git", "path": "deploy/k8s", "target_revision": "main"},
        "deploy_started_at": "2026-06-23T14:04:30Z",
        "deploy_message": "Add percentage discount support to checkout (commit abc1234)",
    },
    {
        "id": 2,
        "revision": "9f8e7d6",
        "deployed_at": "2026-06-22T16:15:00Z",
        "source": {"repo_url": "https://github.com/acme/checkout-svc.git", "path": "deploy/k8s", "target_revision": "main"},
        "deploy_started_at": "2026-06-22T16:14:00Z",
        "deploy_message": "Refactor cart total calculation",
    },
    {
        "id": 1,
        "revision": "1122334",
        "deployed_at": "2026-06-21T10:00:00Z",
        "source": {"repo_url": "https://github.com/acme/checkout-svc.git", "path": "deploy/k8s", "target_revision": "main"},
        "deploy_started_at": "2026-06-21T09:58:00Z",
        "deploy_message": "Bump dependencies",
    },
]

_DEMO_RESOURCES = [
    {"kind": "Deployment", "name": "checkout-svc", "namespace": "default",
     "status": "OutOfSync", "health": "Degraded", "version": "v1", "group": "apps",
     "message": "Deployment replicas: 1/3 available"},
    {"kind": "Service", "name": "checkout-svc", "namespace": "default",
     "status": "Synced", "health": "Healthy", "version": "v1", "group": "",
     "message": ""},
    {"kind": "ConfigMap", "name": "checkout-svc-config", "namespace": "default",
     "status": "Synced", "health": "Healthy", "version": "v1", "group": "",
     "message": ""},
    {"kind": "HorizontalPodAutoscaler", "name": "checkout-svc-hpa", "namespace": "default",
     "status": "Synced", "health": "Healthy", "version": "v2", "group": "autoscaling",
     "message": ""},
]


async def _get(path: str, params: dict | None = None) -> dict:
    """Make an async GET request to the ArgoCD API."""
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{ARGOCD_SERVER_URL}{path}",
            headers=_headers(),
            params=params,
        )
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, json_body: dict | None = None) -> dict:
    """Make an async POST request to the ArgoCD API."""
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ARGOCD_SERVER_URL}{path}",
            headers=_headers(),
            json=json_body or {},
        )
        resp.raise_for_status()
        return resp.json()


async def _put(path: str, json_body: dict | None = None) -> dict:
    """Make an async PUT request to the ArgoCD API."""
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{ARGOCD_SERVER_URL}{path}",
            headers=_headers(),
            json=json_body or {},
        )
        resp.raise_for_status()
        return resp.json()


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
@mcp.tool()
async def list_applications(project: str = "", health_status: str = "", sync_status: str = "") -> list[dict]:
    """List all ArgoCD applications with their sync and health status.

    Args:
        project: filter by ArgoCD project name (optional).
        health_status: filter by health (Healthy, Progressing, Degraded, Suspended, Missing, Unknown).
        sync_status: filter by sync status (Synced, OutOfSync, Unknown).
    """
    if OFFLINE:
        apps = list(_get_apps())
        if project:
            apps = [a for a in apps if a.get("project") == project]
        if health_status:
            apps = [a for a in apps if a.get("health_status", "").lower() == health_status.lower()]
        if sync_status:
            apps = [a for a in apps if a.get("sync_status", "").lower() == sync_status.lower()]
        return apps

    params = {}
    if project:
        params["project"] = project
    data = await _get("/api/v1/applications", params=params if params else None)
    apps = []
    for item in data.get("items", []):
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        source = spec.get("source", {})
        status = item.get("status", {})
        sync = status.get("sync", {})
        health = status.get("health", {})

        app = {
            "name": metadata.get("name"),
            "namespace": metadata.get("namespace"),
            "project": spec.get("project"),
            "repo_url": source.get("repoURL"),
            "path": source.get("path"),
            "target_revision": source.get("targetRevision"),
            "sync_status": sync.get("status"),
            "health_status": health.get("status"),
            "created_at": metadata.get("creationTimestamp"),
        }

        # Apply filters
        if health_status and (app.get("health_status") or "").lower() != health_status.lower():
            continue
        if sync_status and (app.get("sync_status") or "").lower() != sync_status.lower():
            continue
        apps.append(app)

    return apps


@mcp.tool()
async def get_application(name: str) -> dict:
    """Get detailed information about an ArgoCD application including sync status,
    health, operation state, and any error conditions.

    Args:
        name: the application name.
    """
    if OFFLINE:
        if name == "checkout-svc":
            return dict(_DEMO_APPLICATION_DETAIL)
        matching = [a for a in _get_apps() if a.get("name") == name]
        if matching:
            return matching[0]
        return {"error": f"application '{name}' not found (offline demo)",
                "available": [a["name"] for a in _get_apps()]}

    data = await _get(f"/api/v1/applications/{name}")
    metadata = data.get("metadata", {})
    spec = data.get("spec", {})
    source = spec.get("source", {})
    status = data.get("status", {})
    sync = status.get("sync", {})
    health = status.get("health", {})
    operation = status.get("operationState")

    result = {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "project": spec.get("project"),
        "repo_url": source.get("repoURL"),
        "path": source.get("path"),
        "target_revision": source.get("targetRevision"),
        "sync_status": sync.get("status"),
        "sync_revision": sync.get("revision"),
        "health_status": health.get("status"),
        "health_message": health.get("message"),
        "conditions": [
            {"type": c.get("type"), "message": c.get("message"),
             "last_transition_time": c.get("lastTransitionTime")}
            for c in status.get("conditions", [])
        ],
        "created_at": metadata.get("creationTimestamp"),
    }

    if operation:
        result["operation_state"] = {
            "phase": operation.get("phase"),
            "message": operation.get("message"),
            "started_at": operation.get("startedAt"),
            "finished_at": operation.get("finishedAt"),
        }
        sync_result = operation.get("syncResult")
        if sync_result:
            result["operation_state"]["sync_result"] = {
                "revision": sync_result.get("revision"),
                "resources": [
                    {"kind": r.get("kind"), "name": r.get("name"),
                     "status": r.get("status"), "message": r.get("message")}
                    for r in sync_result.get("resources", [])
                ],
            }

    return result


@mcp.tool()
async def get_application_history(name: str, max_count: int | str = 10) -> list[dict]:
    """Get deployment history for an ArgoCD application — the revisions that have
    been deployed, when, and what changed. Essential for correlating a bad deploy
    with an incident onset.

    Args:
        name: the application name.
        max_count: maximum number of history entries to return (default 10).
    """
    max_count = _as_int(max_count, 10)

    if OFFLINE:
        if name != "checkout-svc":
            return [{"error": f"application '{name}' not found (offline demo has 'checkout-svc')"}]
        return _DEMO_HISTORY[:max_count]

    data = await _get(f"/api/v1/applications/{name}")
    history = data.get("status", {}).get("history", [])

    result = []
    for entry in history[-max_count:]:
        source = entry.get("source", {})
        result.append({
            "id": entry.get("id"),
            "revision": entry.get("revision"),
            "deployed_at": entry.get("deployedAt"),
            "deploy_started_at": entry.get("deployStartedAt"),
            "source": {
                "repo_url": source.get("repoURL"),
                "path": source.get("path"),
                "target_revision": source.get("targetRevision"),
            },
        })

    # Sort by id descending (newest first)
    result.sort(key=lambda x: x.get("id", 0), reverse=True)
    return result


@mcp.tool()
async def get_application_resources(name: str) -> list[dict]:
    """Get the Kubernetes resources managed by an ArgoCD application — deployments,
    services, configmaps, etc. Shows sync status and health of each resource,
    pinpointing which specific resource is out of sync or unhealthy.

    Args:
        name: the application name.
    """
    if OFFLINE:
        if name != "checkout-svc":
            return [{"error": f"application '{name}' not found (offline demo has 'checkout-svc')"}]
        return list(_DEMO_RESOURCES)

    data = await _get(f"/api/v1/applications/{name}")
    resources = data.get("status", {}).get("resources", [])

    return [
        {
            "kind": r.get("kind"),
            "name": r.get("name"),
            "namespace": r.get("namespace"),
            "group": r.get("group", ""),
            "version": r.get("version"),
            "status": r.get("status"),
            "health": r.get("health", {}).get("status") if isinstance(r.get("health"), dict) else r.get("health"),
            "message": r.get("health", {}).get("message", "") if isinstance(r.get("health"), dict) else "",
        }
        for r in resources
    ]


@mcp.tool()
async def get_sync_status(name: str) -> dict:
    """Check if an ArgoCD application is synced or out-of-sync with its Git source.
    Returns the current sync state, revision info, and any sync errors.

    Args:
        name: the application name.
    """
    if OFFLINE:
        if name == "checkout-svc":
            return {
                "name": "checkout-svc",
                "sync_status": "OutOfSync",
                "health_status": "Degraded",
                "sync_revision": "abc1234",
                "target_revision": "main",
                "target_revision_resolved": "def5678",
                "is_synced": False,
                "message": "Application is out of sync with the target revision. "
                          "The current deployed revision (abc1234) differs from "
                          "the target (def5678 on main).",
            }
        matching = [a for a in _get_apps() if a.get("name") == name]
        if matching:
            app = matching[0]
            is_synced = app.get("sync_status") == "Synced"
            return {
                "name": name,
                "sync_status": app.get("sync_status"),
                "health_status": app.get("health_status"),
                "is_synced": is_synced,
                "message": "Application is in sync." if is_synced else "Application is out of sync.",
            }
        return {"error": f"application '{name}' not found (offline demo)",
                "available": [a.get("name") for a in _get_apps()]}

    data = await _get(f"/api/v1/applications/{name}")
    status = data.get("status", {})
    sync = status.get("sync", {})
    health = status.get("health", {})
    spec = data.get("spec", {})
    source = spec.get("source", {})

    sync_status = sync.get("status", "Unknown")
    is_synced = sync_status == "Synced"

    result = {
        "name": name,
        "sync_status": sync_status,
        "health_status": health.get("status"),
        "sync_revision": sync.get("revision"),
        "target_revision": source.get("targetRevision"),
        "is_synced": is_synced,
    }

    if is_synced:
        result["message"] = "Application is in sync with the target revision."
    else:
        result["message"] = (
            f"Application is {sync_status}. The deployed revision may differ from "
            f"the target ({source.get('targetRevision', 'unknown')})."
        )
        # Include comparison info if available
        comparison = sync.get("comparedTo", {})
        if comparison:
            result["compared_to"] = {
                "source_revision": comparison.get("source", {}).get("targetRevision"),
                "destination": comparison.get("destination", {}),
            }

    return result


# --------------------------------------------------------------------------- #
# Write tools — gated via app/policy.py (human approval)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def sync_application(name: str, revision: str = "", prune: bool = False, dry_run: bool = False) -> dict:
    """(WRITE) Trigger a sync for an ArgoCD application — deploys the current target
    revision from Git to the cluster. Requires human approval.

    Args:
        name: the application name.
        revision: specific revision to sync to (optional, defaults to target revision).
        prune: delete resources that are no longer defined in Git (default False).
        dry_run: simulate the sync without making changes (default False).
    """
    if OFFLINE:
        return {
            "status": "sync triggered (simulated - offline demo, no real sync performed)",
            "application": name,
            "revision": revision or "HEAD",
            "prune": prune,
            "dry_run": dry_run,
            "message": "In offline mode, no actual sync is performed. In live mode, this "
                      "would trigger ArgoCD to synchronize the application with the Git source.",
        }

    # Build sync request body
    sync_options = []
    if prune:
        sync_options.append("Prune=true")
    if dry_run:
        sync_options.append("DryRun=true")

    body: dict = {"prune": prune, "dryRun": dry_run}
    if revision:
        body["revision"] = revision
    if sync_options:
        body["syncOptions"] = sync_options

    data = await _post(f"/api/v1/applications/{name}/sync", body)

    operation = data.get("status", {}).get("operationState", {})
    return {
        "status": "sync triggered",
        "application": name,
        "phase": operation.get("phase", "Running"),
        "message": operation.get("message", "Sync operation initiated"),
        "revision": revision or data.get("spec", {}).get("source", {}).get("targetRevision"),
        "prune": prune,
        "dry_run": dry_run,
    }


@mcp.tool()
async def rollback_application(name: str, revision: int | str) -> dict:
    """(WRITE) Rollback an ArgoCD application to a previous deployment revision.
    Use get_application_history to find valid revision IDs. Requires human approval.

    Args:
        name: the application name.
        revision: the history ID to rollback to (from get_application_history).
    """
    revision_id = _as_int(revision, -1)
    if revision_id < 0:
        return {"error": "revision must be a valid history ID (positive integer)"}

    if OFFLINE:
        # Find the revision in demo history
        matching = [h for h in _DEMO_HISTORY if h.get("id") == revision_id]
        if name != "checkout-svc":
            return {"error": f"application '{name}' not found (offline demo has 'checkout-svc')"}
        if not matching:
            return {"error": f"revision {revision_id} not found in history",
                    "available_revisions": [h["id"] for h in _DEMO_HISTORY]}
        target = matching[0]
        return {
            "status": "rollback triggered (simulated - offline demo, no real rollback performed)",
            "application": name,
            "target_revision_id": revision_id,
            "target_git_revision": target.get("revision"),
            "message": f"In offline mode, no actual rollback is performed. In live mode, this "
                      f"would roll back to revision {target.get('revision')} "
                      f"(deployed at {target.get('deployed_at')}).",
        }

    # ArgoCD rollback is done via sync to a specific revision from history
    # First, get the application to find the git revision for the history ID
    app_data = await _get(f"/api/v1/applications/{name}")
    history = app_data.get("status", {}).get("history", [])

    target_entry = None
    for entry in history:
        if entry.get("id") == revision_id:
            target_entry = entry
            break

    if not target_entry:
        return {
            "error": f"revision {revision_id} not found in application history",
            "available_revisions": [e.get("id") for e in history],
        }

    git_revision = target_entry.get("revision")

    # Trigger sync to the specific revision
    body = {"revision": git_revision, "prune": False}
    data = await _post(f"/api/v1/applications/{name}/sync", body)

    operation = data.get("status", {}).get("operationState", {})
    return {
        "status": "rollback triggered",
        "application": name,
        "target_revision_id": revision_id,
        "target_git_revision": git_revision,
        "phase": operation.get("phase", "Running"),
        "message": f"Rolling back to revision {git_revision} (history ID {revision_id})",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
