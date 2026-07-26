"""Terraform Cloud/Enterprise MCP server.

Gives the agent infrastructure-as-code context: workspace state, run history,
plan diffs, and the ability to trigger plans/applies (with human approval). When
`TERRAFORM_CLOUD_TOKEN` is set it calls the real Terraform Cloud API; otherwise
it runs in OFFLINE DEMO mode with fixtures that tie into a sample infrastructure
incident scenario.

Tools:
  Read (allow):
    - list_workspaces:        list workspaces in the organization
    - get_workspace:          get workspace details (settings, VCS, last run)
    - get_state:              get current state (managed resources)
    - get_run_history:        get plan/apply history for a workspace
    - get_run_details:        get specific run details
    - get_plan_output:        get plan diff
    - list_state_resources:   list managed resources in current state

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
        result = list(_DEMO_WORKSPACES)
        if search:
            result = [ws for ws in result if search.lower() in str(ws["name"]).lower()]
        if environment:
            result = [ws for ws in result if str(ws.get("environment", "")).lower() == environment.lower()]
        return result

    import httpx

    params: dict[str, str | int] = {"page[size]": 100}
    if search:
        params["search[name]"] = search

    resp = httpx.get(
        f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces",
        headers=_headers(),
        params=params,
        timeout=20,
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
        ws = _find_workspace(name)
        if not ws:
            return {"error": f"workspace '{name}' not found (offline demo)",
                    "available": [w["name"] for w in _DEMO_WORKSPACES]}
        if name == "checkout-prod" or name == "ws-checkout-prod":
            return dict(_DEMO_WORKSPACE_DETAIL)
        return {**ws, "last_run": None}

    import httpx

    resp = httpx.get(
        f"{BASE_URL}/workspaces/{name}" if name.startswith("ws-") else
        f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{name}",
        headers=_headers(),
        timeout=20,
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
        ws = _find_workspace(workspace)
        if not ws:
            return {"error": f"workspace '{workspace}' not found (offline demo)",
                    "available": [w["name"] for w in _DEMO_WORKSPACES]}
        if workspace in ("checkout-prod", "ws-checkout-prod"):
            # Count resources by type
            type_counts: dict[str, int] = {}
            for r in _DEMO_STATE_RESOURCES:
                rtype = str(r["type"])
                type_counts[rtype] = type_counts.get(rtype, 0) + 1
            return {
                "workspace": workspace,
                "serial": 42,
                "terraform_version": "1.6.0",
                "resource_count": len(_DEMO_STATE_RESOURCES),
                "resources_by_type": type_counts,
                "outputs": {
                    "service_endpoint": "https://checkout.acme.com",
                    "database_endpoint": "checkout-db-prod.xxx.us-east-1.rds.amazonaws.com",
                },
            }
        return {"workspace": workspace, "serial": 1, "resource_count": ws.get("resource_count", 0),
                "resources_by_type": {}, "outputs": {}}

    import httpx

    # Get workspace ID if name provided
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=20,
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    # Get current state version
    resp = httpx.get(
        f"{BASE_URL}/workspaces/{ws_id}/current-state-version",
        headers=_headers(),
        timeout=20,
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
        ws = _find_workspace(workspace)
        if not ws:
            return [{"error": f"workspace '{workspace}' not found (offline demo)",
                     "available": [w["name"] for w in _DEMO_WORKSPACES]}]
        if workspace in ("checkout-prod", "ws-checkout-prod"):
            resources = list(_DEMO_STATE_RESOURCES)
            if resource_type:
                resources = [r for r in resources if r["type"] == resource_type]
            return resources
        return []

    import httpx

    # Get workspace ID
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=20,
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    # Get current state version
    state_resp = httpx.get(
        f"{BASE_URL}/workspaces/{ws_id}/current-state-version",
        headers=_headers(),
        timeout=20,
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
        timeout=30,
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
        ws = _find_workspace(workspace)
        if not ws:
            return [{"error": f"workspace '{workspace}' not found (offline demo)",
                     "available": [w["name"] for w in _DEMO_WORKSPACES]}]
        if workspace in ("checkout-prod", "ws-checkout-prod"):
            return _DEMO_RUN_HISTORY[:limit]
        return []

    import httpx

    # Get workspace ID
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=20,
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    resp = httpx.get(
        f"{BASE_URL}/workspaces/{ws_id}/runs",
        headers=_headers(),
        params={"page[size]": limit},
        timeout=20,
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
        if run_id == "run-abc123" or run_id == "":
            return dict(_DEMO_RUN_DETAILS)
        # Check if it's in history
        for run in _DEMO_RUN_HISTORY:
            if run["id"] == run_id:
                return {**run, "workspace": {"id": "ws-checkout-prod", "name": "checkout-prod"}}
        return {"error": f"run '{run_id}' not found (offline demo)",
                "available": [r["id"] for r in _DEMO_RUN_HISTORY]}

    import httpx

    resp = httpx.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=_headers(),
        timeout=20,
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


@mcp.tool()
def get_plan_output(run_id: str) -> str:
    """Get the plan output (diff) for a run.

    Args:
        run_id: The run ID (e.g. "run-abc123").

    Returns the terraform plan output showing what will change.
    """
    if OFFLINE:
        if run_id == "run-abc123" or run_id == "":
            return _DEMO_PLAN_OUTPUT
        return f"Plan output not available for run '{run_id}' (offline demo)"

    import httpx

    # First get the plan ID from the run
    run_resp = httpx.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=_headers(),
        timeout=20,
    )
    run_resp.raise_for_status()
    plan_id = run_resp.json().get("data", {}).get("relationships", {}).get("plan", {}).get("data", {}).get("id")

    if not plan_id:
        return f"No plan found for run {run_id}"

    # Get the plan JSON output
    resp = httpx.get(
        f"{BASE_URL}/plans/{plan_id}/json-output",
        headers=_headers(),
        timeout=30,
    )

    if resp.status_code == 404:
        # Try getting the text output instead
        text_resp = httpx.get(
            f"{BASE_URL}/plans/{plan_id}",
            headers=_headers(),
            timeout=30,
        )
        if text_resp.status_code == 200:
            attrs = text_resp.json().get("data", {}).get("attributes", {})
            return attrs.get("log-read-url", "Plan output not available")
        return f"Plan output not available for run {run_id}"

    resp.raise_for_status()
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
        ws = _find_workspace(workspace)
        if not ws:
            return {"error": f"workspace '{workspace}' not found (offline demo)",
                    "available": [w["name"] for w in _DEMO_WORKSPACES]}
        return {
            "status": "plan queued (simulated - offline demo, no actual plan triggered)",
            "run_id": "run-demo-new",
            "workspace": workspace,
            "speculative": speculative,
            "message": message,
        }

    import httpx

    # Get workspace ID
    ws_id = workspace if workspace.startswith("ws-") else None
    if not ws_id:
        ws_resp = httpx.get(
            f"{BASE_URL}/organizations/{TERRAFORM_ORGANIZATION}/workspaces/{workspace}",
            headers=_headers(),
            timeout=20,
        )
        ws_resp.raise_for_status()
        ws_id = ws_resp.json().get("data", {}).get("id")

    # Create a new run
    payload = {
        "data": {
            "type": "runs",
            "attributes": {
                "message": message or "Triggered by DevOps Copilot",
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
        timeout=30,
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
        if run_id not in [r["id"] for r in _DEMO_RUN_HISTORY] and run_id != "run-abc123":
            return {"error": f"run '{run_id}' not found (offline demo)",
                    "available": [r["id"] for r in _DEMO_RUN_HISTORY]}
        return {
            "status": "apply queued (simulated - offline demo, no actual apply triggered)",
            "run_id": run_id,
            "comment": comment,
        }

    import httpx

    # First verify the run is in an applyable state
    run_resp = httpx.get(
        f"{BASE_URL}/runs/{run_id}",
        headers=_headers(),
        timeout=20,
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
        "comment": comment or "Applied by DevOps Copilot",
    }

    resp = httpx.post(
        f"{BASE_URL}/runs/{run_id}/actions/apply",
        headers=_headers(),
        json=payload,
        timeout=30,
    )

    if resp.status_code == 409:
        return {"error": "Run cannot be applied in its current state", "run_id": run_id}

    resp.raise_for_status()

    return {
        "status": "apply triggered",
        "run_id": run_id,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
