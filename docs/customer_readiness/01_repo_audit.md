# Customer readiness repository audit

Generated from the current checkout on 2026-10-14. This document records local
evidence only; it does not convert missing provider, customer, or staging
evidence into a pass.

## Baseline

- Branch: `codex/fix-pipeline-demo`
- Commit at audit start: `a2083de721a323da07f56531ca55d9b1cf5c865e`
- Runtime: Python/FastAPI backend, React/Vite frontend, LangGraph, PostgreSQL,
  Redis, MCP adapters, and Kubernetes manifests.
- The checkout contained uncommitted implementation changes. The final commit
  SHA must be recorded after those changes are reviewed.

## Capability findings

| Capability | Status | Local evidence | Remaining proof |
|---|---|---|---|
| Authentication, organizations, RBAC | PARTIAL | Server-side auth and tenant guards; incident tenant tests | Live OAuth and two-organization acceptance |
| Connectors and source provenance | PARTIAL | Connector adapters, typed observations, redaction and scope checks | Named-provider permission, timeout and freshness matrix |
| Alert intake and deduplication | IMPLEMENTED LOCALLY | Durable intake/idempotency tests and Redis restart benchmark | Production Redis failover and provider contract |
| Service catalog and correlation | IMPLEMENTED LOCALLY | Catalog validation and ambiguity abstention tests | Live discovery and source identity validation |
| Durable investigations | PARTIAL | PostgreSQL production guard, coordinator persistence and cancellation controls | Worker crash/restart and multi-replica drill |
| RCA and abstention | IMPLEMENTED LOCALLY | Evidence verification, contradiction handling and release gate | Independent labeled 30-case benchmark |
| Workbench and onboarding | PARTIAL | React routes, onboarding, integration metadata and frontend tests | Browser E2E, mobile and WCAG acceptance |
| Policy and approvals | IMPLEMENTED LOCALLY | Deterministic gateway, approval binding, freeze controls and audit paths | Named disposable-cluster integration |
| Recovery verification | IMPLEMENTED LOCALLY | Independent receipt predicates and stale-data fail-closed tests | Real telemetry recovery drill |
| Operations and deployment | PARTIAL | Health checks, manifests, migration checks and configuration guards | Staging deploy, restore, failover and load evidence |
| Customer pilot | MISSING | No signed pilot artifacts in checkout | Independent users and two-organization pilot report |

## Prior concern revalidation

- Live rollback uses explicit typed safety checks and rejects unsupported or
  simulated execution paths; live cluster execution is not proven.
- Recovery is not inferred from an HTTP/API success; stale, missing, or
  non-independent observations produce an inconclusive result.
- Shared Redis state exists for deduplication and execution controls; failover
  evidence is still required.
- Domain contracts are connected in local tests, but browser and external
  provider acceptance remains outstanding.

## Release decision

The repository is not yet read-only-pilot-ready. The machine-readable gate is
`scripts/prd_readiness.py`; it intentionally exits nonzero while its external
evidence gates are unresolved.
