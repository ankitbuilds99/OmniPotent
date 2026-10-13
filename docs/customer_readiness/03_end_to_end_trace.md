# End-to-end customer trace

## Read-only investigation path

```text
Browser
  -> React route/API client and authenticated session
  -> FastAPI route and tenant/role guard
  -> connector or durable incident store
  -> normalized source observations and evidence IDs
  -> LangGraph investigation with bounded scope/budget
  -> evidence verifier and abstention logic
  -> investigation report/workbench/history
```

The expected customer journey is onboarding, scoped connector verification,
service selection, incident intake or manual creation, investigation, evidence
inspection, report sharing, history retrieval, and logout/login recovery.

## Mutation path

```text
Proposal
  -> exact action intent and immutable digest
  -> deterministic policy/capability checks
  -> human approval bound to intent and expiry
  -> mutation freeze and preflight checks
  -> optional customer runner
  -> typed provider outcome
  -> independent recovery observation
  -> append-only audit and recovery receipt
```

This path is separate from the default read-only path. A command accepted by a
provider is not treated as customer recovery. Missing, stale, contradictory,
or non-independent observations remain inconclusive.

## Verified local boundaries

- Tenant checks are applied server-side for incidents, evidence, jobs, memory,
  connector metadata, and action controls.
- Production configuration requires durable PostgreSQL checkpointing and
  configured Redis for shared state/session controls.
- Embedded or non-authoritative policy results cannot make a mutation decision
  allow.
- Terraform and Kubernetes unsupported/simulated operations are represented as
  such instead of being reported as completed.
- Artifact and readiness gates require actual digest/signature verification.

## Missing execution evidence

- Fresh browser run through the complete journey on desktop and mobile.
- Two-tenant live IDOR/SSE/share-link acceptance.
- Named Prometheus/Alertmanager/Kubernetes/GitHub provider tests.
- Worker crash/restart and production-like Redis failover.
- Independent labeled RCA corpus and signed pilot artifacts.
