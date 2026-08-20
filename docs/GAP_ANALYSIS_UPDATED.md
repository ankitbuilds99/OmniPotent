# DevOps Copilot - Updated Gap Analysis

**Generated:** 2026-08-02  
**Repo:** https://github.com/ankit25bcs10610/OmniPotent

---

## Executive Summary

The project has matured significantly. Most components previously marked as "MISSING" are now **fully implemented**. The remaining gaps are primarily around production hardening, external service integrations, and fine-tuning pipeline infrastructure.

> **Status revision (2026-08-20).** Several rows below were marked ✅ Complete on the
> basis that a module existed, without checking that anything imports it. An audit
> found 18 top-level modules — ~16,400 statements — with zero imports from outside
> their own package, zero router registrations and 0% test coverage. Code that
> nothing calls is not a shipped capability, so those rows are now **⚠️ Unwired**:
> the implementation is real and reviewable, but no runtime path reaches it.
> Rows still marked ✅ have been confirmed to be reachable from wired code.

| Category | Implemented | Partial | Missing |
|----------|-------------|---------|---------|
| Event Intelligence | 6/7 | 1 | 0 |
| Adaptive Model Router | 5/5 | 0 | 0 |
| Memory & Knowledge | 6/7 | 1 | 0 |
| Planning Layer | 3/3 | 0 | 0 |
| Coordinator | 2/2 | 0 | 0 |
| Verification Agent | 4/4 | 0 | 0 |
| Decision Engine | 4/5 | 1 | 0 |
| Policy Engine | 4/5 | 1 | 0 |
| Digital Twin / Simulation | 3/5 | 2 | 0 |
| Sandbox Test | 3/3 | 0 | 0 |
| Continuous Learning | 5/7 | 2 | 0 |
| Audit & Compliance | 5/6 | 1 | 0 |
| Platform Enablers | 19/23 | 4 | 0 |

> These counts are as-generated on 2026-08-02 and have **not** been recomputed against
> the reachability finding above — they count implementations, not wired capabilities,
> so they read high. Rows corrected so far cite `app/deduplication/`,
> `app/dependency_graph/`, `app/analytics_advanced/` and `app/tasks/`. The other 14
> unwired modules are simply absent from this document rather than misreported.
> Treat a ✅ as "code exists" unless the row says otherwise.
>
> Being worked through since: `app/deduplication/`, `app/dependency_graph/` and
> `app/cost_optimizer/` are now wired, and their rows are restored to ✅ on the
> stronger basis that HTTP requests reach them. `app/tasks/` is now deployed as a
> worker container (§13.F) — partially: two of its seven actors do real work out of
> process, and that section names what blocks each of the rest rather than counting
> the package as done.
>
> The reachability finding above was itself understated. Re-measured with an AST scan
> of every import (the first pass used a grep that missed `from app import (a, b)`,
> relative imports and `import app.x.y as z`), **60 of the app's packages — 99,863 of
> 240,249 lines — have no importer outside their own package**. 40 of those are reached
> only by tests; 20 are reached by nothing at all. `app/api/` is excluded from the
> count: it has no importer because it *is* the ASGI entrypoint (`Dockerfile:61`).

---

## 1. Event Intelligence Layer

| Component | Status | Location |
|-----------|--------|----------|
| Deduplication | ✅ Complete | `app/deduplication/` (9 modules), served at `/api/deduplication/*` |
| Correlation | ✅ Complete | `app/correlation/` (4 modules) |
| Noise Reduction | ✅ Complete | `app/event_intelligence/noise_reduction.py` |
| Incident Detection | ✅ Complete | `app/alerting/engine.py` |
| Alert Grouping | ✅ Complete | `app/correlation/engine.py`, `app/correlation/storm.py` |
| Severity Prediction | ✅ Complete | `app/event_intelligence/severity_predictor.py` |
| Blast Radius Estimation | ✅ Complete | `app/dependency_graph/blast_radius.py`, served at `/api/dependency-graph/blast-radius/{service}` |

**Notes:** Correlation, noise reduction (statistical, temporal, contextual) and severity
prediction are reachable from wired code.

Blast radius previously cited `app/risk/blast_radius.py`. Two modules define a
`BlastRadiusAnalyzer`, both exactly 406 lines, and the row named the wrong one:
`app/risk/blast_radius.py` is unwired *and* computes its answers from a hardcoded
ten-service e-commerce topology with invented request rates, so it returns the same
result regardless of the customer's infrastructure. `app/dependency_graph/blast_radius.py`
traverses a real `DependencyGraph`. That is the one now behind the endpoint.

Deduplication was not, until 2026-08-20. `app/deduplication/` was the largest unwired
module in the repo (~2,900 statements) while backing the `advanced_deduplication` flag
sold from the Growth tier up, so the row overstating its status had commercial
consequences. It is now served by `app/deduplication/api_router.py` at
`/api/deduplication/{process,incidents,stats}`, with one pipeline per organization —
a shared pipeline would correlate one tenant's alerts against another's incidents.

Plan flags select how deep the pipeline runs rather than whether it runs at all:
`basic_deduplication` (in every plan) keeps Layer 1 fingerprinting and Layer 6
suppression on, while `advanced_deduplication` enables the semantic and ML layers,
`cascade_detection` the temporal layer, and `service_dependency_map` the topology
layer. A Starter customer gets working deduplication with fewer layers of it.

Alert Grouping previously cited `app/alert_correlation/correlator.py`. That module
was a smaller, unwired reimplementation of `app/correlation/` — 46% of its public API
overlapped, and it lacked the dependency correlation, storm detection and semantic
matching the wired engine has. It was deleted on 2026-08-20; the row now points at
the implementation that actually runs.

---

## 2. Adaptive Model Router

| Component | Status | Location |
|-----------|--------|----------|
| Model routing (Claude/GPT/Llama) | ✅ Complete | `app/model_router/adaptive_router.py` |
| Route by incident complexity | ✅ Complete | `app/model_router/complexity_analyzer.py` |
| Route by SLA/latency | ✅ Complete | `app/model_router/adaptive_router.py:SLARequirements` |
| Customer tier-based routing | ✅ Complete | `app/model_router/adaptive_router.py:CustomerTier` |
| Cost/latency trade-off | ✅ Complete | `app/model_router/cost_optimizer.py` |

**Notes:** Full implementation with 5 model tiers (FAST → PREMIUM), customer tier restrictions, complexity analysis, and cost optimization.

---

## 3. Memory & Knowledge Layer

| Component | Status | Location |
|-----------|--------|----------|
| Long-Term Memory | ✅ Complete | `app/incident_memory.py`, `app/similar_search/` |
| Vector Database | ✅ Complete | `app/vector_store.py` |
| Live Knowledge Graph (GraphQL) | ✅ Complete | `app/knowledge_graph/graphql_schema.py` |
| Real-time infrastructure state | ✅ Complete | `app/knowledge_graph/neo4j_backend.py` |
| Continuous sync (GitHub/CloudTrail/Prometheus) | ✅ Complete | `app/knowledge_graph/sync_engine.py` |
| Service graph + Dependency map | ✅ Complete | `app/service_map/`, `app/dependency_graph/` served at `/api/dependency-graph/*` |
| Deployment Timeline | ⚠️ Partial | `app/timeline/collector.py` (needs enhancement) |

**Notes:** Full GraphQL API with Strawberry, Neo4j backend with in-memory fallback, real-time subscriptions for services/deployments/incidents.

`app/dependency_graph/` was unwired until 2026-08-20 and is now served behind the
`service_dependency_map` flag (Growth and up; 402 without it, since there is no
meaningful free version of a dependency map). Wiring it exposed a second problem:
two of its four discovery sources returned fabricated topology. The Kubernetes source
returned a fixed list of six services and the Istio source a fixed set of four edges,
both marked "simulation" in a comment. Behind an endpoint those become confident,
wrong blast-radius answers about infrastructure the customer does not run, so both
were replaced with real integrations — the Kubernetes API via the pod ServiceAccount,
and Kiali for the mesh graph — which return nothing when unreachable rather than
inventing. Guessing edges from service names survives as an explicitly opt-in flag
and is recorded under `metadata["inferred_dependencies"]` so a guess is never
mistaken for an observation.

---

## 4. Planning Layer

| Component | Status | Location |
|-----------|--------|----------|
| Task Graph creation | ✅ Complete | `app/planning/task_graph.py` |
| Break down into ordered steps | ✅ Complete | `app/planning/goal_decomposer.py` |
| Dependency tracking | ✅ Complete | `app/planning/task_graph.py:TaskGraph` |

**Notes:** Full DAG-based task graph with topological sorting, parallel execution levels,
checkpointing, and incident response templates. Graphs are persisted per org
(`app/planning/store.py` → `planning_graphs`), so one built across several requests
survives a restart or a different replica.

---

## 5. Coordinator

| Component | Status | Location |
|-----------|--------|----------|
| Orchestrates specialists | ✅ Complete | `app/agents/parallel_coordinator.py` (934 lines) |
| Task Graph workflow | ✅ Complete | `app/coordinator/dag_executor.py` |

**Notes:** Advanced DAG executor with conditional branching, resource-aware scheduling,
approval gates, checkpoint/resume capability. Execution records are persisted per org
(`app/coordinator/store.py` → `coordinator_executions`), written when a run *starts* so
that polling works mid-run. The approval gate itself is still a live `asyncio.Future`
held by the pod running the DAG — see Platform Enablers §G.

---

## 6. Verification Agent

| Component | Status | Location |
|-----------|--------|----------|
| Checks evidence | ✅ Complete | `app/verification/evidence_collector.py` |
| Detects hallucination | ✅ Complete | `app/verification/hallucination_detector.py` |
| Validates confidence | ✅ Complete | `app/verification/confidence_calibrator.py` |
| Data consistency before action | ✅ Complete | `app/verification/consistency_checker.py` |

**Notes:** Full verification pipeline with evidence collection from Prometheus/Loki/K8s/GitHub, hallucination detection, confidence calibration with Brier score.

---

## 7. Decision Engine

| Component | Status | Location |
|-----------|--------|----------|
| Ranks options by evidence | ✅ Complete | `app/verification/verification_agent.py` |
| Risk scoring | ✅ Complete | `app/risk/scorer.py` |
| Cost estimation | ✅ Complete | `app/cost_tracking/calculator.py` |
| Impact analysis | ✅ Complete | `app/predictive/business_impact.py` |
| Success probability | ⚠️ Partial | Exists in verification but needs dedicated module |

---

## 8. Policy Engine

| Component | Status | Location |
|-----------|--------|----------|
| Policy evaluation | ✅ Complete | `app/policy.py` |
| OPA/Kyverno integration | ⚠️ Partial | Policy framework exists, external integration stubbed |
| Compliance rules | ✅ Complete | `app/compliance/controls.py` |
| Change windows | ✅ Complete | `app/safety_gates/policy_gate.py` |
| Entry/exit criteria | ✅ Complete | `app/safety_gates/` |

---

## 9. Digital Twin / Simulation Layer

| Component | Status | Location |
|-----------|--------|----------|
| Clone infrastructure | ⚠️ Partial | `app/safety_gates/sandbox_gate.py:KubernetesSandboxExecutor` |
| Run fix simulation | ✅ Complete | `app/service_map/simulation.py` |
| Estimate impact | ✅ Complete | `app/dependency_graph/blast_radius.py` (see §1 — `app/risk/blast_radius.py` is unwired and uses a fixed fake topology) |
| Predict downtime | ⚠️ Partial | Basic prediction in `app/predictive/` |
| Rollback simulation | ✅ Complete | `app/safety_gates/sandbox_gate.py:verify_rollback` |

---

## 10. Sandbox Test (Gate 3)

| Component | Status | Location |
|-----------|--------|----------|
| Dry-run infra changes | ✅ Complete | `app/safety_gates/sandbox_gate.py` |
| Verify rollback | ✅ Complete | `app/safety_gates/sandbox_gate.py` |
| Test before execute | ✅ Complete | `app/safety_gates/orchestrator.py` |

**Notes:** Full 3-layer safety gate system with local and Kubernetes sandbox executors.

---

## 11. Continuous Learning

| Component | Status | Location |
|-----------|--------|----------|
| Replay incidents | ✅ Complete | `app/replay.py` |
| Measure accuracy | ✅ Complete | `app/eval/metrics.py` |
| Train finetuned models | ⚠️ Partial | Infrastructure exists in `app/eval/pipeline.py` |
| A/B test strategies | ⚠️ Partial | Framework in place, needs expansion |
| Human feedback ingestion | ✅ Complete | `app/feedback.py` |
| Playbook auto-generation | ✅ Complete | `app/playbook_marketplace/ai_generator.py` |
| Knowledge graph update | ✅ Complete | `app/knowledge_graph/sync_engine.py` |

---

## 12. Full Audit Trail & Compliance

| Component | Status | Location |
|-----------|--------|----------|
| Decision tracing | ✅ Complete | `app/audit/logger.py` |
| Agent reasoning logs | ✅ Complete | `app/agents/protocol.py` |
| Confidence + policy gate logs | ✅ Complete | `app/safety_gates/models.py` |
| Immutable event log | ✅ Complete | `app/audit/logger.py` |
| Queryable compliance reports | ✅ Complete | `app/compliance/dashboard.py` |
| SOC2/HIPAA/PCI-DSS ready | ⚠️ Partial | SOC2/ISO27001/HIPAA controls implemented, needs real integrations |

**Notes:** Full audit logging with 40+ action types, compliance controls for SOC2/ISO27001/HIPAA/PCI-DSS/GDPR/NIST.

---

## 13. Platform Enablers

### A. Cost Optimizer

| Component | Status | Location |
|-----------|--------|----------|
| Token estimation | ✅ Complete | `app/spend.py` |
| Latency tracking | ✅ Complete | `app/observability/metrics.py` |
| GPU availability | ✅ Complete | `app/cost_optimizer/gpu_tracker.py`, served at `/api/cost/gpu/*` |
| Budget-based model selection | ✅ Complete | `app/model_router/cost_optimizer.py` |
| Budgets, thresholds and alerts | ✅ Complete | `app/cost_optimizer/budget_manager.py`, served at `/api/cost/budgets/*` |
| ROI attribution for automation | ✅ Complete | `app/cost_optimizer/roi_tracker.py`, served at `/api/cost/roi/*` |
| Prompt compression | ✅ Complete | `app/cost_optimizer/token_optimizer.py`, served at `/api/cost/tokens/*` |

**Notes:** Three packages in this repo have "cost" in the name and only one was
reachable. `app/model_router/cost_optimizer.py` (wired) prices LLM calls and enforces
a per-customer monthly cap. `app/cost_optimization/analyzer.py` remains unwired.
`app/cost_optimizer/` — the largest of the three at 3,526 lines, with no importer and
no test — was wired on 2026-08-20 at `/api/cost/*`, minus its `model_router.py`
submodule, which is a second implementation of the already-wired `AdaptiveModelRouter`
and is proposed for deletion rather than a second routing endpoint that disagrees
with the first.

Wiring it turned up the same fabrication pattern as `dependency_graph`:
`GPUTracker.forecast_demand` generated 30 days of usage history from an arithmetic
formula, projected from it, and returned a "reserve N GPUs" recommendation — a
spending commitment derived from a fleet it had never looked at. It now forecasts
only from usage observed during cloud scans and returns `status:
"insufficient_data"` below seven observations. Spot-conversion savings now quote the
live spot price where one was fetched and label the static reference range as "not a
live quote" where one was not.

These routes are **not** plan-gated: `PlanFeatures` has no cost/ROI/GPU flag, and
adding one is a pricing decision rather than a wiring decision.

### B. AI Observability

| Component | Status | Location |
|-----------|--------|----------|
| Latency | ✅ Complete | `app/observability/` |
| Token usage | ✅ Complete | `app/spend.py` |
| Confidence | ✅ Complete | `app/verification/` |
| Hallucination rate | ✅ Complete | `app/verification/hallucination_detector.py` |
| Failure rate | ✅ Complete | `app/observability/metrics.py` |
| Model cost | ✅ Complete | `app/cost_tracking/` |
| Approval % | ⚠️ Unwired | `app/analytics_advanced/human_loop.py` — nothing imports it |
| Success % | ⚠️ Unwired | `app/analytics_advanced/ai_performance.py` — nothing imports it |

### C. Security Layer

| Component | Status | Location |
|-----------|--------|----------|
| Secrets Manager | ✅ Complete | `app/secrets_vault.py`, `app/secrets_advanced.py` |
| Vault integration | ⚠️ Partial | Custom implementation, needs HashiCorp Vault client |
| IAM/OIDC | ✅ Complete | `app/sso/`, `app/tenancy/oauth.py` |
| RBAC | ✅ Complete | `app/rbac/` |
| Encryption | ✅ Complete | Multiple modules |
| Key rotation | ✅ Complete | `app/security/secret_rotation.py` |
| Zero Trust | ⚠️ Partial | Session binding exists, needs full implementation |

### D. Human Collaboration Layer

| Component | Status | Location |
|-----------|--------|----------|
| Approve/Reject | ✅ Complete | `app/graph/nodes.py:approval_node` |
| Edit Plan | ✅ Complete | `app/coordinator/replanner.py` |
| Suggest Fix | ✅ Complete | `app/ai_fix/generator.py` |
| Override | ✅ Complete | `app/autonomy.py` |
| Explain | ✅ Complete | Multiple modules |
| Feedback | ✅ Complete | `app/feedback.py` |

### E. Reliability Layer

| Component | Status | Location |
|-----------|--------|----------|
| Checkpoints | ✅ Complete | `app/coordinator/dag_executor.py` |
| Retries | ✅ Complete | `app/resilience/`, `app/error_handling/retry.py` |
| Fallback Model | ✅ Complete | `app/model_router/adaptive_router.py:fallback_models` |
| Circuit Breaker | ✅ Complete | `app/error_handling/circuit_breaker.py` |
| Dead Letter Queue | ⚠️ Unwired | `app/reliability/dead_letter_queue.py` (579 lines, complete) — nothing imports it |

### F. Background Task Execution

| Component | Status | Location |
|-----------|--------|----------|
| In-process job queue | ✅ Complete | `app/jobqueue.py`, started in the API lifespan (`app/api/main.py:392`); Redis-backed when `COPILOT_REDIS_URL` is set, with retries and a dead-letter path |
| Out-of-process worker | ✅ Deployed | `app/tasks/`, run by the `worker` service in `docker-compose.yml` and `deploy/helm/copilot/templates/worker-deployment.yaml` (`worker.enabled=false` by default) |
| Notification delivery (Slack, PagerDuty) | ✅ Complete | `app/tasks/tasks.py:send_notification` |
| Metered usage sync to Stripe | ✅ Complete | `app/tasks/tasks.py:sync_usage_to_stripe` |
| Out-of-process investigations | ⚠️ Blocked | `app/tasks/tasks.py:run_investigation` — runs, but the report is unreadable (see below) |
| Out-of-process runbook execution | ✅ Complete | `app/tasks/tasks.py:execute_runbook_async` — reads definitions from and writes progress to `runbook_definitions`/`runbook_runs`, so the returned `execution_id` resolves through the API |
| Scheduled/periodic jobs | ❌ None | `app/tasks/worker.py:SCHEDULED_JOBS` is empty; `--mode scheduler` exits non-zero |
| Webhook offloading | ❌ Not wired | `app/tasks/tasks.py:process_webhook` acknowledges without acting; the API's webhook routes handle synchronously |

**Notes:** There are two job systems, not one. `app/jobqueue.py` is wired and running
— but its consumer is an asyncio task *inside the API process*, so queued
investigations compete with request handling even when Redis holds the queue.
`app/tasks/` (Dramatiq, 3 modules) is the out-of-process system and had never run:
four of its seven actors called `asyncio.get_event_loop().run_until_complete`, which
raises `RuntimeError: There is no current event loop in thread` on Python 3.12+, and
Dramatiq executes actors in worker threads. Nothing in the codebase enqueued any
actor, which is why that never surfaced. The image also installed only the `postgres`
extra, so a worker container would have died on `import dramatiq`.

What a worker can and cannot do is decided by process boundaries, and the split is
uneven. `send_notification` and `sync_usage_to_stripe` are fire-and-forget outbound
HTTP built from env settings and their own arguments — exactly what a worker is for,
and both are deployed. `execute_runbook_async` was in the same position until runbook
state moved to Postgres: it now loads the definition from `runbook_definitions` and
writes progress to `runbook_runs`, so a worker can run a runbook an operator authored
through the API and the `execution_id` it returns resolves through
`GET /api/runbooks/executions/{id}`. `run_investigation` still hands back a result
nobody can read — no result backend is configured, and the session it creates lives in
the worker's memory, so the API's lookup endpoint 404s on the ID returned. It becomes
useful once session state is externalised the same way.

The scheduler was removed rather than deployed. Its two jobs could not work from a
scheduler process: session cleanup cannot reach `app.api.main._SESSIONS`, and alert
evaluation was being sent an empty service list — `AlertEngine.evaluate_all([])`
iterates services inside its rule loop, so it evaluated nothing on every 60-second
tick for as long as it ran, while logging `"status": "completed"`.

Giving it a real service list would have been worse, and finding out why turned up a
shipped bug in the alert engine. Rules and metric history live in a per-process
singleton fed through the API, so a scheduler-side engine is empty; `get_metric_value`
then returns `None` for every service, and the ABSENCE branch of `evaluate_rule`
treated `None` as "the service stopped reporting". It does not distinguish a metric
that was never sent from one that stopped — and the shipped `metrics-absence` default
rule watches `heartbeat` on service `*`, a metric nothing in the codebase records, at
high severity routed to Slack and PagerDuty. Any caller of `evaluate_all` paged for
every healthy service it was given, in the API as much as in a worker. ABSENCE now
requires prior history for that service/metric pair before it fires.

### G. Persistence & State

| Component | Status | Location |
|-----------|--------|----------|
| Customers, trials, billing usage events | ✅ Postgres | `app/customers/manager.py` → `crm_customers` / `crm_trials` / `crm_usage_events`, Alembic `002_crm` |
| ORM schema creation | ✅ Wired | `app/database/engine.py:ensure_schema`, called from the API lifespan |
| Investigation history & dashboard analytics | ✅ Postgres | `app/dashboard/metrics.py` → `investigations`, Alembic `003_investigations`; served by `GET /dashboard/metrics` and `/dashboard/trends` |
| Planning graphs, coordinator DAG executions | ✅ Postgres | `app/planning/store.py` → `planning_graphs`, `app/coordinator/store.py` → `coordinator_executions`, Alembic `004_planning_coord` |
| Runbook definitions & executions | ✅ Postgres | `app/runbooks/store.py` → `runbook_definitions` / `runbook_runs`, Alembic `005_runbooks`; built-ins seeded per org |
| RBAC, waitlist, dedup, dep-graph, cost | ⚠️ In-process | 6 further module-level dicts, listed in `deploy/helm/copilot/values.yaml` |
| In-flight execution control (cancel/pause/approve) | ⚠️ Per-pod, by nature | `ExecutionEngine._active_executions`, `DAGExecutor._pending_approvals` — live asyncio objects; needs a control channel, not a table |
| LangGraph checkpointer | ⚠️ SQLite by default | Postgres when `COPILOT_CHECKPOINT_DB` is set |

**Notes:** Authoritative business state lived in module-level Python dicts, which is
why the deployment is pinned to a single replica
(`tests/test_single_replica_until_state_externalised.py` enforces the pin *and* fails
once the last store is gone, so it retires itself rather than becoming a permanent
ceiling). The customers slice moved first because it is the one that costs money to
lose: customers, their trials, and the usage records billing is computed from were
destroyed on every restart, deploy and scale-down.

Moving it established that **the ORM layer had never run at all** on the repo's own
default configuration, for three independent reasons:

1. `app/database/models.py` and `app/oncall/models.py` declared columns as
   `postgresql.JSONB` / `postgresql.UUID`. Those types cannot be *compiled* outside
   PostgreSQL — `create_all` raised `CompileError (in table 'organizations', column
   'settings'): can't render element of type JSONB`. Both modules share one
   `Base.metadata`, so a single postgres-only column made all 27 tables uncreatable.
2. `migrations/versions/001_initial_schema.py` hardcoded the same `postgresql.JSONB()`
   in 7 columns, and `alembic.ini` defaults `sqlalchemy.url` to SQLite — so
   `alembic upgrade head` had never succeeded on the default URL either.
3. `init_db()` had **no caller anywhere in the codebase**. Even with the types fixed,
   nothing created the tables.

All three are fixed. Columns now use `sa.Uuid` and
`sa.JSON().with_variant(JSONB(), "postgresql")`, which render *identical* PostgreSQL
DDL — this is a portability fix, not a schema change, so a database that already ran
`001_initial` is unaffected. `ensure_schema()` runs from the API lifespan and
`close_db()` disposes the pool on shutdown. Both migrations were verified `upgrade
head` → `downgrade base` on SQLite, and the alembic-created and `create_all`-created
schemas were diffed via `PRAGMA table_info`/`index_list` with no drift.

No data migration was needed: the state being moved was already destroyed on every
restart. The tables are prefixed `crm_` and deliberately kept separate from the
existing `organizations`/`usage_records` pair — `UsageRecord` is a per-org monthly
aggregate for metered billing, while `CRMUsageEvent` is an individual event row, and
collapsing them would lose the per-event detail the CRM dashboard reads.

Three bugs surfaced only once the data was real rather than in-process, each now
covered by a test:

- **Billing counters were read-modify-write in Python**, so concurrent usage records
  lost counts. They now increment in SQL (`col = col + n`); a 10-way `asyncio.gather`
  pins it.
- **JSON columns are only marked dirty on assignment**, so `customer.tags.append(...)`
  silently discarded the conversion tags. The list is reassigned instead.
- **SQLite returns naive datetimes**, which made `Trial.is_active` raise `TypeError`
  against an aware `now()`. UTC is re-attached on read.

#### Investigation history (second slice)

The analytics layer was four parallel representations of "an investigation", none of
which met: `_investigation_store` (a dict, written only by the dashboard's own unit
tests), an `investigations` ORM table with **no reader and no writer**, the
`app/observability` investigation counters (never called, so the Prometheus gauges are
permanently zero — still true), and `app/incidents/store.py` (in-memory, demo only).
The ORM table was chosen and reshaped rather than a new one added.

What the table could not previously hold explains why it was never written: `org_id`
was `UUID REFERENCES organizations(id)`, but live org ids are strings (`org_abc123`)
minted by `app/tenancy/store.py`, a separate aiosqlite store that does not use this
schema at all. No real request could satisfy that foreign key. `003_investigations`
retypes it to `VARCHAR(128)` with no FK, makes `query` nullable, and adds the columns
the dashboard aggregates over. Both tables were provably empty everywhere, so the
migration recreates rather than alters them; `downgrade` says in its docstring that it
discards recorded history, because the 001 shape cannot represent it.

Recording is wired into all five completion paths in `app/api/main.py` — `/chat`,
`/approve`, both SSE streams, and the alert-triggered investigation. `/approve` was
additionally missing its SLO record entirely.

Counting is now the database's job. The unique index on `(org_id, thread_id)` makes
`record_investigation` an upsert, so a ten-turn conversation is **one** investigation
rather than ten; tokens accumulate across turns, and duration is measured from the
earliest turn's start.

Two invented numbers were removed on the way in, both of which reached the user as
fact:

- **A 45-minute MTTR "industry baseline"** was substituted whenever there was no
  previous period, so a fresh deployment whose first investigations averaged 10
  minutes was told MTTR had improved 78% — and that fiction was worth 25 of the 100
  health-score points. There is now no comparison without a measured previous period;
  `comparison_available` reports which case you are in, and the health score is out of
  `health_score_max` (75 when the MTTR band is unscoreable) rather than being scored
  out of 100 with a quarter of it unearnable.
- **Every rising metric was called an improvement.** `app/dashboard/trends.py` had no
  notion of polarity, so a 30% rise in MTTR was rendered as "mttr_minutes improved by
  30.0%". `MetricPolarity` is now explicit, inferred from the metric name only as a
  fallback, and emitted in the trend JSON so the reading is auditable.

`ROIMetrics.estimated_cost_savings_usd` is left at 0: it derives from
`time_saved_minutes`, and nothing in the pipeline measures a before/after. The
recording hook passes 0.0 rather than an estimate, so a customer is shown no ROI
figure instead of a flattering one. `tools_used` is likewise not populated from the
API hook yet — the turn result does not carry it.

The public API of `app/customers/` is now entirely coroutines, and the dataclasses it
returns are *detached read models* — a stale caller snapshot has no write path back to
the row.

#### Planning graphs and coordinator executions (third slice)

**Postgres, not Redis, and the reason matters.** The plan said Redis for these two.
`app/redis_pool.py:get_redis()` falls back to a process-global `MockRedis` dict when
`COPILOT_REDIS_URL` is unset — and that setting defaults to `""`, with the Helm chart
requiring an explicit `--set`. A Redis-backed move would therefore have left both
stores exactly as per-pod as they were on the default deployment, while the store list,
the Helm comment and this document all claimed they had been externalised. That is the
precise failure this whole effort exists to correct, and it would have let the replica
pin lift onto state that still was not shared. Both went to Postgres, as the two
earlier slices did.

Neither `TaskGraph` nor `DAGExecution` had a `from_dict`, so the round-trips were
written first. They are deliberately **separate from `to_dict()`**: `to_dict()` is the
*API response* shape — it carries a derived `progress` block and drops `_checkpoints`,
`accumulated_context`, `execution_levels` and per-task `output`/`error_traceback`, so
round-tripping through it would have silently discarded a graph's checkpoints and an
execution's resume state. `serialize()`/`deserialize()` are the *storage* shape.

Both tables use a **composite primary key `(id, org_id)`**. Graph and execution ids are
caller-supplied or short random hex, and neither router was tenant-scoped at all
before: `GET /api/planning/graphs/{id}` and `GET /api/coordinator/executions/{id}` would
hand over any org's data to anyone who named it, and `DELETE` would remove it. Putting
org in the key means a lookup that omits it cannot compile, rather than relying on every
future call site remembering to filter.

Three further defects were fixed on the way in:

- **`_active_executions` was written only after `executor.execute()` returned**, so
  `GET /coordinator/executions/{id}` answered 404 for the entire duration of a run —
  exactly the window in which a caller polls — and began answering once the answer no
  longer mattered. A row is now written before the DAG starts and updated when it ends.
  `tests/test_coordinator_persistence.py::test_the_record_exists_while_the_dag_is_still_running`
  holds a handler open and asserts the row is visible and `running` while it is held.
- **The execution id was never returned to the caller.** `/dags/{id}/execute` and
  `/investigations` replied `{"status": "started"}`, so even after a run the only way to
  find it was to list everything. Both now return `execution_id`.
- **`delete_graph` and `remove_task` always claimed success**, so a typo'd id read as a
  completed delete. Both report what actually happened.

`ensure_schema()` was unlocked, on the documented reasoning that
`create_all(checkfirst=True)` is idempotent. It is idempotent only against a *completed*
creation: it issues a SELECT and then a CREATE, so two coroutines reaching a fresh
database together both see no tables and the loser raises `table organizations already
exists`. The API lifespan hides this behind a request, but a worker, a script, or — as
here — a background task racing a poll hits it. The lock is keyed by running event loop,
because an `asyncio.Lock` binds to the loop that first awaits it and raises on every
other one.

**What did not move, and cannot by adding a table.** `ExecutionEngine._active_executions`
and `DAGExecutor._pending_approvals` hold live asyncio tasks and futures.
`/planning/executions/{id}/cancel|pause` and `/coordinator/.../approve` act on those
objects: a row in Postgres cannot be cancelled, and an approval cannot be delivered to a
future on another pod by writing one. Routing those commands needs a control channel
(pub/sub or a work queue). Both are now tracked explicitly in
`tests/test_single_replica_until_state_externalised.py` under `PROCESS_LOCAL_RUNTIME_STATE`,
with a second AST check for instance attributes, so "the coordinator is persisted now"
cannot be misread as permission to lift the pin.

One behaviour change worth stating: concurrent mutations of a single graph are now
last-write-wins, where the shared dict made them additive. Two requests adding a task at
the same instant each read, mutate and write the whole state, so one task is lost. It is
accepted because a graph is built by one operator issuing sequential calls; anything
needing concurrent writers wants per-task rows rather than a JSON column.

#### Runbooks (fourth slice)

This one was not only a storage problem. The runbook API had three independent defects
at once, and fixing any one alone would have left it lying about something else.

**No runbook was ever executed.** `POST /runbooks/{id}/execute` took a `BackgroundTasks`
parameter and never scheduled anything on it. It wrote a record with status `running`
and returned; nothing advanced it, and its `steps` list stayed empty forever. `approve`
did the same thing — set a status field to `running` and return. `RunbookExecutor`, which
actually runs steps, existed in `app/runbooks/executor.py` and had no caller anywhere in
the request path. So an operator who triggered a mitigation during an incident got a
record that said "running" indefinitely, from an API that had never run a runbook step.
This is the same pattern the earlier orphan-wiring work kept turning up: an unwired
module beside a wired one that fabricates its output.

**`GET /runbooks/executions` was unreachable.** It was declared *after*
`GET /runbooks/{runbook_id}`, and FastAPI matches in declaration order, so every request
resolved as a lookup of a runbook whose id happened to be `executions` and returned 404
"Runbook not found". The literal routes now precede the parameterised one and
`test_literal_execution_routes_are_declared_before_the_parameterised_one` pins the
ordering — re-adding a route above them would otherwise reintroduce the bug with no
symptom beyond a 404 that reads as an ordinary missing runbook.

**The two dicts.** `_runbooks` was seeded at import from `DEFAULT_RUNBOOKS` and written
to by `POST /runbooks`, so a runbook authored during an incident lived only on the
replica that served the request and was gone at the next restart — the worst possible
lifetime for a document whose whole purpose is to exist the next time the same thing
breaks. Both now live in Postgres under `(id, org_id)`, like the earlier slices.

The built-ins are seeded **per org** rather than shared. They are starting points an
operator is expected to edit or disable, and the single process-wide copy meant one
tenant's edit rewrote every tenant's; `is_builtin` records which rows came from the seed.

Executor changes, all additive:

- `execute()` takes an optional `runbook` and `execution_id`. The router keeps
  definitions per tenant, so resolving them through the executor's process-wide registry
  would let two orgs sharing a runbook id run whichever was written last; and the id must
  be known before the run starts, because the response returns it for polling.
- `on_progress` is awaited after every step, so a caller polling a long runbook sees it
  advance. A run that reports only its final state is indistinguishable from a hung one.
  A raising hook is logged and swallowed — the hook is bookkeeping, and a step that
  really ran must not be un-run because recording it failed.
- `resume(execution, runbook, from_step_id)` was split out of `approve()`, which could
  only ever continue a run whose `_pending_approvals` entry was in *this* process. An
  approval that landed on a different replica than the one that paused the run could
  never be actioned; the execution simply stayed `waiting_approval` forever. Resume
  refuses when `from_step_id` is no longer in the runbook — resuming by position after an
  edit would run whatever now occupies that offset, which for a file full of scale and
  drain actions is how the wrong cluster gets touched.

Smaller corrections: rejection is recorded as `skipped` rather than `failed` (a human
declining is not the runbook going wrong, and the old behaviour inflated the failure rate
of every runbook anyone thought better of); unknown `execution_mode`/`step_type` values
now 422 with the valid list instead of raising `ValueError` out of the route as a 500;
`limit` is bounded; `delete` reports what it actually did; and timestamps are
timezone-aware, since SQLite drops the offset and a naive string reads as local time.

The executor's fail-closed behaviour is now observable end to end: a `command` step with
no command runner configured produces a **failed** execution carrying "refusing to report
success for an action that was not performed", where previously the status field simply
never moved off `running`.

---

## Remaining Gaps (Priority Order)

### High Priority (Production Readiness)

| # | Gap | Description | Effort |
|---|-----|-------------|--------|
| 1 | HashiCorp Vault Integration | Replace custom secrets with Vault client | 2-3 days |
| 2 | OPA Integration | Add real OPA policy engine client | 2-3 days |
| 3 | Dead Letter Queue | Implement DLQ for failed tasks | 1-2 days |
| 4 | GPU Availability Tracking | For GPU-accelerated models | 1 day |
| 5 | Zero Trust Security | Complete mTLS, network policies | 3-5 days |

### Medium Priority (Feature Completeness)

| # | Gap | Description | Effort |
|---|-----|-------------|--------|
| 6 | Success Probability Module | Dedicated module for action success prediction | 2-3 days |
| 7 | Deployment Timeline Enhancement | Full timeline with correlation | 2-3 days |
| 8 | A/B Testing Framework | Complete experiment infrastructure | 3-5 days |
| 9 | Model Fine-tuning Pipeline | End-to-end fine-tuning workflow | 5-7 days |
| 10 | Downtime Prediction | ML-based downtime forecasting | 3-5 days |

### Low Priority (Nice to Have)

| # | Gap | Description | Effort |
|---|-----|-------------|--------|
| 11 | Kyverno Integration | K8s-native policy engine | 2-3 days |
| 12 | Real K8s Digital Twin | Vcluster-based sandbox | 5-7 days |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DevOps Copilot Platform                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │   Alerts    │  │   Metrics   │  │    Logs     │  │   GitHub    │       │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘       │
│         │                │                │                │               │
│         └────────────────┴────────────────┴────────────────┘               │
│                                   │                                         │
│                    ┌──────────────▼──────────────┐                         │
│                    │   Event Intelligence Layer   │                         │
│                    │  ┌─────┐ ┌─────┐ ┌────────┐ │                         │
│                    │  │Dedup│ │Noise│ │Severity│ │                         │
│                    │  │     │ │     │ │Predict │ │                         │
│                    │  └─────┘ └─────┘ └────────┘ │                         │
│                    └──────────────┬──────────────┘                         │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐   │
│  │                     Adaptive Model Router                            │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │   │
│  │  │Complexity│  │   SLA    │  │ Customer │  │   Cost   │            │   │
│  │  │ Analyzer │  │ Router   │  │  Tier    │  │Optimizer │            │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐   │
│  │                     Agent Orchestration                              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │   │
│  │  │Coordinator│  │Investigator│ │Remediation│ │ Reviewer │            │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐   │
│  │                    3-Layer Safety Gates                              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐                          │   │
│  │  │Confidence│  │  Policy  │  │ Sandbox  │                          │   │
│  │  │   Gate   │→ │   Gate   │→ │   Gate   │                          │   │
│  │  └──────────┘  └──────────┘  └──────────┘                          │   │
│  └────────────────────────────────┬────────────────────────────────────┘   │
│                                   │                                         │
│  ┌────────────────────────────────┼────────────────────────────────────┐   │
│  │                    Knowledge & Memory                                │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │   │
│  │  │Knowledge │  │  Vector  │  │ Incident │  │ Service  │            │   │
│  │  │  Graph   │  │  Store   │  │  Memory  │  │   Map    │            │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Platform Enablers                                 │   │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐│   │
│  │  │ Audit  │ │Compliance│ │Security│ │  RBAC  │ │Billing │ │  SSO   ││   │
│  │  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘│   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Recommendations

The ordering below is deliberate: each step is a prerequisite for the one after it,
and the table above is only trustworthy from the bottom up. "Feature-complete" was the
previous verdict here, and it did not survive contact with the code — the features
existed, but several were unreachable, unbacked by storage, or reporting on data they
had invented.

1. **Decide the fate of every unwired module** — wire it or delete it, never "leave
   it". Every orphan audited so far turned out to be hiding fabricated data or code
   that had never once executed; an unwired module is not neutral, it is an unverified
   claim in the table above. *(Done for `deduplication`, `dependency_graph`,
   `cost_optimizer`, `tasks`.)*

2. **Move authoritative state out of process memory**, in cost-of-loss order:
   customers/billing → investigation history → coordinator executions and planning
   graphs → runbooks. *(All four done — see Platform Enablers §G. 6 module-level stores
   remain, plus the two live-object stores that need a control channel rather than a
   table; the replica pin lifts once those are handled.)*

3. **Enforce or remove the plan flags.** `PlanFeatures` declares 37 fields and 26 of
   them are referenced nowhere outside `app/billing/` — 22 boolean flags plus all four
   `limit_*` quotas. A flag that gates nothing is a billing promise the code does not
   keep, and `hipaa_compliance` / `soc2_compliance` / `air_gapped_deployment` are
   promises no flag *can* keep. Enforce the four quotas, then delete what cannot be
   backed. *(4 flags enforced so far: the deduplication and dependency-graph ones.)*

4. **Then scale.** Postgres checkpointer, Redis, and distributed locks for the 17
   module-level `asyncio.Lock()` singletons — only after step 2, since raising
   replicas before then corrupts billing data rather than merely losing throughput.

5. **Reliability sweep.** Triage the 456 bare `except Exception` handlers starting with
   `mcp/`, `api/`, `coordinator/`; fix the 9 `time.sleep` calls (8 of them in
   `app/mcp/http_client.py`) and the synchronous `requests` calls in async paths
   first — each one blocks the event loop for every concurrent request, not just its
   own.

Deferred until the above lands: Vault, OPA, Zero Trust, and the coverage ladder. They
are real gaps, but hardening a system whose state does not survive a restart optimises
the wrong layer.

---

*Originally written against the codebase as of 2026-08-02; §4 Planning, §5 Coordinator,
Platform Enablers §A, §F and §G, the runbooks slice, and these recommendations updated
2026-08-20.*
