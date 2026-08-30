"""Deploys / change-events MCP server — "what shipped, and when".

~80% of incidents trace to a change, so a first-class deploy timeline is one of
the highest-ROI RCA signals: the agent can line up an error onset against the
deploy that immediately preceded it. When `DEPLOYS_API_URL` is set it queries a
deploy-tracker (Spinnaker/ArgoCD/CD webhook store — Jaeger-style JSON); otherwise
it serves offline fixtures tied to the bundled checkout-svc incident (the 1.8.0
discount rollout). Same live/offline pattern as the other connectors.

Tools:
  - list_deploys:        recent deploys (filter by service)
  - get_deploy:          one deploy's details (version, sha, status, change-cause)
  - deploys_in_window:   deploys between two ISO timestamps (onset correlation)

Run standalone:
    python -m app.mcp.servers.deploys.server
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

DEPLOYS_API_URL = os.environ.get("DEPLOYS_API_URL", "").strip()
OFFLINE = not DEPLOYS_API_URL

mcp = FastMCP("deploys")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mcp_timeout() -> float:
    """Deadline for the outbound deploy-tracker API calls (HTTP_TIMEOUT_MCP).

    Read per call rather than frozen at import: tests monkeypatch the env, and a
    long-lived server process should honour a value set after it started.
    """
    try:
        return float(os.environ.get("HTTP_TIMEOUT_MCP", "") or 20.0)
    except ValueError:
        # A malformed override must not take the timeout to infinity.
        return 20.0


def _demo(payload):
    """Label fixture data so the agent can't mistake it for a live deploy read.

    Offline responses were shaped exactly like live ones, so a deploy timeline
    built on invented rollouts looked identical to one built on real CD history.
    """
    from app.mcp.fixtures import mark_demo
    return mark_demo(payload, "deploys")


def _gate() -> dict | None:
    """Fail closed when no deploy tracker is configured outside demo mode:
    fixtures are a demo feature, not a production fallback (app/config.py's
    contract)."""
    from app.mcp.fixtures import offline_gate
    return offline_gate("Deploys", "DEPLOYS_API_URL")


# --- Offline fixtures (multi-scenario support) --- #
def _get_deploys() -> list[dict[str, Any]]:
    from app.mcp.fixtures import get_fixture
    deploys = get_fixture("deploys", "deploys")
    return deploys if deploys else _FALLBACK_DEPLOYS


_FALLBACK_DEPLOYS: list[dict[str, Any]] = [
    {
        "id": "dep-9001", "service": "checkout-svc", "version": "1.8.0", "sha": "abc1234",
        "status": "succeeded", "env": "production", "deployer": "ci-bot",
        "deployed_at": "2026-06-23T09:58:30Z",
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
]


def _headers() -> dict:
    token = os.environ.get("DEPLOYS_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


@mcp.tool()
def list_deploys(service: str | None = None, limit: int | str = 10) -> list[dict]:
    """List recent deploys (newest first), optionally filtered by service."""
    limit = _as_int(limit, 10)
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        rows = [d for d in _get_deploys() if not service or d["service"] == service]
        return _demo(sorted(rows, key=lambda d: d["deployed_at"], reverse=True)[:limit])

    import httpx

    params: dict = {"limit": limit}
    if service:
        params["service"] = service
    resp = httpx.get(f"{DEPLOYS_API_URL}/deploys", headers=_headers(), params=params, timeout=_mcp_timeout())
    resp.raise_for_status()
    return resp.json().get("deploys", [])[:limit]


@mcp.tool()
def get_deploy(deploy_id: str) -> dict:
    """Return one deploy's details by id."""
    if OFFLINE:
        err = _gate()
        if err:
            return err
        found = next((d for d in _get_deploys() if d["id"] == deploy_id), None)
        return _demo(found) if found else {"error": f"unknown deploy '{deploy_id}'"}

    import httpx

    resp = httpx.get(f"{DEPLOYS_API_URL}/deploys/{deploy_id}", headers=_headers(), timeout=_mcp_timeout())
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def deploys_in_window(start: str, end: str, service: str | None = None) -> list[dict]:
    """Deploys with deployed_at in [start, end] (ISO-8601 UTC) — line these up
    against an error onset to find the change that most likely caused an incident."""
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        return _demo(sorted(
            [d for d in _get_deploys()
             if (not service or d["service"] == service) and start <= d["deployed_at"] <= end],
            key=lambda d: d["deployed_at"], reverse=True,
        ))

    import httpx

    params: dict = {"start": start, "end": end}
    if service:
        params["service"] = service
    resp = httpx.get(f"{DEPLOYS_API_URL}/deploys", headers=_headers(), params=params, timeout=_mcp_timeout())
    resp.raise_for_status()
    return [d for d in resp.json().get("deploys", []) if start <= d.get("deployed_at", "") <= end]


if __name__ == "__main__":
    mcp.run(transport="stdio")
