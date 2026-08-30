"""Terraform Cloud/Enterprise MCP server.

Gives the agent infrastructure-as-code context: workspace state, run history,
plan diffs, and the ability to trigger plans/applies (with human approval). When
`TERRAFORM_CLOUD_TOKEN` is set it calls the real Terraform Cloud API; otherwise
it runs in OFFLINE DEMO mode with fixtures that tie into a sample infrastructure
incident scenario.

Also provides Terraform code generation tools for creating infrastructure from
templates, descriptions, or existing cloud resources.

Tools:
  Read (allow):
    - list_workspaces:        list workspaces in the organization
    - get_workspace:          get workspace details (settings, VCS, last run)
    - get_state:              get current state (managed resources)
    - get_run_history:        get plan/apply history for a workspace
    - get_run_details:        get specific run details
    - get_plan_output:        get plan diff
    - list_state_resources:   list managed resources in current state

  Code Generation (allow):
    - generate_terraform_module:   generate a complete Terraform module
    - generate_backend_config:     generate backend configuration
    - generate_tfvars:             generate variables file from template
    - generate_provider_config:    generate provider configuration
    - list_module_templates:       list available module templates

  Write (gated via app/policy.py -> human approval):
    - trigger_plan:           queue a speculative or normal plan
    - trigger_apply:          apply a run that has a plan

Run standalone for debugging:
    TERRAFORM_CLOUD_TOKEN=... TERRAFORM_ORGANIZATION=... python -m app.mcp.servers.terraform.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

TERRAFORM_CLOUD_TOKEN = os.environ.get("TERRAFORM_CLOUD_TOKEN", "").strip()
TERRAFORM_ORGANIZATION = os.environ.get("TERRAFORM_ORGANIZATION", "").strip()
OFFLINE = not TERRAFORM_CLOUD_TOKEN
BASE_URL = "https://app.terraform.io/api/v2"

mcp = FastMCP("terraform")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {TERRAFORM_CLOUD_TOKEN}",
        "Content-Type": "application/vnd.api+json",
    }


def _demo(payload):
    """Label fixture data so the agent can't mistake it for a live Terraform read.

    With no TERRAFORM_CLOUD_TOKEN — the default for this repo — every read tool
    below answered from the _DEMO_* tables in exactly the shape Terraform Cloud
    uses, with no marker anywhere. An invented workspace list, state serial 42
    and an errored run blaming an ECS health check therefore entered
    investigations as measured infrastructure fact, and the incident report that
    came out the far end looked identical to one built against a real org.

    Marking is what the other connectors already do (argocd, datadog,
    kubernetes, pagerduty, sentry); mark_demo copies rather than mutates, so the
    module-level fixture tables stay clean across calls.
    """
    from app.mcp.fixtures import mark_demo
    return mark_demo(payload, "terraform")


def _demo_notice_comment() -> str:
    """The same marker for tools whose return type has nowhere to hang a key."""
    from app.mcp.fixtures import demo_notice
    return f"# {demo_notice('terraform')}"


def _gate() -> dict | None:
    """Fail closed when no token is set outside demo mode: fixtures are a demo
    feature, not a production fallback (app/config.py's contract)."""
    from app.mcp.fixtures import offline_gate
    return offline_gate("Terraform Cloud", "TERRAFORM_CLOUD_TOKEN")


def _mcp_timeout() -> float:
    """Deadline for the outbound Terraform Cloud API calls (HTTP_TIMEOUT_MCP).

    Read per call rather than frozen at import: tests monkeypatch the env, and a
    long-lived server process should honour a value set after it started.
    """
    try:
        return float(os.environ.get("HTTP_TIMEOUT_MCP", "") or 20.0)
    except ValueError:
        # A malformed override must not take the timeout to infinity.
        return 20.0


# --------------------------------------------------------------------------- #
# Offline fixtures (simulate a checkout-svc infrastructure workspace)
# --------------------------------------------------------------------------- #
_DEMO_WORKSPACES = [
    {
        "id": "ws-checkout-prod",
        "name": "checkout-prod",
        "description": "Production checkout service infrastructure",
        "environment": "production",
        "terraform_version": "1.6.0",
        "auto_apply": False,
        "vcs_repo": "acme/checkout-infra",
        "locked": False,
        "resource_count": 24,
        "run_status": "errored",
        "last_run_at": "2026-06-23T14:00:00Z",
    },
    {
        "id": "ws-checkout-staging",
        "name": "checkout-staging",
        "description": "Staging checkout service infrastructure",
        "environment": "staging",
        "terraform_version": "1.6.0",
        "auto_apply": True,
        "vcs_repo": "acme/checkout-infra",
        "locked": False,
        "resource_count": 18,
        "run_status": "applied",
        "last_run_at": "2026-06-23T12:30:00Z",
    },
    {
        "id": "ws-api-gateway",
        "name": "api-gateway",
        "description": "API Gateway and routing infrastructure",
        "environment": "production",
        "terraform_version": "1.5.7",
        "auto_apply": False,
        "vcs_repo": "acme/gateway-infra",
        "locked": False,
        "resource_count": 12,
        "run_status": "applied",
        "last_run_at": "2026-06-22T09:15:00Z",
    },
]

_DEMO_WORKSPACE_DETAIL = {
    "id": "ws-checkout-prod",
    "name": "checkout-prod",
    "description": "Production checkout service infrastructure",
    "environment": "production",
    "terraform_version": "1.6.0",
    "auto_apply": False,
    "vcs_repo": {
        "identifier": "acme/checkout-infra",
        "branch": "main",
        "oauth_token_id": "ot-xxx",
    },
    "locked": False,
    "locked_reason": None,
    "resource_count": 24,
    "speculative_enabled": True,
    "queue_all_runs": True,
    "working_directory": "terraform/prod",
    "trigger_prefixes": ["modules/", "terraform/prod/"],
    "run_status": "errored",
    "last_run": {
        "id": "run-abc123",
        "status": "errored",
        "created_at": "2026-06-23T14:00:00Z",
        "message": "Triggered by VCS commit abc1234",
    },
    "created_at": "2025-01-15T10:00:00Z",
    "updated_at": "2026-06-23T14:05:00Z",
}

_DEMO_STATE_RESOURCES = [
    {
        "type": "aws_ecs_service",
        "name": "checkout",
        "provider": "aws",
        "module": "module.ecs",
        "attributes": {
            "name": "checkout-svc",
            "cluster": "arn:aws:ecs:us-east-1:123456789:cluster/prod",
            "desired_count": 3,
            "task_definition": "checkout:42",
        },
    },
    {
        "type": "aws_ecs_task_definition",
        "name": "checkout",
        "provider": "aws",
        "module": "module.ecs",
        "attributes": {
            "family": "checkout",
            "revision": 42,
            "cpu": "512",
            "memory": "1024",
            "container_definitions": "[{\"name\":\"checkout\",\"image\":\"checkout-svc:1.8.0\"}]",
        },
    },
    {
        "type": "aws_lb_target_group",
        "name": "checkout",
        "provider": "aws",
        "module": "module.alb",
        "attributes": {
            "name": "checkout-tg",
            "port": 8080,
            "protocol": "HTTP",
            "health_check": {"path": "/health", "interval": 30},
        },
    },
    {
        "type": "aws_rds_cluster",
        "name": "checkout_db",
        "provider": "aws",
        "module": "module.rds",
        "attributes": {
            "cluster_identifier": "checkout-db-prod",
            "engine": "aurora-postgresql",
            "engine_version": "14.9",
            "database_name": "checkout",
        },
    },
    {
        "type": "aws_elasticache_replication_group",
        "name": "checkout_cache",
        "provider": "aws",
        "module": "module.elasticache",
        "attributes": {
            "replication_group_id": "checkout-cache",
            "engine": "redis",
            "engine_version": "7.0",
            "node_type": "cache.r6g.large",
        },
    },
]

_DEMO_RUN_HISTORY = [
    {
        "id": "run-abc123",
        "status": "errored",
        "source": "tfe-ui",
        "trigger_reason": "VCS commit abc1234",
        "message": "Add percentage discount support",
        "created_at": "2026-06-23T14:00:00Z",
        "plan_only": False,
        "has_changes": True,
        "resource_additions": 0,
        "resource_changes": 2,
        "resource_destructions": 0,
        "error_message": "Error: timeout waiting for ECS service to become stable",
    },
    {
        "id": "run-xyz789",
        "status": "applied",
        "source": "tfe-ui",
        "trigger_reason": "VCS commit 9f8e7d6",
        "message": "Refactor ECS task definition",
        "created_at": "2026-06-22T12:00:00Z",
        "plan_only": False,
        "has_changes": True,
        "resource_additions": 0,
        "resource_changes": 1,
        "resource_destructions": 0,
        "error_message": None,
    },
    {
        "id": "run-qrs456",
        "status": "applied",
        "source": "tfe-api",
        "trigger_reason": "Manual trigger",
        "message": "Update RDS engine version",
        "created_at": "2026-06-20T09:30:00Z",
        "plan_only": False,
        "has_changes": True,
        "resource_additions": 0,
        "resource_changes": 1,
        "resource_destructions": 0,
        "error_message": None,
    },
]

_DEMO_RUN_DETAILS = {
    "id": "run-abc123",
    "status": "errored",
    "source": "tfe-ui",
    "trigger_reason": "VCS commit abc1234",
    "message": "Add percentage discount support",
    "created_at": "2026-06-23T14:00:00Z",
    "plan_started_at": "2026-06-23T14:00:30Z",
    "plan_completed_at": "2026-06-23T14:02:00Z",
    "apply_started_at": "2026-06-23T14:02:30Z",
    "apply_completed_at": "2026-06-23T14:05:00Z",
    "plan_only": False,
    "has_changes": True,
    "resource_additions": 0,
    "resource_changes": 2,
    "resource_destructions": 0,
    "error_message": "Error: timeout waiting for ECS service to become stable. The new task "
    "definition (revision 42) failed health checks — checkout-svc:1.8.0 returns 500 on /health.",
    "workspace": {"id": "ws-checkout-prod", "name": "checkout-prod"},
    "configuration_version": {"id": "cv-abc123", "source": "github"},
    "cost_estimate": {"prior_monthly_cost": "1234.56", "proposed_monthly_cost": "1234.56"},
    "policy_checks": [{"status": "passed", "scope": "organization"}],
}

_DEMO_PLAN_OUTPUT = """\
Terraform will perform the following actions:

  # module.ecs.aws_ecs_task_definition.checkout will be updated in-place
  ~ resource "aws_ecs_task_definition" "checkout" {
        id                     = "checkout"
      ~ revision               = 41 -> 42
      ~ container_definitions  = jsonencode(
          ~ [
              ~ {
                  ~ image   = "checkout-svc:1.7.0" -> "checkout-svc:1.8.0"
                    name    = "checkout"
                    # (other attributes unchanged)
                },
            ]
        )
        # (other attributes unchanged)
    }

  # module.ecs.aws_ecs_service.checkout will be updated in-place
  ~ resource "aws_ecs_service" "checkout" {
        id                             = "checkout-svc"
        name                           = "checkout-svc"
      ~ task_definition                = "checkout:41" -> "checkout:42"
        # (other attributes unchanged)
    }

Plan: 0 to add, 2 to change, 0 to destroy.

Changes to Outputs:
  ~ task_definition_revision = 41 -> 42
"""


def _find_workspace(name: str) -> dict | None:
    """Lookup a workspace by name or id in the demo fixtures."""
    for ws in _DEMO_WORKSPACES:
        if ws["name"] == name or ws["id"] == name:
            return ws
    return None


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_workspaces(search: str = "", environment: str = "") -> list[dict]:
    """List Terraform Cloud/Enterprise workspaces in the organization.

    Args:
        search: Filter workspaces by name (substring match).
        environment: Filter by environment tag (e.g. "production", "staging").
    """
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        result = list(_DEMO_WORKSPACES)
        if search:
            result = [ws for ws in result if search.lower() in str(ws["name"]).lower()]
        if environment:
            result = [ws for ws in result if str(ws.get("environment", "")).lower() == environment.lower()]
        return _demo(result)

    import httpx

    params: dict[str, str | int] = {"page[size]": 100}
    if search:
        params["search[name]"] = search

    resp = httpx.get(
        f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces",
        headers=_headers(),
        params=params,
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()

    workspaces = []
    for ws in resp.json().get("data", []):
        attrs = ws.get("attributes", {})
        workspaces.append({
            "id": ws.get("id"),
            "name": attrs.get("name"),
            "description": attrs.get("description"),
            "environment": attrs.get("environment"),
            "terraform_version": attrs.get("terraform-version"),
            "auto_apply": attrs.get("auto-apply"),
            "vcs_repo": (attrs.get("vcs-repo") or {}).get("identifier"),
            "locked": attrs.get("locked"),
            "resource_count": attrs.get("resource-count"),
            "run_status": attrs.get("latest-run", {}).get("status") if attrs.get("latest-run") else None,
        })

    if environment:
        workspaces = [ws for ws in workspaces if (ws.get("environment") or "").lower() == environment.lower()]

    return workspaces


@mcp.tool()
def get_workspace(name: str) -> dict:
    """Get detailed information about a workspace.

    Args:
        name: Workspace name or ID.

    Returns workspace settings, VCS configuration, last run status, and more.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        ws = _find_workspace(name)
        if not ws:
            # The error reply lists the invented workspace names, so it is
            # fixture data too and gets the same marker.
            return _demo({"error": f"workspace '{name}' not found (offline demo)",
                          "available": [w["name"] for w in _DEMO_WORKSPACES]})
        if name == "checkout-prod" or name == "ws-checkout-prod":
            return _demo(dict(_DEMO_WORKSPACE_DETAIL))
        return _demo({**ws, "last_run": None})

    import httpx

    resp = httpx.get(
        f"{BASE_URL}/workspaces/{name}" if name.startswith("ws-") else
        f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{name}",
        headers=_headers(),
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()

    ws = resp.json().get("data", {})
    attrs = ws.get("attributes", {})
    vcs_repo = attrs.get("vcs-repo") or {}
    latest_run = attrs.get("latest-run") or {}

    return {
        "id": ws.get("id"),
        "name": attrs.get("name"),
        "description": attrs.get("description"),
        "environment": attrs.get("environment"),
        "terraform_version": attrs.get("terraform-version"),
        "auto_apply": attrs.get("auto-apply"),
        "vcs_repo": {
            "identifier": vcs_repo.get("identifier"),
            "branch": vcs_repo.get("branch"),
            "oauth_token_id": vcs_repo.get("oauth-token-id"),
        } if vcs_repo else None,
        "locked": attrs.get("locked"),
        "locked_reason": attrs.get("locked-reason"),
        "resource_count": attrs.get("resource-count"),
        "speculative_enabled": attrs.get("speculative-enabled"),
        "queue_all_runs": attrs.get("queue-all-runs"),
        "working_directory": attrs.get("working-directory"),
        "trigger_prefixes": attrs.get("trigger-prefixes"),
        "run_status": latest_run.get("status") if latest_run else None,
        "created_at": attrs.get("created-at"),
        "updated_at": attrs.get("updated-at"),
    }


@mcp.tool()
def get_state(workspace: str) -> dict:
    """Get the current Terraform state for a workspace.

    Args:
        workspace: Workspace name or ID.

    Returns a summary of the current state including resource counts by type.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        ws = _find_workspace(workspace)
        if not ws:
            return _demo({"error": f"workspace '{workspace}' not found (offline demo)",
                          "available": [w["name"] for w in _DEMO_WORKSPACES]})
        if workspace in ("checkout-prod", "ws-checkout-prod"):
            # Count resources by type
            type_counts: dict[str, int] = {}
            for r in _DEMO_STATE_RESOURCES:
                rtype = str(r["type"])
                type_counts[rtype] = type_counts.get(rtype, 0) + 1
            return _demo({
                "workspace": workspace,
                "serial": 42,
                "terraform_version": "1.6.0",
                "resource_count": len(_DEMO_STATE_RESOURCES),
                "resources_by_type": type_counts,
                "outputs": {
                    "service_endpoint": "https://checkout.acme.com",
                    "database_endpoint": "checkout-db-prod.xxx.us-east-1.rds.amazonaws.com",
                },
            })
        return _demo({"workspace": workspace, "serial": 1, "resource_count": ws.get("resource_count", 0),
                      "resources_by_type": {}, "outputs": {}})

    import httpx

    # Get workspace ID if name provided
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=_mcp_timeout(),
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    # Get current state version
    resp = httpx.get(
        f"{BASE_URL}/workspaces/{ws_id}/current-state-version",
        headers=_headers(),
        timeout=_mcp_timeout(),
    )

    if resp.status_code == 404:
        return {"workspace": workspace, "error": "no state exists for this workspace"}

    resp.raise_for_status()
    state = resp.json().get("data", {})
    attrs = state.get("attributes", {})

    return {
        "workspace": workspace,
        "serial": attrs.get("serial"),
        "terraform_version": attrs.get("terraform-version"),
        "resource_count": attrs.get("resource-count"),
        "state_version_id": state.get("id"),
        "created_at": attrs.get("created-at"),
    }


@mcp.tool()
def list_state_resources(workspace: str, resource_type: str = "") -> list[dict]:
    """List managed resources in the current Terraform state.

    Args:
        workspace: Workspace name or ID.
        resource_type: Filter by resource type (e.g. "aws_ecs_service").

    Returns details about each managed resource.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        ws = _find_workspace(workspace)
        if not ws:
            return _demo([{"error": f"workspace '{workspace}' not found (offline demo)",
                           "available": [w["name"] for w in _DEMO_WORKSPACES]}])
        if workspace in ("checkout-prod", "ws-checkout-prod"):
            resources = list(_DEMO_STATE_RESOURCES)
            if resource_type:
                resources = [r for r in resources if r["type"] == resource_type]
            return _demo(resources)
        # The workspace is real in the fixture list but no state was authored
        # for it. A bare [] would read as a measured "this workspace manages
        # nothing"; say the state was never captured instead.
        return _demo([{
            "workspace": workspace,
            "resources": "unmeasured",
            "note": f"no fixture state exists for workspace '{workspace}' in this offline demo; "
                    "this is not a measurement that the workspace has zero resources",
        }])

    import httpx

    # Get workspace ID
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=_mcp_timeout(),
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    # Get current state version
    state_resp = httpx.get(
        f"{BASE_URL}/workspaces/{ws_id}/current-state-version",
        headers=_headers(),
        timeout=_mcp_timeout(),
    )

    if state_resp.status_code == 404:
        return [{"error": "no state exists for this workspace"}]

    state_resp.raise_for_status()
    state_id = state_resp.json().get("data", {}).get("id")

    # Get state resources
    resp = httpx.get(
        f"{BASE_URL}/state-versions/{state_id}/resources",
        headers=_headers(),
        params={"page[size]": 100},
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()

    resources = []
    for res in resp.json().get("data", []):
        attrs = res.get("attributes", {})
        rtype = attrs.get("type")
        if resource_type and rtype != resource_type:
            continue
        resources.append({
            "type": rtype,
            "name": attrs.get("name"),
            "provider": attrs.get("provider"),
            "module": attrs.get("module"),
            "address": attrs.get("address"),
        })

    return resources


@mcp.tool()
def get_run_history(workspace: str, limit: int | str = 10) -> list[dict]:
    """Get plan/apply run history for a workspace.

    Args:
        workspace: Workspace name or ID.
        limit: Maximum number of runs to return.

    Returns recent runs with status, changes, and error messages.
    """
    limit = _as_int(limit, 10)

    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        ws = _find_workspace(workspace)
        if not ws:
            return _demo([{"error": f"workspace '{workspace}' not found (offline demo)",
                           "available": [w["name"] for w in _DEMO_WORKSPACES]}])
        if workspace in ("checkout-prod", "ws-checkout-prod"):
            return _demo(_DEMO_RUN_HISTORY[:limit])
        # Same reasoning as list_state_resources: no run history was authored
        # for this workspace, which is not the same fact as "it has never run".
        return _demo([{
            "workspace": workspace,
            "runs": "unmeasured",
            "note": f"no fixture run history exists for workspace '{workspace}' in this offline "
                    "demo; this is not a measurement that the workspace has never run",
        }])

    import httpx

    # Get workspace ID
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=_mcp_timeout(),
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    resp = httpx.get(
        f"{BASE_URL}/workspaces/{ws_id}/runs",
        headers=_headers(),
        params={"page[size]": limit},
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()

    runs = []
    for run in resp.json().get("data", []):
        attrs = run.get("attributes", {})
        runs.append({
            "id": run.get("id"),
            "status": attrs.get("status"),
            "source": attrs.get("source"),
            "trigger_reason": attrs.get("trigger-reason"),
            "message": attrs.get("message"),
            "created_at": attrs.get("created-at"),
            "plan_only": attrs.get("plan-only"),
            "has_changes": attrs.get("has-changes"),
            "resource_additions": attrs.get("resource-additions"),
            "resource_changes": attrs.get("resource-changes"),
            "resource_destructions": attrs.get("resource-destructions"),
            "error_message": attrs.get("status-message") if attrs.get("status") == "errored" else None,
        })

    return runs


@mcp.tool()
def get_run_details(run_id: str) -> dict:
    """Get detailed information about a specific run.

    Args:
        run_id: The run ID (e.g. "run-abc123").

    Returns run details including timestamps, changes, errors, and cost estimate.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        if run_id == "run-abc123" or run_id == "":
            return _demo(dict(_DEMO_RUN_DETAILS))
        # Check if it's in history
        for run in _DEMO_RUN_HISTORY:
            if run["id"] == run_id:
                return _demo({**run, "workspace": {"id": "ws-checkout-prod", "name": "checkout-prod"}})
        return _demo({"error": f"run '{run_id}' not found (offline demo)",
                      "available": [r["id"] for r in _DEMO_RUN_HISTORY]})

    import httpx

    resp = httpx.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=_headers(),
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()

    run = resp.json().get("data", {})
    attrs = run.get("attributes", {})
    relationships = run.get("relationships", {})

    return {
        "id": run.get("id"),
        "status": attrs.get("status"),
        "source": attrs.get("source"),
        "trigger_reason": attrs.get("trigger-reason"),
        "message": attrs.get("message"),
        "created_at": attrs.get("created-at"),
        "plan_started_at": attrs.get("plan-started-at"),
        "plan_completed_at": attrs.get("plan-completed-at"),
        "apply_started_at": attrs.get("apply-started-at"),
        "apply_completed_at": attrs.get("apply-completed-at"),
        "plan_only": attrs.get("plan-only"),
        "has_changes": attrs.get("has-changes"),
        "resource_additions": attrs.get("resource-additions"),
        "resource_changes": attrs.get("resource-changes"),
        "resource_destructions": attrs.get("resource-destructions"),
        "error_message": attrs.get("status-message") if attrs.get("status") == "errored" else None,
        "workspace": {
            "id": relationships.get("workspace", {}).get("data", {}).get("id"),
        },
    }


def _fetch_plan_log(plan_id: str, run_id: str, why: str) -> str:
    """Read the human-readable plan log when the JSON plan is not available.

    The old fallback fetched the plan record and returned `log-read-url`, i.e.
    the signed archivist URL as a string. That is not a plan diff: the caller is
    an LLM that cannot fetch a URL, so the tool answered a question about what
    would change with a link, and the answer read like success. This follows the
    URL and returns the log body instead, and when a step fails it says which
    endpoint answered what rather than the old flat "Plan output not available".
    """
    import httpx

    plan_resp = httpx.get(
        f"{BASE_URL}/plans/{plan_id}",
        headers=_headers(),
        timeout=_mcp_timeout(),
        follow_redirects=True,
    )
    if plan_resp.status_code != 200:
        return (
            f"Plan output unavailable for run {run_id}: {why}, and the plan record at "
            f"/plans/{plan_id} answered HTTP {plan_resp.status_code}."
        )

    attrs = plan_resp.json().get("data", {}).get("attributes", {})
    log_url = attrs.get("log-read-url")
    if not log_url:
        return (
            f"Plan output unavailable for run {run_id}: {why}, and the plan record for "
            f"{plan_id} carries no log-read-url to fall back to."
        )

    # The log URL is pre-signed and lives on a different origin (archivist), so
    # it is sent without the Terraform Cloud token rather than leaking it.
    log_resp = httpx.get(log_url, timeout=_mcp_timeout(), follow_redirects=True)
    if log_resp.status_code != 200:
        return (
            f"Plan output unavailable for run {run_id}: {why}, and the log-read-url for "
            f"{plan_id} answered HTTP {log_resp.status_code}."
        )
    if not log_resp.text.strip():
        return (
            f"Plan output unavailable for run {run_id}: {why}, and the log-read-url for "
            f"{plan_id} returned an empty body."
        )
    return log_resp.text


@mcp.tool()
def get_plan_output(run_id: str) -> str:
    """Get the plan output (diff) for a run.

    Args:
        run_id: The run ID (e.g. "run-abc123").

    Returns the terraform plan output showing what will change.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err["error"]
        # A bare string has nowhere to hang the _simulated key the dict-returning
        # tools carry, so the notice goes in as a leading comment line — the same
        # convention the datadog server uses for its log text.
        if run_id == "run-abc123" or run_id == "":
            return f"{_demo_notice_comment()}\n{_DEMO_PLAN_OUTPUT}"
        return (
            f"{_demo_notice_comment()}\n"
            f"No fixture plan output exists for run '{run_id}' in this offline demo. "
            "This is not a measurement that the run produced an empty plan."
        )

    import httpx

    # First get the plan ID from the run
    run_resp = httpx.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=_headers(),
        timeout=_mcp_timeout(),
    )
    run_resp.raise_for_status()
    plan_id = run_resp.json().get("data", {}).get("relationships", {}).get("plan", {}).get("data", {}).get("id")

    if not plan_id:
        return f"No plan found for run {run_id}"

    # Terraform Cloud answers the JSON-plan endpoint with a 307 to a temporary
    # archivist URL, and that redirect *is* the documented success path. httpx
    # does not follow redirects by default and raise_for_status() rejects a 3xx,
    # so this call used to raise HTTPStatusError on every live run and only the
    # offline fixture path ever returned a plan. httpx drops the Authorization
    # header when a redirect crosses origins, which is what we want: the
    # archivist URL is pre-signed and must not receive the org token.
    resp = httpx.get(
        f"{BASE_URL}/plans/{plan_id}/json-output",
        headers=_headers(),
        timeout=_mcp_timeout(),
        follow_redirects=True,
    )

    # 204 means the plan is still running. It is a success status, so the old
    # code sailed past raise_for_status and returned "" — an empty diff, which
    # reads as "nothing will change" instead of "not measured yet".
    if resp.status_code == 204:
        return (
            f"Plan output for run {run_id} is not ready: Terraform Cloud answered HTTP 204 "
            f"for plan {plan_id}, meaning the plan is still running. Retry once the run "
            "leaves the planning phase."
        )

    # 403/404/422 are the documented "no JSON plan for this run" answers (422 is
    # returned for pre-0.12 Terraform). Those fall back to the text log; anything
    # else that failed is a genuine error and still raises.
    if resp.status_code in (403, 404, 422):
        return _fetch_plan_log(
            plan_id,
            run_id,
            f"the JSON plan endpoint answered HTTP {resp.status_code}",
        )

    resp.raise_for_status()
    if not resp.text.strip():
        return (
            f"Plan output for run {run_id} could not be read: the JSON plan endpoint for "
            f"{plan_id} answered HTTP {resp.status_code} with an empty body."
        )
    return resp.text


# --------------------------------------------------------------------------- #
# Write tools (gated via app/policy.py -> human approval)
# --------------------------------------------------------------------------- #
@mcp.tool()
def trigger_plan(workspace: str, message: str = "", speculative: bool = False) -> dict:
    """(WRITE) Trigger a Terraform plan on a workspace. Requires human approval.

    Args:
        workspace: Workspace name or ID.
        message: Optional message describing why the plan is being triggered.
        speculative: If true, run a speculative plan (no state changes possible).

    Returns the new run ID and status.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        ws = _find_workspace(workspace)
        if not ws:
            return _demo({"error": f"workspace '{workspace}' not found (offline demo)",
                          "available": [w["name"] for w in _DEMO_WORKSPACES]})
        # The prose already said "(simulated ...)"; the marker means a consumer
        # never has to string-match to find that out.
        return _demo({
            "status": "plan queued (simulated - offline demo, no actual plan triggered)",
            "run_id": "run-demo-new",
            "workspace": workspace,
            "speculative": speculative,
            "message": message,
        })

    import httpx

    # Get workspace ID
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=_mcp_timeout(),
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    # Create a new run
    payload = {
        "data": {
            "type": "runs",
            "attributes": {
                "message": message or "Triggered by OmniPotent",
                "plan-only": speculative,
            },
            "relationships": {
                "workspace": {
                    "data": {
                        "type": "workspaces",
                        "id": ws_id,
                    }
                }
            }
        }
    }

    resp = httpx.post(
        f"{BASE_URL}/runs",
        headers=_headers(),
        json=payload,
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()

    run = resp.json().get("data", {})
    return {
        "status": "plan queued",
        "run_id": run.get("id"),
        "workspace": workspace,
        "speculative": speculative,
    }


@mcp.tool()
def trigger_apply(run_id: str, comment: str = "") -> dict:
    """(WRITE) Apply a Terraform run that has a successful plan. Requires human approval.

    Args:
        run_id: The run ID to apply (e.g. "run-abc123").
        comment: Optional comment explaining why the apply is being triggered.

    Returns the run status after triggering apply.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        if run_id not in [r["id"] for r in _DEMO_RUN_HISTORY] and run_id != "run-abc123":
            return _demo({"error": f"run '{run_id}' not found (offline demo)",
                          "available": [r["id"] for r in _DEMO_RUN_HISTORY]})
        return _demo({
            "status": "apply queued (simulated - offline demo, no actual apply triggered)",
            "run_id": run_id,
            "comment": comment,
        })

    import httpx

    # First verify the run is in an applyable state
    run_resp = httpx.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=_headers(),
        timeout=_mcp_timeout(),
    )
    run_resp.raise_for_status()

    run = run_resp.json().get("data", {})
    status = run.get("attributes", {}).get("status")

    if status not in ("planned", "policy_checked", "cost_estimated"):
        return {
            "error": f"Run {run_id} is not in an applyable state",
            "current_status": status,
            "applyable_states": ["planned", "policy_checked", "cost_estimated"],
        }

    # Trigger the apply
    payload = {
        "comment": comment or "Applied by OmniPotent",
    }

    resp = httpx.post(
        f"{BASE_URL}/runs/{run_id}/actions/apply",
        headers=_headers(),
        json=payload,
        timeout=_mcp_timeout(),
    )

    if resp.status_code == 409:
        return {"error": "Run cannot be applied in its current state", "run_id": run_id}

    resp.raise_for_status()

    return {
        "status": "apply triggered",
        "run_id": run_id,
    }


# --------------------------------------------------------------------------- #
# Terraform Code Generation Templates
# --------------------------------------------------------------------------- #
MODULE_TEMPLATES: dict[str, dict] = {
    "aws-vpc": {
        "name": "AWS VPC",
        "description": "Complete VPC with public/private subnets, NAT gateway, and routing",
        "provider": "aws",
        "variables": ["vpc_cidr", "environment", "availability_zones", "enable_nat_gateway"],
        "resources": ["aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_nat_gateway", "aws_route_table"],
    },
    "aws-ecs-fargate": {
        "name": "AWS ECS Fargate",
        "description": "ECS Fargate cluster with ALB, auto-scaling, and service definition",
        "provider": "aws",
        "variables": ["cluster_name", "service_name", "container_image", "cpu", "memory", "desired_count"],
        "resources": ["aws_ecs_cluster", "aws_ecs_service", "aws_ecs_task_definition", "aws_lb", "aws_lb_target_group"],
    },
    "aws-rds-postgres": {
        "name": "AWS RDS PostgreSQL",
        "description": "RDS PostgreSQL instance with subnet group and security group",
        "provider": "aws",
        "variables": ["identifier", "engine_version", "instance_class", "allocated_storage", "db_name", "username"],
        "resources": ["aws_db_instance", "aws_db_subnet_group", "aws_security_group"],
    },
    "aws-lambda": {
        "name": "AWS Lambda Function",
        "description": "Lambda function with IAM role, CloudWatch logs, and optional VPC",
        "provider": "aws",
        "variables": ["function_name", "runtime", "handler", "memory_size", "timeout"],
        "resources": ["aws_lambda_function", "aws_iam_role", "aws_iam_role_policy", "aws_cloudwatch_log_group"],
    },
    "aws-s3-static": {
        "name": "AWS S3 Static Website",
        "description": "S3 bucket configured for static website hosting with CloudFront",
        "provider": "aws",
        "variables": ["bucket_name", "domain_name", "enable_cloudfront"],
        "resources": ["aws_s3_bucket", "aws_s3_bucket_policy", "aws_cloudfront_distribution"],
    },
    "aws-eks": {
        "name": "AWS EKS Cluster",
        "description": "EKS cluster with managed node groups and IRSA support",
        "provider": "aws",
        "variables": ["cluster_name", "kubernetes_version", "node_instance_types", "node_desired_size"],
        "resources": ["aws_eks_cluster", "aws_eks_node_group", "aws_iam_role", "aws_security_group"],
    },
    "gcp-gke": {
        "name": "GCP GKE Cluster",
        "description": "GKE cluster with node pool and workload identity",
        "provider": "google",
        "variables": ["cluster_name", "region", "machine_type", "node_count", "kubernetes_version"],
        "resources": ["google_container_cluster", "google_container_node_pool", "google_service_account"],
    },
    "gcp-cloud-run": {
        "name": "GCP Cloud Run",
        "description": "Cloud Run service with custom domain and IAM",
        "provider": "google",
        "variables": ["service_name", "image", "region", "memory", "cpu"],
        "resources": ["google_cloud_run_service", "google_cloud_run_service_iam_member"],
    },
    "azure-aks": {
        "name": "Azure AKS Cluster",
        "description": "AKS cluster with default node pool and Azure CNI",
        "provider": "azurerm",
        "variables": ["cluster_name", "location", "node_count", "vm_size", "kubernetes_version"],
        "resources": ["azurerm_kubernetes_cluster", "azurerm_resource_group"],
    },
    "azure-app-service": {
        "name": "Azure App Service",
        "description": "App Service with plan and deployment slots",
        "provider": "azurerm",
        "variables": ["app_name", "location", "sku_name", "runtime_stack"],
        "resources": ["azurerm_app_service", "azurerm_app_service_plan", "azurerm_resource_group"],
    },
}

MODULE_CODE_TEMPLATES: dict[str, str] = {
    "aws-vpc": '''# AWS VPC Module
# Generated by OmniPotent

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}

variable "availability_zones" {
  description = "List of availability zones"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "enable_nat_gateway" {
  description = "Enable NAT Gateway for private subnets"
  type        = bool
  default     = true
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "${var.project}-${var.environment}-vpc"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name        = "${var.project}-${var.environment}-igw"
    Environment = var.environment
  }
}

resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "${var.project}-${var.environment}-public-${count.index + 1}"
    Environment = var.environment
    Type        = "public"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index + length(var.availability_zones))
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name        = "${var.project}-${var.environment}-private-${count.index + 1}"
    Environment = var.environment
    Type        = "private"
  }
}

resource "aws_eip" "nat" {
  count  = var.enable_nat_gateway ? 1 : 0
  domain = "vpc"

  tags = {
    Name        = "${var.project}-${var.environment}-nat-eip"
    Environment = var.environment
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = var.enable_nat_gateway ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id

  tags = {
    Name        = "${var.project}-${var.environment}-nat"
    Environment = var.environment
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name        = "${var.project}-${var.environment}-public-rt"
    Environment = var.environment
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  dynamic "route" {
    for_each = var.enable_nat_gateway ? [1] : []
    content {
      cidr_block     = "0.0.0.0/0"
      nat_gateway_id = aws_nat_gateway.main[0].id
    }
  }

  tags = {
    Name        = "${var.project}-${var.environment}-private-rt"
    Environment = var.environment
  }
}

resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}

output "nat_gateway_ip" {
  description = "NAT Gateway public IP"
  value       = var.enable_nat_gateway ? aws_eip.nat[0].public_ip : null
}
''',
    "aws-ecs-fargate": '''# AWS ECS Fargate Module
# Generated by OmniPotent

variable "cluster_name" {
  description = "ECS cluster name"
  type        = string
}

variable "service_name" {
  description = "ECS service name"
  type        = string
}

variable "container_image" {
  description = "Container image URL"
  type        = string
}

variable "container_port" {
  description = "Container port"
  type        = number
  default     = 8080
}

variable "cpu" {
  description = "Fargate CPU units"
  type        = number
  default     = 256
}

variable "memory" {
  description = "Fargate memory (MB)"
  type        = number
  default     = 512
}

variable "desired_count" {
  description = "Desired task count"
  type        = number
  default     = 2
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the service"
  type        = list(string)
}

variable "environment" {
  description = "Environment name"
  type        = string
}

resource "aws_ecs_cluster" "main" {
  name = var.cluster_name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_ecs_task_definition" "main" {
  family                   = var.service_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = var.service_name
    image     = var.container_image
    essential = true
    portMappings = [{
      containerPort = var.container_port
      hostPort      = var.container_port
      protocol      = "tcp"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.main.name
        awslogs-region        = data.aws_region.current.name
        awslogs-stream-prefix = var.service_name
      }
    }
  }])

  tags = {
    Environment = var.environment
  }
}

resource "aws_ecs_service" "main" {
  name            = var.service_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.main.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.main.arn
    container_name   = var.service_name
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.main]

  tags = {
    Environment = var.environment
  }
}

resource "aws_lb" "main" {
  name               = "${var.service_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids

  tags = {
    Environment = var.environment
  }
}

resource "aws_lb_target_group" "main" {
  name        = "${var.service_name}-tg"
  port        = var.container_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 10
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_lb_listener" "main" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
}

resource "aws_security_group" "alb" {
  name        = "${var.service_name}-alb-sg"
  description = "ALB security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_security_group" "ecs" {
  name        = "${var.service_name}-ecs-sg"
  description = "ECS tasks security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = var.container_port
    to_port         = var.container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Environment = var.environment
  }
}

resource "aws_cloudwatch_log_group" "main" {
  name              = "/ecs/${var.service_name}"
  retention_in_days = 30

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role" "ecs_execution" {
  name = "${var.service_name}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name = "${var.service_name}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
}

data "aws_region" "current" {}

output "cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.main.name
}

output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}
''',
    "aws-rds-postgres": '''# AWS RDS PostgreSQL Module
# Generated by OmniPotent

variable "identifier" {
  description = "RDS instance identifier"
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "15.4"
}

variable "instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "allocated_storage" {
  description = "Allocated storage in GB"
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Database name"
  type        = string
}

variable "username" {
  description = "Master username"
  type        = string
  sensitive   = true
}

variable "password" {
  description = "Master password"
  type        = string
  sensitive   = true
}

variable "vpc_id" {
  description = "VPC ID"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for DB subnet group"
  type        = list(string)
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to access the database"
  type        = list(string)
  default     = []
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "multi_az" {
  description = "Enable Multi-AZ deployment"
  type        = bool
  default     = false
}

variable "backup_retention_period" {
  description = "Backup retention period in days"
  type        = number
  default     = 7
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.identifier}-subnet-group"
  subnet_ids = var.subnet_ids

  tags = {
    Name        = "${var.identifier}-subnet-group"
    Environment = var.environment
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.identifier}-rds-sg"
  description = "Security group for RDS instance"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.allowed_cidr_blocks
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.identifier}-rds-sg"
    Environment = var.environment
  }
}

resource "aws_db_instance" "main" {
  identifier             = var.identifier
  engine                 = "postgres"
  engine_version         = var.engine_version
  instance_class         = var.instance_class
  allocated_storage      = var.allocated_storage
  storage_type           = "gp3"
  storage_encrypted      = true

  db_name  = var.db_name
  username = var.username
  password = var.password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  multi_az                  = var.multi_az
  publicly_accessible       = false
  backup_retention_period   = var.backup_retention_period
  backup_window             = "03:00-04:00"
  maintenance_window        = "Mon:04:00-Mon:05:00"
  auto_minor_version_upgrade = true
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${var.identifier}-final-snapshot" : null

  performance_insights_enabled = true

  tags = {
    Name        = var.identifier
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

output "endpoint" {
  description = "RDS instance endpoint"
  value       = aws_db_instance.main.endpoint
}

output "address" {
  description = "RDS instance address"
  value       = aws_db_instance.main.address
}

output "port" {
  description = "RDS instance port"
  value       = aws_db_instance.main.port
}

output "database_name" {
  description = "Database name"
  value       = aws_db_instance.main.db_name
}
''',
    "aws-lambda": '''# AWS Lambda Function Module
# Generated by OmniPotent

variable "function_name" {
  description = "Lambda function name"
  type        = string
}

variable "runtime" {
  description = "Lambda runtime"
  type        = string
  default     = "python3.11"
}

variable "handler" {
  description = "Lambda handler"
  type        = string
  default     = "main.handler"
}

variable "memory_size" {
  description = "Memory size in MB"
  type        = number
  default     = 256
}

variable "timeout" {
  description = "Timeout in seconds"
  type        = number
  default     = 30
}

variable "source_dir" {
  description = "Source directory to package"
  type        = string
}

variable "environment_variables" {
  description = "Environment variables"
  type        = map(string)
  default     = {}
}

variable "vpc_config" {
  description = "VPC configuration (optional)"
  type = object({
    subnet_ids         = list(string)
    security_group_ids = list(string)
  })
  default = null
}

variable "environment" {
  description = "Environment name"
  type        = string
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.module}/.terraform/lambda_${var.function_name}.zip"
}

resource "aws_lambda_function" "main" {
  function_name    = var.function_name
  role             = aws_iam_role.lambda.arn
  handler          = var.handler
  runtime          = var.runtime
  memory_size      = var.memory_size
  timeout          = var.timeout

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = var.environment_variables
  }

  dynamic "vpc_config" {
    for_each = var.vpc_config != null ? [var.vpc_config] : []
    content {
      subnet_ids         = vpc_config.value.subnet_ids
      security_group_ids = vpc_config.value.security_group_ids
    }
  }

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 30

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role" "lambda" {
  name = "${var.function_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })

  tags = {
    Environment = var.environment
  }
}

resource "aws_iam_role_policy" "lambda_logs" {
  name = "${var.function_name}-logs"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda.arn}:*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_vpc" {
  count = var.vpc_config != null ? 1 : 0
  name  = "${var.function_name}-vpc"
  role  = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateNetworkInterface",
          "ec2:DescribeNetworkInterfaces",
          "ec2:DeleteNetworkInterface"
        ]
        Resource = "*"
      }
    ]
  })
}

output "function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.main.function_name
}

output "function_arn" {
  description = "Lambda function ARN"
  value       = aws_lambda_function.main.arn
}

output "invoke_arn" {
  description = "Lambda invoke ARN"
  value       = aws_lambda_function.main.invoke_arn
}

output "role_arn" {
  description = "Lambda IAM role ARN"
  value       = aws_iam_role.lambda.arn
}
''',
}

BACKEND_TEMPLATES: dict[str, str] = {
    "s3": '''# S3 Backend Configuration
terraform {{
  backend "s3" {{
    bucket         = "{bucket}"
    key            = "{key}"
    region         = "{region}"
    encrypt        = true
    dynamodb_table = "{dynamodb_table}"
  }}
}}
''',
    "gcs": '''# GCS Backend Configuration
terraform {{
  backend "gcs" {{
    bucket = "{bucket}"
    prefix = "{prefix}"
  }}
}}
''',
    "azurerm": '''# Azure Backend Configuration
terraform {{
  backend "azurerm" {{
    resource_group_name  = "{resource_group_name}"
    storage_account_name = "{storage_account_name}"
    container_name       = "{container_name}"
    key                  = "{key}"
  }}
}}
''',
    "remote": '''# Terraform Cloud Backend Configuration
terraform {{
  cloud {{
    organization = "{organization}"

    workspaces {{
      name = "{workspace}"
    }}
  }}
}}
''',
}

PROVIDER_TEMPLATES: dict[str, str] = {
    "aws": '''# AWS Provider Configuration
terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Environment = var.environment
      Project     = var.project
      ManagedBy   = "terraform"
    }
  }
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}
''',
    "google": '''# GCP Provider Configuration
terraform {
  required_version = ">= 1.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}
''',
    "azurerm": '''# Azure Provider Configuration
terraform {
  required_version = ">= 1.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
}

variable "location" {
  description = "Azure location"
  type        = string
  default     = "eastus"
}

variable "environment" {
  description = "Environment name"
  type        = string
}

variable "project" {
  description = "Project name"
  type        = string
}
''',
    "kubernetes": '''# Kubernetes Provider Configuration
terraform {
  required_version = ">= 1.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.0"
    }
  }
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  kubernetes {
    config_path = var.kubeconfig_path
  }
}

variable "kubeconfig_path" {
  description = "Path to kubeconfig file"
  type        = string
  default     = "~/.kube/config"
}
''',
}


# --------------------------------------------------------------------------- #
# Code Generation Tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_module_templates() -> list[dict]:
    """List available Terraform module templates.

    Returns a list of module templates that can be used with
    generate_terraform_module.
    """
    return [
        {
            "id": key,
            "name": template["name"],
            "description": template["description"],
            "provider": template["provider"],
            "variables": template["variables"],
            "resources": template["resources"],
        }
        for key, template in MODULE_TEMPLATES.items()
    ]


@mcp.tool()
def generate_terraform_module(
    template: str,
    project_name: str = "my-project",
    environment: str = "production",
    custom_vars: str = "",
) -> dict:
    """Generate a complete Terraform module from a template.

    Args:
        template: Template ID (e.g., "aws-vpc", "aws-ecs-fargate", "aws-rds-postgres")
        project_name: Project name for resource naming
        environment: Target environment (production, staging, development)
        custom_vars: JSON string of custom variable values to set

    Returns:
        Generated Terraform module code and related files
    """
    if template not in MODULE_TEMPLATES:
        return {
            "error": f"Template '{template}' not found",
            "available": list(MODULE_TEMPLATES.keys()),
        }

    template_info = MODULE_TEMPLATES[template]
    code = MODULE_CODE_TEMPLATES.get(template, "")

    if not code:
        return {
            "error": f"Code template for '{template}' not yet implemented",
            "template_info": template_info,
        }

    import json as json_module
    custom_values = {}
    if custom_vars:
        try:
            custom_values = json_module.loads(custom_vars)
        except json_module.JSONDecodeError:
            pass

    tfvars_content = f'# Generated tfvars for {template}\n\n'
    tfvars_content += f'project     = "{project_name}"\n'
    tfvars_content += f'environment = "{environment}"\n'
    for var_name in template_info["variables"]:
        if var_name in custom_values:
            value = custom_values[var_name]
            if isinstance(value, str):
                tfvars_content += f'{var_name} = "{value}"\n'
            elif isinstance(value, bool):
                tfvars_content += f'{var_name} = {str(value).lower()}\n'
            elif isinstance(value, list):
                tfvars_content += f'{var_name} = {json_module.dumps(value)}\n'
            else:
                tfvars_content += f'{var_name} = {value}\n'

    return {
        "template": template,
        "template_info": template_info,
        "project_name": project_name,
        "environment": environment,
        "files": {
            "main.tf": code,
            "terraform.tfvars": tfvars_content,
        },
        "notes": [
            "Review and customize variable values before applying",
            "Ensure required provider credentials are configured",
            "Run 'terraform init' to initialize the module",
            "Run 'terraform plan' to preview changes before applying",
        ],
    }


@mcp.tool()
def generate_backend_config(
    backend_type: str,
    bucket: str = "",
    key: str = "terraform.tfstate",
    region: str = "us-east-1",
    organization: str = "",
    workspace: str = "",
    resource_group_name: str = "",
    storage_account_name: str = "",
    container_name: str = "",
    dynamodb_table: str = "",
    prefix: str = "terraform/state",
) -> dict:
    """Generate Terraform backend configuration for remote state.

    Args:
        backend_type: Backend type ("s3", "gcs", "azurerm", "remote")
        bucket: S3/GCS bucket name
        key: State file key/path
        region: AWS region (for S3)
        organization: Terraform Cloud organization (for remote)
        workspace: Terraform Cloud workspace (for remote)
        resource_group_name: Azure resource group (for azurerm)
        storage_account_name: Azure storage account (for azurerm)
        container_name: Azure container name (for azurerm)
        dynamodb_table: DynamoDB table for state locking (for S3)
        prefix: GCS prefix (for gcs)

    Returns:
        Backend configuration file content
    """
    if backend_type not in BACKEND_TEMPLATES:
        return {
            "error": f"Backend type '{backend_type}' not supported",
            "available": list(BACKEND_TEMPLATES.keys()),
        }

    template = BACKEND_TEMPLATES[backend_type]

    config = template.format(
        bucket=bucket,
        key=key,
        region=region,
        organization=organization,
        workspace=workspace,
        resource_group_name=resource_group_name,
        storage_account_name=storage_account_name,
        container_name=container_name,
        dynamodb_table=dynamodb_table or f"{bucket}-lock",
        prefix=prefix,
    )

    setup_instructions = []
    if backend_type == "s3":
        setup_instructions = [
            f"Create S3 bucket: aws s3 mb s3://{bucket}",
            "Enable versioning: aws s3api put-bucket-versioning --bucket {bucket} --versioning-configuration Status=Enabled",
            f"Create DynamoDB table: aws dynamodb create-table --table-name {dynamodb_table or f'{bucket}-lock'} --attribute-definitions AttributeName=LockID,AttributeType=S --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST",
        ]
    elif backend_type == "gcs":
        setup_instructions = [
            f"Create GCS bucket: gsutil mb gs://{bucket}",
            "Enable versioning: gsutil versioning set on gs://{bucket}",
        ]
    elif backend_type == "azurerm":
        setup_instructions = [
            "Create resource group and storage account",
            "Create blob container for state",
            "Configure Azure credentials",
        ]
    elif backend_type == "remote":
        setup_instructions = [
            "Create Terraform Cloud organization and workspace",
            "Run 'terraform login' to authenticate",
        ]

    return {
        "backend_type": backend_type,
        "config": config,
        "file_name": "backend.tf",
        "setup_instructions": setup_instructions,
        "notes": [
            "Save this as backend.tf in your Terraform directory",
            "Run 'terraform init' to migrate existing state",
            "Ensure credentials are properly configured",
        ],
    }


@mcp.tool()
def generate_provider_config(
    provider: str,
    region: str = "",
    project: str = "",
    environment: str = "production",
) -> dict:
    """Generate Terraform provider configuration.

    Args:
        provider: Provider name ("aws", "google", "azurerm", "kubernetes")
        region: Cloud region (provider-specific)
        project: Project name or ID
        environment: Environment name

    Returns:
        Provider configuration file content
    """
    if provider not in PROVIDER_TEMPLATES:
        return {
            "error": f"Provider '{provider}' not supported",
            "available": list(PROVIDER_TEMPLATES.keys()),
        }

    config = PROVIDER_TEMPLATES[provider]

    tfvars_lines = [f'environment = "{environment}"']
    if provider == "aws":
        tfvars_lines.append(f'aws_region  = "{region or "us-east-1"}"')
        tfvars_lines.append(f'project     = "{project or "my-project"}"')
    elif provider == "google":
        tfvars_lines.append(f'region      = "{region or "us-central1"}"')
        tfvars_lines.append(f'project_id  = "{project}"')
    elif provider == "azurerm":
        tfvars_lines.append(f'location    = "{region or "eastus"}"')
        tfvars_lines.append(f'project     = "{project or "my-project"}"')

    return {
        "provider": provider,
        "files": {
            "providers.tf": config,
            "terraform.tfvars": "\n".join(tfvars_lines),
        },
        "notes": [
            f"Configure {provider} credentials before running Terraform",
            "Review and customize variable defaults",
            "Run 'terraform init' to download the provider",
        ],
    }


@mcp.tool()
def generate_tfvars(
    template: str,
    project_name: str = "my-project",
    environment: str = "production",
    values: str = "",
) -> dict:
    """Generate terraform.tfvars file from a module template.

    Args:
        template: Module template ID (e.g., "aws-vpc", "aws-ecs-fargate")
        project_name: Project name
        environment: Environment name
        values: JSON string of variable values

    Returns:
        terraform.tfvars file content
    """
    if template not in MODULE_TEMPLATES:
        return {
            "error": f"Template '{template}' not found",
            "available": list(MODULE_TEMPLATES.keys()),
        }

    import json as json_module
    template_info = MODULE_TEMPLATES[template]

    custom_values = {}
    if values:
        try:
            custom_values = json_module.loads(values)
        except json_module.JSONDecodeError:
            pass

    tfvars_lines = [
        f"# terraform.tfvars for {template_info['name']}",
        f"# Environment: {environment}",
        "",
        f'project     = "{project_name}"',
        f'environment = "{environment}"',
        "",
    ]

    example_values: dict[str, str | int | bool | list] = {
        "vpc_cidr": "10.0.0.0/16",
        "availability_zones": '["us-east-1a", "us-east-1b", "us-east-1c"]',
        "enable_nat_gateway": "true",
        "cluster_name": f'"{project_name}-cluster"',
        "service_name": f'"{project_name}-api"',
        "container_image": f'"123456789.dkr.ecr.us-east-1.amazonaws.com/{project_name}:latest"',
        "cpu": "256",
        "memory": "512",
        "desired_count": "2",
        "identifier": f'"{project_name}-db"',
        "engine_version": '"15.4"',
        "instance_class": '"db.t3.micro"',
        "allocated_storage": "20",
        "db_name": f'"{project_name.replace("-", "_")}"',
        "function_name": f'"{project_name}-function"',
        "runtime": '"python3.11"',
        "handler": '"main.handler"',
        "memory_size": "256",
        "timeout": "30",
    }

    for var_name in template_info["variables"]:
        if var_name in custom_values:
            value = custom_values[var_name]
            if isinstance(value, str):
                tfvars_lines.append(f'{var_name} = "{value}"')
            elif isinstance(value, bool):
                tfvars_lines.append(f'{var_name} = {str(value).lower()}')
            elif isinstance(value, list):
                tfvars_lines.append(f'{var_name} = {json_module.dumps(value)}')
            else:
                tfvars_lines.append(f'{var_name} = {value}')
        elif var_name in example_values:
            tfvars_lines.append(f'{var_name} = {example_values[var_name]}')
        else:
            tfvars_lines.append(f'# {var_name} = ""  # TODO: Set value')

    return {
        "template": template,
        "tfvars": "\n".join(tfvars_lines),
        "file_name": "terraform.tfvars",
        "variables": template_info["variables"],
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
