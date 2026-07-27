<div align="center">

# DevOps Copilot

### Cut incident resolution time from 4 hours to 15 minutes

DevOps teams spend **4+ hours per incident** on root cause analysis — correlating logs, metrics, traces, and deploys manually. DevOps Copilot automates this investigation, identifies the root cause, and drafts a fix PR — **pausing for human approval before any action**.

<br/>

[![CI](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)

**Currently in private beta** — [Join the waitlist](#waitlist)

</div>

---

## The Problem

Every on-call engineer knows this pain:

```
3:47 AM — PagerDuty alert: "Checkout API latency > 5s"

Next 4 hours:
├── Check Datadog dashboards for error spikes
├── Search logs for exceptions (wrong time range, try again)
├── Find the error, but what caused it?
├── Check recent deploys — 3 in the last hour
├── git log to find which commit touched checkout
├── Correlate deploy time with error onset
├── Finally: null pointer in billing service from deploy #47
└── Write the fix, get it reviewed, deploy

Total time: 4 hours. Cost: $2,800 in engineer time + customer impact.
```

**DevOps Copilot does this in 15 minutes.**

---

## How It Works

Ask a question in plain English:

```
"Why is the checkout API throwing 500s?"
```

The AI agent autonomously:

1. **Gathers evidence** — Pulls logs, metrics, traces, K8s pod state, recent deploys
2. **Correlates signals** — Links error onset to specific commits via git history
3. **Identifies root cause** — Ranks hypotheses by confidence with supporting evidence
4. **Drafts the fix** — Creates a PR with the solution

**Key differentiator:** Every action that writes data (deploy, PR, incident update) requires human approval. The AI investigates; you decide.

---

## Quick Demo

```bash
# 1. Clone and setup
git clone https://github.com/ankit25bcs10610/DevOps-Copilot.git
cd DevOps-Copilot
cp .env.example .env  # Add your ANTHROPIC_API_KEY

# 2. Run with Docker
docker compose up --build

# 3. Open http://localhost:8000 and ask:
"Why did the payment service start failing at 2pm?"
```

Or use the CLI:

```bash
uv venv && uv pip install -e .
uv run copilot "Investigate the memory leak in the user service"
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DevOps Copilot                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Question ──▶ LangGraph Agent ──▶ MCP Tools ──▶ Analysis       │
│                     │                  │                        │
│                     ▼                  ▼                        │
│              ┌─────────────┐   ┌─────────────────┐              │
│              │  7-Node     │   │  9 MCP Servers  │              │
│              │  Reasoning  │   │  ─────────────  │              │
│              │  Graph      │   │  Datadog        │              │
│              │             │   │  Kubernetes     │              │
│              │  • Triage   │   │  GitHub         │              │
│              │  • Hypothesize│ │  PagerDuty      │              │
│              │  • Investigate│ │  Sentry         │              │
│              │  • Synthesize│  │  Traces         │              │
│              │  • Approve   │   │  Deploys        │              │
│              └─────────────┘   │  Repository     │              │
│                                │  Memory         │              │
│                                └─────────────────┘              │
│                                                                 │
│   ────────────────── Human Approval Gate ──────────────────     │
│                                                                 │
│                 Fix PR  │  Deploy  │  Incident Update           │
│                         ▼                                       │
│              [Approve] [Reject] [Modify]                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## What's Included

| Component | Details |
|-----------|---------|
| **AI Agent** | LangGraph 7-node reasoning graph with tool selection |
| **Observability** | Datadog logs/metrics, distributed traces, K8s state |
| **Code Analysis** | Git history, commit diffs, deployment correlation |
| **Incident Mgmt** | PagerDuty integration, Sentry errors, Jira tickets |
| **Human-in-the-loop** | Risk-tiered approval gates for all write operations |
| **LLM Support** | Claude, GPT-4, Gemini, Groq, DeepSeek |

---

## Why Human-in-the-Loop?

Every AI incident tool faces a trust problem: **"Will it break prod?"**

DevOps Copilot separates investigation from action:

- **Read operations** (logs, metrics, traces): Autonomous
- **Write operations** (deploy, PR, incident update): Requires approval

This means:
- SOC2/compliance teams approve it (audit trail for every action)
- On-call engineers trust it (they're always in control)
- Enterprises adopt it (no "AI deployed to prod without review" incidents)

---

## Roadmap

- [x] Core investigation engine with MCP tools
- [x] Human-in-the-loop approval workflow
- [x] Multi-tenant SaaS with API keys and usage metering
- [ ] **Now:** Private beta with 5 pilot companies
- [ ] Slack/Teams integration for approvals
- [ ] Custom runbook generation from past incidents
- [ ] Self-hosted enterprise deployment

---

<a name="waitlist"></a>
## Join the Waitlist

We're onboarding pilot customers who:
- Have 50+ engineers and dedicated on-call rotation
- Use Kubernetes + Datadog/New Relic/Prometheus
- Experience MTTR > 2 hours on average

**What you get:**
- 30-day free pilot
- Direct Slack channel with founders
- Custom integrations for your stack

**[Request Access →](https://github.com/ankit25bcs10610/DevOps-Copilot/issues/new?title=Pilot%20Request&body=Company:%0ATeam%20size:%0ACurrent%20MTTR:%0AObservability%20stack:)**

---

## Development

```bash
# Run tests
uv run pytest

# Type checking
uv run mypy app

# Linting
ruff check app

# Full CI locally
./scripts/ci.sh
```

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for development setup.

---

## License

MIT — see [LICENSE](LICENSE)

---

<div align="center">

**Built by engineers who've been woken up at 3 AM too many times.**

[Documentation](docs/USAGE.md) · [API Reference](docs/API.md) · [Request Demo](https://github.com/ankit25bcs10610/DevOps-Copilot/issues/new?title=Demo%20Request)

</div>
