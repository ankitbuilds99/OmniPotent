<div align="center">

<img src="docs/assets/logo.svg" alt="OmniPotent" width="120" />

# OmniPotent

### An AI SRE copilot that investigates incidents, proposes fixes, and asks before it acts

[![CI](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ankit25bcs10610/OmniPotent/branch/main/graph/badge.svg)](https://codecov.io/gh/ankit25bcs10610/OmniPotent)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/typescript-5.0+-3178c6.svg)](https://www.typescriptlang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)

[Quick start](#quick-start) · [See it work](#see-it-work) · [Architecture](#architecture) · [API](#api) · [Docs](docs/)

<img src="docs/media/omnipotent-demo.gif" alt="OmniPotent walkthrough: dashboard, incidents, service map, SLOs, analytics and notification routing" width="880" />

<sub><b><a href="docs/media/omnipotent-walkthrough.mp4">▶ Watch the full 3-minute walkthrough (MP4)</a></b> · every screen below is a real
render of the running app against a seeded database, not a mockup</sub>

</div>

---

## What it does

You page an on-call engineer at 3am because checkout is throwing 500s. They spend the first
twenty minutes doing the same thing every time: pull the error rate, find the deploy that
lines up with the onset, read the diff, check whether the pods are healthy, look for a
runbook someone wrote the last time this happened.

OmniPotent does that first twenty minutes. It queries your logs, metrics, traces, deploy
history and repository through [MCP](https://modelcontextprotocol.io/) tool servers, ranks
the hypotheses by the evidence it actually found, and writes up a root-cause report with
that evidence attached.

Then it stops. Remediation is a separate, gated step: an action only executes when the
measured confidence clears the threshold, the policy engine allows it, and the digital-twin
simulation verifies the rollback. Anything that fails a gate becomes an approval request
with a human's name on it. Autonomy is **off** by default, and dry-run is **on** even when
you turn autonomy on.

### At a glance

| | |
|---|---|
| **Investigation** | Evidence-ranked root-cause analysis across logs, metrics, traces, deploy history and code |
| **Safety** | Confidence gate, policy engine, digital-twin simulation, human approval — autonomy off by default |
| **Integrations** | 27 MCP tool servers and 8 direct connectors: Kubernetes, GitHub, Datadog, PagerDuty, Jira, Slack and more |
| **Enterprise** | Multi-tenancy, RBAC, SSO (OIDC + SCIM), metered billing, SHA-256 hash-chained audit log |
| **Deployment** | One command on a laptop (SQLite, no Docker required) through Kubernetes with Helm, ArgoCD and Terraform |

**Scope, stated plainly:** OmniPotent is a working platform — 368 documented API endpoints,
27 MCP tool servers, a 37-page operator console, and a test suite of 215 files gating every
merge. It has not yet undergone third-party compliance certification; the
[Security](#security) section below describes the controls that are implemented in code,
and nothing else.

---

## See it work

Screenshots are captured from the running app against a seeded demo database. Click any
image for full resolution.

### The console — where an investigation starts

<a href="docs/media/console.png"><img src="docs/media/console.png" alt="OmniPotent console with the MCP server sidebar and suggested investigation prompts" width="100%" /></a>

Ask a question in English. The left rail lists every MCP server the agent can reach and the
exact tools each one exposes — `search_logs`, `first_bad_deploy`, `correlate_changes` — so
you can see what the model has access to before it runs.

This shot was taken on a fresh checkout with no LLM key configured, which is why the model
card reads `NO API KEY` and the connectors read `OFFLINE DEMO`. It reports the state it is
actually in rather than rendering a fake answer.

### The dashboard — the fleet at a glance

<a href="docs/media/dashboard.png"><img src="docs/media/dashboard.png" alt="Dashboard showing active alerts, runbooks, pending approvals and service count" width="100%" /></a>

Every number on this page comes from an endpoint. Active alerts, runbooks, pending
approvals and service health are read at load; nothing is hardcoded.

### Incidents — ranked by real severity

<a href="docs/media/incidents.png"><img src="docs/media/incidents.png" alt="Incident list with severity, status, assignee and age columns" width="100%" /></a>

Incidents arrive from webhooks — `/api/webhooks/pagerduty`, `/alertmanager`, `/datadog`,
`/grafana` and `/generic` — from the Slack bot, or from the API. Deduplication collapses the
storm of alerts a single failure produces into one incident.

### Service map — blast radius, computed

<a href="docs/media/service-map.png"><img src="docs/media/service-map.png" alt="Service dependency graph with health status on each node and edge" width="100%" /></a>

The dependency graph is what the policy gate reads to answer "how much breaks if this
action goes wrong". Blast radius is derived from this topology, not declared by the caller.

### SLOs — error budgets and burn rate

<a href="docs/media/slo.png"><img src="docs/media/slo.png" alt="SLO tracker with four objectives, error budgets and burn rates" width="100%" /></a>

Objectives are fed by real measurements. A burn rate above 1.0 means the budget is being
spent faster than the window allows, and the page says so plainly.

### On-call — rotation and escalation

<a href="docs/media/on-call.png"><img src="docs/media/on-call.png" alt="On-call schedule grid, current responder, and a severity-1 escalation ladder" width="100%" /></a>

Schedules, overrides and per-severity escalation ladders. A page is only deliverable if a
responder was provisioned with a contact method, so the UI shows the contact method.

### Runbooks — typed steps, approval-gated

<a href="docs/media/runbooks.png"><img src="docs/media/runbooks.png" alt="Runbook builder with typed steps: kubernetes, command, condition, wait" width="100%" /></a>

Runbook steps are typed rather than free-form shell. The builder offers `command`,
`script`, `ai_action`, `approval`, `condition`, `notification` and `wait`; the execution
engine (`app/runbooks/models.py`) additionally understands `kubernetes`, `terraform`,
`http` and `slack`. A step can enter `waiting_approval` and block there, so a runbook does
not become a way around the safety gates.

<sub>The builder and execution-engine step vocabularies are being unified — `script` and
`ai_action` are builder-side only today.</sub>

---

## Architecture

Six layers. A request enters at the top and falls through each one in order.

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-3d-dark.svg" />
  <img src="docs/assets/architecture-3d-light.svg"
       alt="OmniPotent architecture as six stacked isometric layers: edge authentication, orchestration, agents, safety gates, integration and persistence."
       width="920" />
</picture>

</div>

### Explore it in 3D

<div align="center">

<a href="docs/architecture.html"><img src="docs/media/architecture-3d-live.png" alt="The same architecture rendered in interactive 3D, orbitable in the browser" width="880" /></a>

<sub><b><a href="docs/architecture.html">▶ Open the interactive version</a></b> — drag to orbit, scroll to zoom, click a
legend row to hide a layer. Clone the repo and open the file, or view it through
<a href="https://htmlpreview.github.io/?https://github.com/ankit25bcs10610/OmniPotent/blob/main/docs/architecture.html">htmlpreview</a>.</sub>

</div>

The static diagram above is generated, not hand-drawn — run `python scripts/gen_architecture.py`
to regenerate both theme variants. The layer list lives in one place in that script, so the
picture cannot quietly drift out of sync with the system it describes.

### What each layer does

| Layer | Responsibility |
|-------|----------------|
| **Edge** | OAuth2/JWT and API-key + HMAC authentication, rate limiting, RBAC, tenant resolution |
| **Orchestration** | Score the question's complexity, route it to a model, drive a LangGraph state machine with checkpoints |
| **Agents** | Gather evidence in parallel, converge on one ranked set of hypotheses |
| **Safety gates** | Confidence threshold, policy evaluation, digital-twin simulation, human approval |
| **Integration** | 27 MCP tool servers, 8 direct connectors, ITSM and chat, cloud and Kubernetes |
| **Persistence** | PostgreSQL for state, Redis for cache and pub/sub, object storage, hash-chained audit log |

### The agents

Five modules under `app/agents/`, each with a bounded job:

| Agent | Module | Job |
|-------|--------|-----|
| **Investigator** | `investigator.py` | Query tools, gather evidence, rank hypotheses against it |
| **Orchestrator** | `orchestrator.py` | Drive the state machine, decide what runs next |
| **Parallel coordinator** | `parallel_coordinator.py` | Fan out independent tool calls and reconcile the results |
| **Remediation** | `remediation.py` | Propose a reversible action and the rollback that undoes it |
| **Reviewer** | `reviewer.py` | Check the proposal against the evidence before a gate ever sees it |

### The path of one request

```
POST /api/v1/chat
      │
      ▼
  authenticate ─── rate limit ─── resolve tenant ─── check quota
      │
      ▼
  score complexity ─── pick model ─── enter LangGraph state machine
      │
      ▼
  investigator + coordinator fan out across MCP tool servers
      │           (logs, metrics, traces, deploys, repo, k8s, incidents)
      ▼
  rank hypotheses against the evidence actually returned
      │
      ▼
  remediation proposes a reversible action ─── reviewer checks it
      │
      ▼
  ┌─ confidence gate ──► below SAFETY_CONFIDENCE_THRESHOLD? ─┐
  ├─ policy gate ──────► blast radius / protected target? ───┤──► approval request
  └─ digital twin ─────► rollback not verified? ─────────────┘         │
      │                                                                ▼
      │ all gates pass AND COPILOT_AUTONOMY=true              a human decides
      ▼
  execute (dry-run unless COPILOT_AUTONOMY_DRYRUN=false) ─── append to audit chain
```

Gate decisions, approvals and executions are written to a SHA-256 hash-chained audit log
(`app/audit/`). Each entry's hash covers the previous one, so editing an old entry
invalidates every entry recorded after it.

---

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for the frontend)
- An API key for at least one LLM provider: Anthropic, OpenAI, Gemini, Groq or DeepSeek

Docker, PostgreSQL and Redis are all optional: without them the app falls back to SQLite
and an in-process cache, so a laptop with Python and Node is enough to run the whole thing.

### One command

```bash
git clone https://github.com/ankit25bcs10610/OmniPotent.git
cd OmniPotent
./start.sh all
```

`start.sh` checks your toolchain, installs dependencies, copies `.env.example` to `.env` if
you have no `.env` yet, and starts both processes:

| | |
|---|---|
| Web console | http://localhost:5173 |
| API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |

Other subcommands: `./start.sh setup` (interactive configuration wizard), `check`
(verify connections), `api`, `web`, `cli`.

To explore the product before configuring any integration, set `COPILOT_DEMO_MODE=true`
in `.env`: unconfigured connectors serve demo fixtures, with every simulated payload
labelled as such. Leave it off (the default) and unconfigured connectors fail closed.

### Manual setup

```bash
cp .env.example .env
# Set COPILOT_PROVIDER and the matching key, e.g.:
#   COPILOT_PROVIDER=anthropic
#   ANTHROPIC_API_KEY=sk-ant-...

uv sync --extra dev
uv run uvicorn app.api.main:app --reload --port 8000   # note: app.api.main, not app.main

cd frontend && npm ci && npm run dev                   # separate terminal
```

### Docker Compose

Brings up the API, a background worker, PostgreSQL, Redis, Prometheus, Grafana and Jaeger:

```bash
docker compose up -d
curl http://localhost:8000/healthz
```

---

## Configuration

`.env.example` is the authoritative reference — it documents roughly 150 variables inline,
including a "deliberately absent" section explaining which knobs were removed and why. The
ones you are most likely to touch:

| Variable | Default | Description |
|----------|---------|-------------|
| `COPILOT_PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `gemini` \| `groq` \| `deepseek` |
| `ANTHROPIC_API_KEY` | — | Key for the selected provider (or `OPENAI_API_KEY`, etc.) |
| `COPILOT_MODEL` | provider default | Override the main model |
| `COPILOT_FAST_MODEL` | provider default | Override the cheap model used for routing and summaries |
| `COPILOT_DATABASE_URL` | SQLite | PostgreSQL connection string |
| `COPILOT_REDIS_URL` | in-process | Redis connection string |
| `COPILOT_ENV` | `development` | `development` \| `staging` \| `production` |
| `COPILOT_RATE_LIMIT_PER_MIN` | `120` | Per-tenant request ceiling |
| `SAFETY_CONFIDENCE_THRESHOLD` | `0.80` | The single bar for "confident enough to change production" |
| `COPILOT_CONFIDENCE_GATE` | `true` | Enforce the confidence gate |
| `COPILOT_AUTONOMY` | `false` | Allow automated remediation at all |
| `COPILOT_AUTONOMY_DRYRUN` | `true` | Simulate rather than execute, even when autonomy is on |
| `COPILOT_MULTI_TENANT` | `false` | Enable per-tenant isolation and seats |
| `COPILOT_DEMO_MODE` | `false` | Serve labelled demo fixtures for unconfigured integrations (off = fail closed) |

`COPILOT_DATABASE_URL` and `COPILOT_REDIS_URL` each also accept the bare `DATABASE_URL` /
`REDIS_URL` that managed platforms (Render, Heroku, Helm, Docker Compose) inject by
convention, so a correctly provisioned database is not silently ignored.

Everything else is prefixed, and settings are declared `extra="ignore"` — a misspelled
variable is discarded without an error, which looks exactly like one that had no effect.
Check `/healthz` and `./start.sh check` rather than assuming a value took.

---

## API

368 documented paths, 415 operations. The full schema is served at `/docs` (Swagger) and
`/openapi.json`; see [docs/api-reference.md](docs/api-reference.md) and [docs/api/](docs/api/)
for prose.

### Start an investigation

```http
POST /api/v1/chat
Content-Type: application/json
Authorization: Bearer <token>

{
  "thread_id": "inv-checkout-500s",
  "message": "Why is the checkout API throwing 500 errors?"
}
```

```json
{
  "thread_id": "inv-checkout-500s",
  "status": "completed",
  "answer": "Connection pool exhaustion in billing-service...",
  "trace": ["search_logs", "first_bad_deploy", "get_commit_diff", "list_pods"],
  "report": {
    "hypotheses": [{ "summary": "...", "confidence": 0.94, "evidence": ["..."] }],
    "severity": "high",
    "postmortem": "..."
  },
  "tokens_used": 18422
}
```

`status` is one of `completed`, `awaiting_approval` or `error`. When it is
`awaiting_approval`, `approval_request` describes the action and the gate that stopped it.

### Stream it

```http
POST /api/v1/chat/stream     # Server-Sent Events, same body
```

### Approve a pending action

```http
POST /api/v1/chat/approve
{ "thread_id": "inv-checkout-500s", "approved": true, "reason": "verified in staging" }
```

### WebSocket

```javascript
const ws = new WebSocket('ws://localhost:8000/ws/chat/inv-checkout-500s');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

Every `/api/v1/*` route restates its own permission and quota dependencies rather than
inheriting them — see the comment at the top of `app/api/v1/chat.py` for why that matters.

---

## Integrations

**27 MCP tool servers** under `app/mcp/servers/`:

`alerting` · `architect` · `argocd` · `cicd` · `costopt` · `datadog` · `deploys` · `docker` ·
`docs` · `github` · `jira` · `kubernetes` · `linear` · `logs_metrics` · `marketplace` ·
`memory` · `multicloud` · `newrelic` · `opsgenie` · `pagerduty` · `repo` · `runbooks` ·
`security` · `sentry` · `slack` · `terraform` · `traces`

**8 direct connectors** under `app/connectors/`: GitLab, Grafana, Jenkins, Kubernetes,
New Relic, Prometheus, ServiceNow, Splunk.

Integrations fail closed: a server with no credentials configured returns an explicit
"not configured" error rather than inventing data. To evaluate the whole product before
wiring up a single integration, set `COPILOT_DEMO_MODE=true` — servers then serve
fixture-backed demo data with every payload explicitly labelled as simulated, and the
console shows them as `OFFLINE DEMO` rather than pretending they are live (which is why
the console screenshot above shows 11 servers active out of the 27 that ship in the repo).

---

## Security

### Implemented in code

- **Authentication** — OAuth2/JWT bearer tokens, API keys with HMAC signing, Supabase JWKS verification
- **Authorization** — RBAC with four ranked roles, `viewer` < `responder` < `admin` < `owner` (`app/tenancy/models.py`); per-route permission dependencies
- **Multi-tenancy** — tenant resolution at the edge, per-tenant quotas and rate limits
- **Audit** — append-only log with SHA-256 hash chaining, so a rewritten entry invalidates every entry after it
- **Prompt-injection defence** — a red-team eval suite (`evals/run_redteam.py`) runs as a blocking CI gate
- **Supply chain** — every pull request runs gitleaks, Trivy, `pip-audit` and a hardcoded-secret grep. Trivy (CRITICAL/HIGH) and the grep block the merge; gitleaks and `pip-audit` are currently advisory
- **SSRF** — a URL guard (`app/url_guard.py`) deny-lists loopback, link-local and private-network destinations for user-supplied webhook URLs, so a subscriber cannot aim the server at its own cloud metadata endpoint
- **Remediation** — reversible actions only (`rollback_deployment`, `restart_deployment`); a caller cannot declare its own confidence tier

### Compliance status

OmniPotent has not yet undergone third-party certification: there is currently no SOC 2
report, HIPAA BAA, PCI-DSS AOC, ISO 27001 certificate or GDPR DPA. This README claims only
the controls implemented in code, listed above. Evaluate the platform as pre-audit software
for regulated workloads.

### Reporting a vulnerability

See [SECURITY.md](SECURITY.md).

---

## Development

### Layout

```
OmniPotent/
├── app/                      # 47 packages
│   ├── api/                  # FastAPI app; main.py is the router hub
│   │   ├── v1/               # /v1 surface: chat, admin, policy, digital_twin, webhooks, ...
│   │   └── websocket.py      # /ws/chat/{thread_id}
│   ├── agents/               # investigator, orchestrator, parallel_coordinator, remediation, reviewer
│   ├── mcp/servers/          # 27 MCP tool servers
│   ├── connectors/           # 8 direct platform connectors
│   ├── integrations/         # 15 integration modules
│   ├── decision_engine/      # weighted-sum ranking of remediation options
│   ├── digital_twin/         # simulation used by the sandbox gate
│   ├── policy_engine/        # policy evaluation and blast-radius limits
│   ├── guardrails.py         # prompt-injection and output constraints
│   ├── audit/                # append-only, hash-chained log
│   └── url_guard.py          # SSRF deny-list for user-supplied URLs
├── frontend/                 # React + TypeScript SPA, 37 pages
├── evals/                    # red-team and golden-trajectory CI gates
├── tests/                    # 215 test files + integration/ e2e/ chaos/ load/ contract/ mutation/
├── migrations/               # Alembic
├── infrastructure/           # Terraform
├── monitoring/               # Prometheus rules, Grafana dashboards
├── scripts/                  # tooling, incl. gen_architecture.py
└── docs/
```

### Checks

These are exactly what CI runs, in order. Running them locally is the fastest way to know
a pull request will go green:

```bash
uv run ruff check app tests evals
uv run mypy app
uv run pytest --cov=app --cov-fail-under=48 -q
uv run python -m evals.run_redteam
```

Integration tests need a real PostgreSQL and Redis — without them the fixtures skip and the
tests silently never run:

```bash
COPILOT_DATABASE_URL=postgresql+asyncpg://omnipotent:omnipotent@localhost:5432/omnipotent_test \
REDIS_URL=redis://localhost:6379/0 \
  uv run pytest tests/integration -q
```

Frontend:

```bash
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build
```

### CI

Four jobs on every pull request: **Security** (gitleaks, Trivy, `pip-audit`, secret grep) →
**Backend** (ruff, mypy, pytest with a 48% coverage floor, integration tests against real
PostgreSQL and Redis, red-team gate, golden-trajectory replay) and **Frontend** (lint, test,
coverage) in parallel → **Coverage gate**.

### Regenerating the assets in this README

```bash
python scripts/gen_architecture.py     # docs/assets/architecture-3d-{light,dark}.svg
```

---

## Contributing

1. Fork and branch (`git checkout -b feat/your-change`)
2. Make the change, with tests
3. Run the four checks under [Checks](#checks) — CI runs the same ones
4. Commit using conventional commits (`feat:`, `fix:`, `docs:`)
5. Open a pull request

Two conventions worth knowing before you start:

- **Comments explain why, not what.** Several files carry comments recording a bug that a
  particular line prevents. Do not delete those without understanding what they are holding up.
- **Do not add a config variable without a reader.** `.env.example` has a section listing
  variables that were removed precisely because nothing read them.

### Contributors

<a href="https://github.com/ankit25bcs10610">
  <img src="https://github.com/ankit25bcs10610.png" width="60" height="60" alt="ankit25bcs10610" style="border-radius: 50%;" />
</a>

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

<sub>Built with Python, FastAPI, LangGraph, React and TypeScript ·
<a href="docs/">Documentation</a> ·
<a href="docs/api-reference.md">API reference</a> ·
<a href="docs/ARCHITECTURE.md">Architecture notes</a></sub>

</div>
