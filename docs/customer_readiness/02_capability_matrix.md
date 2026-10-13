# Customer readiness capability matrix

This matrix maps the customer-ready prompt pack to the current implementation.
`Implemented locally` means code and focused tests exist. `Partial` means a
material runtime, browser, provider, or operational proof is still missing.

| Prompt | Scope | Status | Next evidence or implementation |
|---|---|---|---|
| 00 | Engineering invariants | Implemented locally | Keep evidence boundary in every release review |
| 01 | Repository truth audit | Implemented | This audit and trace are maintained with the release commit |
| 02 | Journeys/contracts/IA | Partial | Complete journey acceptance and contract review |
| 03 | Auth/organizations/tenancy | Partial | Live two-tenant and OAuth/revocation acceptance |
| 04 | Functional application shell | Partial | Browser desktop/mobile/accessibility pass |
| 05 | Public website/docs/demo | Partial | Verify every CTA and configured submission path |
| 06 | Resumable onboarding | Partial | Real connector setup and first-evidence usability test |
| 07 | Connector framework | Partial | Complete live connector contract matrix |
| 08 | Prometheus/Alertmanager | Partial | Disposable provider integration and replay tests |
| 09 | Kubernetes read-only | Partial | Restricted namespace test against a disposable cluster |
| 10 | GitHub/log context | Partial | Scoped App/provider test and provenance acceptance |
| 11 | Service catalog/overview | Implemented locally | Live discovery and freshness validation |
| 12 | Incident workspace | Implemented locally | Browser workflow and provider webhook acceptance |
| 13 | Durable execution | Partial | Crash/restart, replay and multi-replica evidence |
| 14 | Evidence-first RCA | Implemented locally | Independent 30-case labeled benchmark |
| 15 | Investigation workbench/chat | Partial | Browser E2E, share authorization and keyboard checks |
| 16 | SLO/dependency views | Implemented locally | Live metrics and missing-data acceptance |
| 17 | Collaboration/memory | Partial | Reviewer workflow, tenant persistence and optional Slack test |
| 18 | Policy/approvals | Implemented locally | Maintain mutation bypass tests |
| 19 | Restricted restore runner | Partial | Approved disposable-cluster execution only |
| 20 | Independent recovery | Implemented locally | Real recovery drill with fresh observations |
| 21 | Security/quality gates | Partial | Signed threat model and complete CI/E2E evidence |
| 22 | Deployment/operations | Partial | Staging, restore, failover and observability drills |
| 23 | Customer pilot/analytics | Missing | Run authorized 3–5-user read-only pilot |
| 24 | Final release review | Missing | Execute full matrix after external gates pass |

## Current release classes

- Public demo: implementation exists, but CTA and browser acceptance are not
  fully evidenced.
- Read-only pilot: blocked by live connector, browser, tenancy, benchmark and
  customer evidence gates.
- Disposable test-cluster remediation: blocked by named-cluster execution and
  recovery evidence.
- Production remediation: intentionally disabled and not ready.
