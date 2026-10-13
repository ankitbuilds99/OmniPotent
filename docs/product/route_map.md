# Route and contract map

The frontend route is only considered available when it has a meaningful
backend contract or an explicit unavailable state.

| Product area | Frontend | Backend boundary | Tenant-sensitive data |
|---|---|---|---|
| Overview | `Dashboard` | dashboard/health/service summaries | services, incidents, source health |
| Incidents | `Incidents` | incident API and event stream | incidents, alerts, timeline |
| Investigations | workbench/report views | LangGraph launch, progress, evidence | runs, evidence, reports |
| Services | service/catalog views | service catalog and discovery | service identity and mappings |
| Integrations | `IntegrationMarketplace`, onboarding | connector metadata and health | credentials metadata, capabilities |
| History | `History` | persisted incidents/investigations | reports and source links |
| Settings | profile/team/admin pages | organization, roles, preferences | membership and authorization |
| Reliability | SLO views | SLI/SLO calculations | metric definitions and windows |
| Runbooks/remediation | runbook/action views | policy, approval, runner | intents, approvals, audit |

## API contract rules

- Every request derives tenant and user identity server-side.
- Resource identifiers are checked against the current organization before
  reading, streaming, exporting, or mutating.
- Connector results use explicit source status and freshness; absent data is
  not converted to a healthy numeric value.
- Long-running investigations expose stable event IDs and replay/cursor
  semantics; reconnecting must not duplicate action work.
- Errors use safe categories: unauthorized, forbidden, not found, conflict,
  unavailable, stale, invalid scope, and internal failure.

## Durable entities

`tenant_id`, `user_id`, `incident_id`, `service_id`, `investigation_id`, and
`evidence_id` are durable identifiers. All event, memory, cache, connector,
approval, and export keys must include the applicable tenant boundary.
