# PRD requirements status

This matrix distinguishes implementation from release verification. `Implemented`
means the current checkout has code and local tests; `Live verification` requires
configured customer-side systems or a named test environment.

| Requirement | Status | Evidence / remaining gate |
|---|---|---|
| FR-01 Workspace/connectors | Implemented in part | Connector/auth/config paths exist; live permission validation remains |
| FR-02 Alert intake/dedup | Implemented | Webhook auth, idempotency and dedup tests; p95 reference deployment remains |
| FR-03 Service/change correlation | Implemented in part | Correlation handlers now abstain when equally plausible root candidates tie; ambiguous live mappings need validation |
| FR-04 Evidence/provenance | Implemented | Evidence models, provenance, redaction and persistence tests |
| FR-05 Durable investigation | Implemented in part | PostgreSQL checkpoint enforcement, approval recovery, and Redis-backed planning pause/cancel control; full crash/restart drill remains |
| FR-06 Calibrated RCA | Implemented in part | Hypothesis/verifier/calibration paths exist; `evals.run_benchmark --release-gate` now fails closed until an independently labeled 30-case corpus with abstention coverage is supplied |
| FR-07 Investigation workbench | Implemented in part | UI and reconnect paths exist; full browser acceptance pass remains |
| FR-08 Typed rollback proposal | Implemented | Contracts, explicit revision requirement and rollback safety checks |
| FR-09 Policy/approval | Implemented | Deterministic policy, capability checks, approvals and freeze controls; embedded Kyverno results are explicitly non-authoritative and cannot make the aggregate policy decision allow a cluster mutation |
| FR-10 Safe execution/ledger | Implemented in part | Gateway, action states, kill switch and audit paths; live 500-negative-test run remains |
| FR-11 Recovery verification | Implemented | Typed recovery receipts fail closed on missing/stale user signals |
| FR-12 Append-only audit | Implemented in part | Hash-chain audit and receipts exist; notification digest outbox now retains failed deliveries for retry; live outbox/failure drill remains |
| FR-13 Health/cost limits | Implemented | Budgets, rate limits, provider degradation and token accounting exist |
| FR-14 Evaluation/replay | Implemented in part | Evals, cassettes, replay tools, and a release-corpus gate exist; release dashboard evidence remains |
| FR-15 Feedback/override | Implemented | Incident annotation API preserves authored correction chains and confirmed-memory promotion references the source digest; browser workflow remains |
| FR-16 Slack collaboration | Implemented in part | Slack notifications/approval paths exist; live workspace test remains |
| FR-17 Verified incident memory | Implemented in part | Tenant-scoped memory and confirmation metadata exist; `scripts/benchmark_memory_isolation.py` now exercises multi-organization search isolation; live database/RLS acceptance remains |
| FR-18 Postmortem/prevention | Implemented in part | Postmortem models/templates exist; full reviewer workflow needs live acceptance |
| FR-19 Dependency reasoning | Implemented in part | Dependency graph and blast-radius modules validate identity and now reject stale catalog entries before publication; live source freshness/uncertainty validation remains |
| FR-21 SLI/SLO | Implemented | SLO math, burn-rate windows and explicit no-data behavior |
| FR-22 Alert clustering | Implemented in part | Redis-backed tenant-scoped snapshots and distributed locking restore active incidents across worker replacement; live Redis benchmark passed 1,000 events/2 workers with 0 post-restart new incidents (p50 7.331ms, p95 8.157ms); production Redis failover validation remains |
| FR-23 Ownership/routing | Implemented in part | On-call/routing modules exist; external incident-provider validation remains |
| FR-24 Deployment identity | Implemented in part | `app.deployment_identity` now rejects mutable tags and validates immutable image-digest/source-commit bindings; exact live digest-to-SHA mapping remains |
| FR-25 Contradiction/abstention | Implemented | Independent-source requirement and uncertainty behavior tested |
| FR-26 GitOps eligibility | Implemented | Argo CD/Flux ownership blocks direct rollback |
| FR-27 Typed action result | Implemented | Explicit unsupported/simulated/submitted/applied/failed/indeterminate states |
| FR-28 Customer runner | Implemented in part | `app.customer_runner_service` provides a fail-closed HTTP boundary and `deploy/kubernetes/customer-runner.yaml` gives it a separate service account with no automatically mounted token; live customer-environment execution remains |
| FR-29 Recovery receipts | Implemented | Four-state recovery assessment and stale/missing-data tests |
| FR-30 Self-observability | Implemented in part | Health reports the separately configured shared deduplication store; production startup and synchronous session/rate-limit paths now fail closed without reachable Redis; provider-outage acceptance remains |
| FR-31 Retention/export | Implemented | Retention purge and hash-chain audit export now emit independently verifiable SHA-256 manifests; legal-retention drill remains |
| FR-32 Operator annotations | Implemented in part | Append-only authored revisions plus tenant-scoped database tables and Alembic migration 021, incident-workbench annotation/promotion API, and source digests; browser/live acceptance remains |
| FR-33 Service catalog | Implemented | Catalog validation rejects duplicate identities and stale entries before publication; live discovery-source coverage remains |
| FR-34 Runbook safety | Implemented | Typed normalization, policy gateway and approval logging |
| FR-35 Timeline/postmortem | Implemented in part | Timeline/postmortem modules exist; correction workflow acceptance remains |
| FR-36 Kill switch | Implemented in part | Global/tenant controls, API and gateway enforcement; Redis-backed shared registry is wired when configured, live failover/drill remains |
| FR-37 Reliability benchmark | Implemented in part | Evaluation harness exists; independent labeled benchmark evidence remains |
| FR-38 Connector contracts | Implemented in part | Connector tests exist; complete live contract matrix remains |
| FR-39 Change-risk preflight | Implemented | UID/version/controller/revision/schema preflight tests |
| FR-40 Production gate | Implemented in part | Fail-closed evaluator now requires declared verified artifacts, actual SHA-256 digest matches, and cryptographically verified Ed25519 signatures when coverage is complete; live signed pilot evidence remains |

## Release blockers

The repository is not release-verified until the live gates are run against a
named environment: crash/restart recovery, Kubernetes/observability permissions,
provider outage behavior, independent RCA benchmark, restore drill, and signed
pilot release review.

`scripts/prd_readiness.py` emits the current machine-readable handoff report and
exits nonzero while any external release gate remains unresolved.

## Verification environment note

The repository's locked development environment now passes
`scripts/verify_runtime.py` with `langgraph==1.2.6`,
`langgraph-checkpoint==4.1.1`, and
`langgraph-checkpoint-sqlite==3.1.0`. The full backend suite collected and
executed in that environment: 4,147 passed, 29 skipped, 29 failed, and 30
errored. Remaining failures are provider/network, socket-sandbox, stale test
contract, or live-infrastructure cases; they are not hidden behind the former
`DeltaChannelHistory` collection failure.

Local PostgreSQL evidence (2026-10-14): the `omnipotent_it` database was
migrated from `019_postmortems` through `020_evidence_observations` to
`021_operator_annotations (head)`. A separate disposable PostgreSQL database
was also migrated from empty to head and removed successfully. Production
staging migration and rollback-drill evidence remain separate release gates.
