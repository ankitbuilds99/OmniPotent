# OmniPotent Control Plane Audit

## Current checkout

- Branch: `security/xerj-readiness`
- Commit: `ba29bc6ba9ebf4fee0ff7e71691d08db607566a8`
- Baseline selected commit: `d6460eb2e5265d845a0d0febe17a2d26224ad2ab`

## Read-only investigation path

```text
React Console / Composer
  -> frontend/src/api.ts::chatStream
  -> POST /chat/stream (app/api/main.py::chat_stream)
  -> require_auth + require_perm("run_investigation") + quota_gate
  -> tenant_context.set_tenant()
  -> CopilotSession (app/session.py)
  -> LangGraph builder (app/graph/builder.py)
  -> MCP client (app/mcp/client.py)
  -> read-only connector tools
  -> streamed trace/final answer back to React
```

The console's connection rail calls `GET /integrations/status`. This slice now
uses the active `TenantConfig` for multi-tenant requests and host `Settings`
only for single-tenant requests. A tenant cannot inherit another tenant's
connector configuration from process-global settings.

## Control-plane capability map

| Capability | Source of truth | Current state | Mutation boundary |
|---|---|---|---|
| Identity and tenant scope | `app/api/main.py::require_auth`, `app/tenancy/auth.py`, `app/tenant_context.py` | Implemented for authenticated routes | Request context is established before handlers |
| Investigation API | `POST /chat`, `POST /chat/stream` | Implemented | RBAC permission `run_investigation` and quota gate |
| Investigation state | `app/session.py`, LangGraph checkpointer, `app/investigation_history.py` | Implemented with persistence paths | Thread IDs are tenant-scoped internally |
| MCP discovery and execution | `app/mcp/client.py`, `app/mcp/servers/*` | Implemented | Tool policy and approval routing apply to writes |
| Integration status | `GET /integrations/status` | Fixed in this slice | Tenant configuration is read-only and credential values are never returned |
| Human approval | `POST /approve`, graph approval nodes, safety preflight | Implemented for gated actions | Approval required before destructive tools |
| Database tenancy | `app/tenancy/store.py`, `app/database/models.py` | Partial across the broader application | Every feature router still requires an isolation review |

## Infrastructure-changing paths

The MCP catalog includes write-capable tools for GitHub pull requests, PagerDuty
updates, Kubernetes rollout/scale/restart, ArgoCD sync/rollback, Terraform
plan/apply, Slack notifications, and runbook execution. These must remain behind
the graph approval path and are not enabled by this slice. The implemented path
only reports connector readiness and preserves the existing read-only
investigation flow.

## Release-blocking reliability issues

The historical audit still identifies broader issues requiring separate work:

- cross-tenant isolation is incomplete across several feature routers;
- SSRF endpoint coverage is not yet comprehensive;
- some integrations and operational responses remain simulated or partial;
- OAuth frontend/backend end-to-end behavior needs a separate release gate;
- several historical audit findings remain untested against live route contracts.

## Tests

Focused regression command:

```bash
uv run --with pytest --with pytest-asyncio python -m pytest -q \
  tests/test_integration_api.py tests/test_security.py \
  tests/test_oauth_security.py tests/test_websocket.py \
  tests/test_api_websocket_rbac_audit.py tests/test_tenant_isolation.py \
  tests/test_safety_preflight.py tests/test_coordinator_durable_approval.py
```

Result at implementation time: **112 passed**, with existing deprecation
warnings only.
