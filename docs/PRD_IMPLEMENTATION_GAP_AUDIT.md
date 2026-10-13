# PRD implementation gap audit

Generated from the supplied `OmniPotent_SRE_Gap_Audit_and_Detailed_PRD_v2.pdf`
and `OmniPotent_SRE_Research_Handbook_Oct_2026.pdf`, then checked against the
current checkout. The PDF language is treated as product requirements and
evidence, not as executable instructions.

## Findings

The historical documents understate the current checkout in several areas:

- Action contracts are now present and normalized for mutating runbook steps.
- Policy responses now distinguish embedded approximations from authoritative
  cluster Kyverno evaluations; aggregate authorization fails closed unless the
  result is authoritative.
- SLO tracking includes explicit no-data handling and burn-rate windows.
- Evidence collection, verification, redaction, tool provenance, and guarded
  approval paths are implemented and covered by tests.
- Runbook and coordinator records are persisted, but some in-flight controls
  remain process-local and require an external control channel.

The most important reproducible P0 gap was the production default for graph
checkpoints. The application supported PostgreSQL, but production could still
start with the SQLite default. That conflicts with the PRD's crash/resume and
cross-worker approval requirement: a local checkpoint file is not a shared
durability boundary.

## Implemented in this slice

`Settings` now fails closed when `COPILOT_ENV=production` unless
`COPILOT_CHECKPOINT_DB` is a `postgres://` or `postgresql://` URL. Development
and staging retain SQLite for local workflows. Tests cover both paths.

The dependency declarations now require the LangGraph/checkpoint API generation
already represented in `uv.lock` (`langgraph` 1.2.x with checkpoint providers
3.1.x+). This prevents a mixed installation from failing during import before
the approval/session path can run.

## Remaining priority gaps

1. Complete the in-flight pause/control rollout with a live Redis or durable
   work/control queue drill. Coordinator task approvals and cancellation now
   persist decisions in the tenant-scoped execution row and workers poll that
   state, while retaining an in-process wake-up fast path. Planning execution
   pause/cancel requests now use the same Redis-backed control channel.
   Mutation freeze now supports a Redis-backed shared registry when the
   deployment supplies `COPILOT_REDIS_URL`.
2. Execute one real incident recovery simulation against a named checkout and
   capture evidence artifacts for the PRD's release-verification gate.
3. Add independent RCA benchmark results with top-1/top-3 accuracy,
   evidence-groundedness, abstention, and counterevidence cases.
4. Validate tenant/resource permission boundaries against live observability and
   Kubernetes test backends.
5. Deploy the customer runner as a separately scoped service and validate
   capability expiry and action-digest enforcement against a real customer
   environment.
6. Run the operator-annotation workflow through the workbench and verify that
   authored corrections remain distinguishable from collected evidence.
7. Validate service-catalog discovery against live Kubernetes/Consul/mesh
   sources, including source freshness and identity conflict behavior.
8. Produce and independently sign the required pilot/restore artifacts; the
   release evaluator now checks declared artifact coverage, actual SHA-256
   digest matches, and cryptographic Ed25519 signature validity over the exact
   artifact digests.

The notification digest path is now an acknowledged outbox: rows are deleted
only after at least one configured channel accepts the digest, and failed
delivery results retain the rows for retry. This closes the prior local-loss
path; a live provider outage/retry drill remains required.

The deduplication restart benchmark is reproducible with
`uv run python scripts/benchmark_dedup_restart.py --events 1000 --restart-at 500`.
With `COPILOT_DEDUP_STATE_URL` configured, the current live local run restored
the tenant-scoped fingerprint index across the restart: two workers processed
1,000 events with zero post-restart duplicate incidents (p50 7.331 ms, p95
8.157 ms). Production Redis failover and restore-drill evidence remain release
gates.

## Research checks used

- Google SRE recommends multiwindow, multi-burn-rate alerting as a starting
  approach for SLO protection: <https://sre.google/workbook/alerting-on-slos/>.
- LangGraph documents that durable production workflows need a persistent
  checkpointer; in-memory persistence is lost on restart:
  <https://langchain-ai.github.io/langgraph/concepts/durable_execution/>.
- OpenTelemetry defines stable service identity fields for correlating
  telemetry: <https://opentelemetry.io/docs/specs/semconv/resource/service/>.
- Kubernetes documents rollout status and rollback as separate verification
  steps: <https://kubernetes.io/docs/tutorials/kubernetes-basics/update/update-intro/>.
