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
| Model cost | ✅ Complete | `app/cost_tracking/`, `copilot_llm_cost_dollars` + `copilot_llm_calls_unpriced_total` |
| Approval % | ⚠️ Unwired | `app/analytics_advanced/human_loop.py` — nothing imports it |
| Success % | ⚠️ Unwired | `app/analytics_advanced/ai_performance.py` — nothing imports it |

The model-cost row gained a second metric because the first one was quietly incomplete.
`copilot_llm_cost_dollars` is deliberately not emitted for a model whose price the
catalog has not verified — recording a guess would corrupt every dashboard built on it.
But a series that is absent graphs as zero, so "we have no price for this model" and
"this model cost nothing" drew the same line, and the warning explaining the difference
went to the logs, which is not where anyone reading a cost graph is looking. This was
not hypothetical: the default Anthropic model, `claude-opus-4-8`, is one of the unpriced
rows, so the default configuration reported no cost at all.
`copilot_llm_calls_unpriced_total` counts the omissions next to the number they
undercut; alert on it above zero, and its `model` label names the catalog row to fill.

The catalog's own resolver was the other half. Its prefix fallback exists so a dated
snapshot (`claude-sonnet-4-20250514`) can inherit its base model's price, but a bare `-`
boundary cannot tell a date stamp from a version bump, so an uncatalogued point release
— `claude-opus-4-5`, `-4-6`, `-4-7` — resolved to `claude-opus-4` and was billed at
$15/$75 against a 32k output cap. That is the same threefold over-report as the
`m in model` substring bug the catalog was built to end, reached by a longer route, and
the confidence is what makes it costly: a wrong number reaches the dashboard, while
`None` would have reached `copilot_llm_calls_unpriced_total` and asked for a real price.
`resolve()` now accepts only an eight-digit snapshot stamp as an inheritable suffix.

### C. Security Layer

| Component | Status | Location |
|-----------|--------|----------|
| Secrets Manager | ✅ Complete | `app/secrets_vault.py`, `app/secrets_advanced.py` |
| Vault integration | ⚠️ Partial | Custom implementation, needs HashiCorp Vault client |
| IAM/OIDC | ✅ Complete | `app/sso/`, `app/tenancy/oauth.py` |
| RBAC | ✅ Complete | `app/tenancy/models.py` (4 roles × 8 actions, `can()`), enforced by `require_perm()` on 21 routes in `app/api/main.py` |
| Encryption | ✅ Complete | Multiple modules |
| Key rotation | ✅ Complete | `app/security/secret_rotation.py` |
| Zero Trust | ⚠️ Partial | Session binding exists, needs full implementation |
| Pre-commit secret scan | ✅ Complete | `scripts/scan_staged_secrets.py`, installed by `scripts/install-git-hooks.sh` |

The pre-commit row is new, and it is worth saying why it did not exist before: the
scanner did, and had never run. Its logic lived in a `python3 -c "..."` string whose
first statement was `Path(__file__).parent.parent`, and `__file__` is undefined under
`-c`, so every invocation died with a `NameError` before reaching even its own error
handling. The hook was also never installed — `.git/hooks` held only git's stock
samples, because the install step was a comment in the script's own header rather than
a command anyone ran. Three of the credentials found in cleartext on 2026-08-18 were in
files this hook would have been asked about.

Two further faults were policy rather than syntax, and both pointed the same way. The
script ended `except Exception: sys.exit(0)` under the comment "Don't block commit on
scanner errors", and `SecretsScanner.scan_git_staged` returned `[]` from its own bare
`except` — so a scan that could not run was indistinguishable from one that found
nothing, and the hook printed "✅ No secrets detected" over both. A detector that
reports clean when it fails is worse than no detector: it converts an unknown into an
all-clear, and whoever reads it stops looking. Both now raise `SecretsScanError`, the
entry point exits 2, and every non-zero exit blocks; `--no-verify` remains the escape,
typed deliberately by someone who has read what broke.

The scan also read staged *filenames* and then opened those paths on disk, so it
checked the working tree rather than the index. Stage a key, delete the line, forget to
re-stage, and it scanned the clean copy and passed the key through. It now reads each
staged blob with `git show :path`, and lists paths NUL-separated so a name containing a
quote or newline cannot come back escaped into a path that matches no file — which
`scan_file` had been skipping silently, again as "clean". The logic moved out of the
shell string into an importable module for one reason: a control that cannot be
imported cannot be tested, which is why this one was broken for its entire life.
`tests/test_secrets_precommit_hook.py` now drives it end to end, up to a real
`git commit` that git actually refuses.

The RBAC row previously cited `app/rbac/` — a second, entirely unwired implementation
(5 roles, its own `PermissionSet`, a `BaseHTTPMiddleware` that was never installed, and
`Team`/`TeamMembership` classes duplicating `app/teams/`). Nothing outside its own tests
imported it, so no route had ever been gated by it. Authorization that is not reachable
is not authorization, and a "✅ Complete" row pointing at it read as if 21 unprotected
routes were protected. It was deleted on 2026-08-20; the row now names the matrix that
is actually enforced.

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
| Investigation history & dashboard analytics | ✅ Postgres | `app/dashboard/metrics.py` → `investigations`, Alembic `003_investigations`; served by `GET /api/dashboard/metrics` and `/api/dashboard/trends` |
| Planning graphs, coordinator DAG executions | ✅ Postgres | `app/planning/store.py` → `planning_graphs`, `app/coordinator/store.py` → `coordinator_executions`, Alembic `004_planning_coord` |
| Runbook definitions & executions | ✅ Postgres | `app/runbooks/store.py` → `runbook_definitions` / `runbook_runs`, Alembic `005_runbooks`; built-ins seeded per org |
| Waitlist signups | ✅ Postgres | `app/waitlist/store.py` → `waitlist_signups`, Alembic `006_waitlist`; not org-scoped, positions from `MAX(position)+1` under a unique constraint |
| Teams, memberships, invitations | ✅ Postgres | `app/teams/store.py` → `teams` / `team_members` / `team_invitations`, Alembic `007_teams`; `slug` and `(team_id, user_id)` unique, "which teams am I in" derived rather than indexed by hand |
| OAuth identities & login state | ✅ Postgres | `app/auth/oauth.py` → `oauth_accounts` / `oauth_states`, Alembic `008_oauth`; the provider identity is the primary key, so racing first-logins resolve to one user, and state tokens are consumed exactly once by `DELETE ... RETURNING` rowcount |
| Audit trail (hash-chained) | ✅ Postgres | `app/audit/store.py` → `audit_events`, Alembic `009_audit`; one chain per org, position held by a unique `(org_id, seq)` so replicas can append concurrently |
| dedup, dep-graph, cost, SLO, GraphQL, webhook dedup | ⚠️ In-process | 12 module-level containers, listed in `deploy/helm/copilot/values.yaml` |
| Status page, incidents stores | ⚠️ In-process | 6 dicts on singleton instances (`app/status_page/store.py`, `app/incidents/store.py`) |
| In-flight execution control (cancel/pause/approve) | ⚠️ Per-pod, by nature | `ExecutionEngine._active_executions` / `._cancelled`, `DAGExecutor._pending_approvals` — live asyncio objects; needs a control channel, not a table |
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

#### Waitlist (fifth slice)

The only table in this series that is **not** tenant-scoped, and deliberately so: a
waitlist signup happens before the person has an organization, so there is nothing to
scope it to. The waitlist is a property of the product.

**What was being lost.** `_waitlist_store` and `_email_index` held leads — real email
addresses submitted through the public landing page, along with company, role and
use-case — in dicts emptied on every restart, deploy and scale-down. `export_waitlist`
existed but nothing scheduled it, so no copy was ever taken before the dict went away.

**`_position_counter` was worse than volatile.** It reset to 0 with the dicts, so the
first signup after a deploy was handed position 1 — a position an earlier signup had
already been promised. Position now comes from `MAX(position) + 1` computed inside the
insert, with a `UNIQUE` constraint behind it so two concurrent signups reading the same
maximum cannot both keep it: the loser takes an `IntegrityError`, re-reads, and retries.
Failing loudly and retrying is the point; the alternative is two people quietly holding
the same place in a queue.

**Referrals had never worked.** `POST /waitlist` passed `req.referral_code` — a code like
`REF1A2B3C4D` — as `referred_by`, and `add_to_waitlist` resolved that against
`_email_index`, which is keyed by **email**. The lookup could not match, so no referral
made through the public API was ever credited and the "3 referrals earns priority" rule
had never once fired. `tests/test_waitlist.py` passed an *email* as `referred_by`, which
is why it went unnoticed: the test exercised a path the API never took. Resolution now
tries referral code first (the thing that actually travels in a share link) and email
second, and both are covered.

An unresolvable referrer is now dropped rather than stored. Keeping the raw string would
have counted attribution that never landed toward `referral_rate` — a reported referral
rate higher than the number of referrals actually credited.

Everything the module exposes became `async`, including `WaitlistManager`; there is no
in-process copy of the waitlist left to read synchronously. Stats are aggregated in SQL
rather than by loading every row, because this is the one table here meant to grow
without bound. `ip_hash` continues to store a truncated SHA-256 and never the address.

#### Teams (sixth slice)

**This one was never on the list.** The four dicts lived on a singleton's *instance*
(`self._teams`, `self._members`, `self._invitations`, `self._user_teams`) rather than at
module scope, so the scan that produced the store inventory did not see them — while
`app/teams/router.py` had been mounted at `/api/teams` the whole time. Every team, every
membership and every outstanding invitation was destroyed on each restart, deploy and
scale-down. The invitations were the sharpest edge of it: an invitation is a promise made
to somebody *outside* the system, who has already been handed a link that then stopped
resolving.

**Two invariants the dicts could not enforce.** Moving the data without them would have
reproduced the same bugs in a table:

- **`teams.slug` is unique.** Auto-generated slugs were de-duplicated by scanning every
  team, but a caller-supplied slug was stored verbatim — so two teams could hold one,
  `get_team_by_slug` returned whichever the scan reached first, and the other was
  unreachable by its own URL. A slug the caller chose is now rejected with **409** if it
  is taken (renaming it would silently break the link they were about to publish), while
  one we generate is disambiguated with a counted suffix and the insert retried.
- **`(team_id, user_id)` is unique.** "Already a member" was a read-then-write check that
  two concurrent invitation accepts both passed. The constraint answers it now: one
  membership, one `None`.

**`_user_teams` got no table.** It was a hand-maintained index from user to team ids,
updated at four call sites — the kind of bookkeeping that drifts out of step with the
thing it indexes. "Which teams is this user in" is now a query over `team_members`, so it
cannot disagree with the memberships it is derived from.

**Two fixes carried along.** `update_team` applied any key that happened to be an
attribute of the model, so `owner_id` was writable through a settings PATCH; it now
writes `name` and `settings` only. And `DELETE /teams/{team_id}/invitations/{id}` checked
the caller's role against the `team_id` in the path but cancelled by id alone — an admin
of any team could cancel any invitation whose id they knew. The id is now scoped to the
team it belongs to.

Invitation expiry is still evaluated on read, as it was in memory — nothing sweeps the
table on a timer — but it is now written back when observed, and `accept_invitation`
re-checks `expires_at` rather than trusting the status, so an expiry nobody has looked at
is not an acceptable invitation. Timestamps moved from `datetime.utcnow()` to aware UTC:
these now round-trip through a column that carries an offset, and a naive value would
compare wrong rather than fail.

#### OAuth identity (seventh slice)

**Also not on the list, and the most damaging one found.** Three dicts —
`_OAUTH_ACCOUNTS`, `_OAUTH_STATES`, `_USER_ACCOUNTS` — behind `/auth/*`, which had been
mounted the whole time. They failed in two independent ways.

**Every restart quietly issued new identities.** The callback mints
`user_{token_hex(8)}` whenever the account lookup misses. With the accounts in a process
dict, every restart made every lookup miss, so each returning user came back as a
brand-new user — and the team memberships persisted one slice earlier, along with their
roles and history, stayed behind under an id nobody could sign in as again. Nothing
raised, nothing logged; the login simply succeeded as the wrong person. This is the
sharpest example of the pattern the whole exercise keeps turning up: the damage was not
the lost dict, it was the *durable* rows the lost dict stranded.

**Login was broken outright above one replica.** `_OAUTH_STATES` is written by
`/auth/{provider}/login` and read by `/auth/{provider}/callback` — a separate request,
arriving from the provider's redirect, that load-balances independently. Any pair that
split across pods failed with "Invalid or expired state parameter" and nothing in the
logs explaining why. So the CSRF state was not moved for durability; it was moved because
the flow is inherently cross-process.

**The identity is the primary key.** `oauth_accounts.id` keeps the natural key the dict
was keyed by, `"{provider}:{provider_id}"`, rather than taking a surrogate — which makes
"one local user per provider identity" a constraint the database enforces. Two racing
first-ever logins each mint a candidate `user_id`; the insert that loses gets
`IntegrityError`, re-reads, and returns the winner's id, and the callback adopts what it
is handed instead of the id it minted. Without that, the outcome was whichever request
committed last.

**Three smaller fixes.** State tokens are now consumed exactly once — the delete's
rowcount decides, because a `SELECT` then `DELETE` lets two concurrent replays both pass
the read. `find_account_by_email` is ordered by `(created_at, id)`: it decides which
existing user a newly-seen provider identity folds into, and an unordered `LIMIT 1` made
that a coin flip. And `existing.last_login = ...` was writing to a detached copy of a
row, so it updated nothing; `touch_last_login()` issues the write.

**`_USER_ACCOUNTS` got no table.** Like `_user_teams` before it, it was a hand-maintained
user → accounts index; it is a query now.

**What this does not fix.** Deployments already running have no record of the ids they
handed out, so the split identities cannot be reunited — the migration stops the bleeding
from here on and says so in its docstring.

#### Audit chain (eighth slice)

**The only store so far that was already broken on one pod.** The audit events lived in
a `deque(maxlen=2000)` and the running chain hash in a module-level `_LAST_HASH`. Both
backed `GET /audit` and `GET /audit/verify`, and the pair failed in two opposite
directions.

**It reported tampering that had not happened.** `verify_chain` recomputes the chain from
a genesis of `prev_hash == ""`. A bounded deque evicts its oldest entry once full, so
from the 2001st event onward the oldest survivor pointed at a hash no longer present and
the endpoint answered `{"valid": false, "broken_at": 0}` permanently — on a single
replica, with nothing wrong. That is the worst available failure for this control: it is
indistinguishable from a real detection, so the first genuine one would be read as the
same known false alarm. Confirmed by running it: valid at 2000 events, broken at 2001.

**And it attested to one pod's fraction of the trail.** Below 2000 events each replica
verified its own buffer against its own `_LAST_HASH` and answered `valid: true` — about
whichever slice of the events happened to land on the pod serving the request.

The events matter more than the usual store: `approval.decided` is written from the
websocket approval handler, so this is the record of who authorised which infrastructure
change.

**Position is now explicit, which is what makes concurrent appends safe.** A hash chain
is strictly serial, so several replicas appending at once must still land at distinct
positions each folding in the one before. `seq` counts from 0 within an org and
`(org_id, seq)` is unique: the losing insert gets `IntegrityError`, re-reads the head and
chains onto whatever landed. Same optimistic pattern as the waitlist's `MAX(position)+1`.
Nothing is evicted, so a gap in `seq` or a hash that does not fold into its successor is
now evidence rather than an artifact of the buffer having wrapped — the deque could not
distinguish a deleted event from a legitimate eviction at all.

**The first cut of that retry loop reintroduced the bug it was fixing**, and it is worth
recording because the failure was invisible by construction. It retried a fixed 8 times
with no backoff, then logged at ERROR and dropped the event. Two things were wrong. The
retries were in lockstep — every writer that lost a race woke immediately and collided
with the same participants — so six concurrent writers exhausted eight attempts often
enough to fail the slice's own concurrency test once in three runs. And the drop was
undetectable: a dropped event never claims a `seq`, so the entries around it stay dense
and keep folding into one another, and `/audit/verify` answers `valid: true` over a trail
that is quietly missing entries. That is the exact property `audit_events` exists to
provide, defeated by the write path rather than the storage. Appends now back off with
full jitter and, on exhaustion, raise `ContendedAppend` carrying what landed and what did
not; `flush()` requeues only the remainder, so nothing is lost and nothing is written
twice. Bounding the backlog is the queue's job, and it already counts what it sheds.

**Two fixes carried along.** `GET /audit` was not tenant-scoped: it returned every org's
events to whoever asked. It is scoped now, and `/audit/verify` reports per org, because a
single `valid` flag over a multi-tenant deployment hides *which* tenant's trail is broken.

**`record()` stays synchronous, and that is a deliberate trade.** Five of its 27 call
sites sit in sync functions (`sanitize_query`, three `_audit_log` helpers,
`record_feedback`) that would each have to become async and drag their callers with them,
and a synchronous database write from the other 22 would block the event loop. So
recording queues to a bounded hand-off buffer and a drain task started by the API
lifespan writes the chain, flushing on read and on shutdown. The honest cost: events
still queued when a process is killed are lost — a sub-second window, where before the
loss was every event on every restart. Closing it entirely needs an await at the call
site, which those five sync callers block. The queue is bounded and counts what it drops
rather than evicting quietly.

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
   graphs → runbooks → waitlist → teams → OAuth identity → the audit chain.
   *(All eight done — see Platform Enablers §G.)*

   **The inventory this step works from was wrong twice, in the same direction both
   times.** Teams and OAuth were each found by the slice that moved them, not by the
   list, and the reason was identical: the original scan matched only lower-case
   module-level names bound to a dict literal. Authoritative state hides in four
   shapes — module-level containers, instance attributes on a module-level singleton,
   in-memory implementations behind a `get_*_store()` factory, and containers built by
   a call (`defaultdict(list)`, `deque(maxlen=...)`) rather than written as a literal.
   The list has been rebuilt by scanning every module reachable from
   `app/api/main.py` in all four shapes and keeping what a wired endpoint reads or
   writes: **21 stores remain**, not the 4 previously recorded here — 12 module-level,
   6 on singleton instances, and 3 live-object stores that need a control channel
   rather than a table.

   The failure mode was not just an incomplete list, it was a *silently* incomplete
   one: the pin test keeps an entry only if the scan still finds it, so an entry that
   stops matching drops off and moves the pin one store closer to lifting.
   `test_every_declared_store_still_resolves` now fails if any declared entry stops
   resolving, so externalising a store is a deliberate two-part edit rather than
   something a scanner change can simulate.

   Worth stating plainly: reachable ≠ live. Several candidates were ruled out because
   they are reachable only through a package `__init__` re-export with no endpoint
   behind them (`app/observability/slo.py`, `app/oncall/compensation.py`), and caches
   over a durable layer, per-pod control machinery and prefilled constants are
   excluded on purpose — those are correct per-pod, and moving them would be wrong.

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
Platform Enablers §A, §F and §G, the runbooks, waitlist and teams slices, and these
recommendations updated 2026-08-20.*
