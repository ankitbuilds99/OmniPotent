# ADR 0006: Customer-readiness build versus reuse

## Status

Accepted for the read-only customer wedge.

## Context

The repository already contains the customer-facing React console, FastAPI
control plane, LangGraph investigation workflow, MCP connector boundary, and
tenant-aware persistence. Replacing those layers would increase migration and
security risk, so the readiness work is constrained to verified vertical
slices that preserve the existing architecture.

## Decision

Reuse the existing React/Vite application, FastAPI routes, LangGraph graph,
MCP adapters, PostgreSQL models/migrations, Redis shared state, incident
workspace, evidence contracts, policy gateway, and deployment manifests.

Add only narrow vertical slices where the current contracts are incomplete:

- durable coordinator approval/cancel and shared execution controls;
- normalized source observations and evidence verification;
- tenant-scoped service/catalog validation;
- fail-closed action policy, runner, recovery receipt, and release gates;
- customer-readiness documentation and machine-readable release checks.

Do not replace the application with a new scaffold, introduce a telemetry
warehouse for the MVP, or enable production mutation. New integrations must
use the existing connector boundary and expose real capability/freshness
metadata.

## Consequences

This preserves existing customer routes and limits migration risk. It also
means some capabilities remain partial until their real provider, browser, or
customer-environment acceptance is performed. The readiness evaluator therefore
fails closed and distinguishes local implementation from release evidence.
