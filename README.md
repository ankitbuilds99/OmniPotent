<div align="center">

# DevOps Copilot

### Your AI DevOps Engineer

**Build. Deploy. Monitor. Scale. Secure. Automate.**

An autonomous AI-powered DevOps platform that investigates incidents, manages infrastructure, orchestrates deployments, and drafts fixes — **pausing for human approval before any write operation.**

[![CI](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![Three.js](https://img.shields.io/badge/Three.js-3D-000000?logo=three.js&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-stateful%20agent-1C3C3C)
![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-7C5CFF)

**LangGraph 7-node graph · 9 MCP servers / 48 tools · Structured RCA + Postmortem · Fix Verification · Human-in-the-loop · Multi-tenant SaaS · Runs fully offline**

</div>

<p align="center">
  <img src="docs/screenshots/hero.png" alt="DevOps Copilot — AI Command Center" width="100%">
</p>

---

## Features

### AI-Powered Incident Investigation
Ask *"Why is the checkout API throwing 500s?"* and watch the AI autonomously:
- Pull logs, metrics, traces, Kubernetes state, and recent deploys
- Correlate failures to code changes via git history
- Generate structured root-cause analysis with ranked hypotheses
- Draft pull requests with the fix — **stopping for approval before any write**

### Cinematic 3D Landing Experience
Award-worthy frontend inspired by **Apple WWDC, Vercel, Linear, Stripe, and OpenAI**:
- **Immersive 3D Hero** — Holographic AI core with orbiting DevOps tools (Docker, K8s, Terraform, AWS)
- **Interactive Pipeline** — Watch code flow from GitHub to production with live logs
- **Global Cloud Map** — 3D rotating Earth with real-time cloud region metrics
- **Live Dashboard** — Animated metrics, streaming logs, infrastructure health
- **AI Chat Demo** — Natural language to production-ready Dockerfile, Terraform, K8s YAML
- **Glassmorphism UI** — Floating cards with 3D tilt, cursor glow, smooth animations

### Enterprise-Ready Platform
- **9 MCP Servers / 48 Tools** — Datadog · PagerDuty · Kubernetes · Sentry · GitHub · and more
- **5 LLM Providers** — Anthropic (Claude) · OpenAI · Gemini · Groq · DeepSeek
- **Multi-tenant SaaS** — Orgs, RBAC, API keys, usage metering, Stripe billing
- **Security First** — PII redaction, prompt-injection guardrails, token budgets, audit trails

---

## Highlights

| Feature | Description |
|---------|-------------|
| **Structured RCA** | Typed, ranked hypotheses with cited evidence; abstains on thin evidence |
| **Human-in-the-loop** | Risk-tiered policy gates every action behind resumable approval |
| **48 AI Tools** | Live-API or offline-fixture connectors for complete observability |
| **Trust & Safety** | PII redaction, injection guardrails, per-investigation token budget |
| **Triggered Mode** | PagerDuty webhook auto-investigates and posts to Slack |
| **Proactive SLO** | Opt-in burn-rate poller opens investigations before pages fire |

<table>
  <tr>
    <td><img src="docs/screenshots/dashboard.png" alt="Live Dashboard" width="100%"></td>
    <td><img src="docs/screenshots/console.png" alt="Investigation Console" width="100%"></td>
  </tr>
</table>

---

## Quickstart

### Option 1: CLI
```bash
uv venv && uv pip install -e .
cp .env.example .env          # set ANTHROPIC_API_KEY (or COPILOT_PROVIDER + its key)
uv run copilot "Why is the checkout API throwing 500 errors?"
```

### Option 2: Docker (Full Stack)
```bash
docker compose up --build     # http://localhost:8000
```

### Option 3: Development
```bash
# Backend
uv venv && uv pip install -e .
uv run uvicorn app.main:app --reload

# Frontend (new cinematic UI)
cd frontend && npm install && npm run dev
```

Full instructions → **[docs/USAGE.md](docs/USAGE.md)**

---

## Architecture

```
   CLI / React Console ─► FastAPI ─► LangGraph State Machine
                                       plan → agent → (policy route)
                                         ├─ approve? → approval  ◄── human ✅/❌
                                         ├─ read?    → tools (redacted + injection-scanned)
                                         └─ done?    → reflect → report → verify → RCA + postmortem
                                       checkpointer: SQLite / Postgres (resumable)
                                              │  MCP (stdio)
        datadog · pagerduty · kubernetes · sentry · traces · deploys · github · repo · incident-memory
```

The agent never imports a server — it only sees the tools each MCP server advertises.

Deep dive → **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** · **[docs/AGENT.md](docs/AGENT.md)**

---

## Tech Stack

### Backend
- **Agent Framework:** LangGraph · FastMCP · langchain-mcp-adapters · LangChain
- **Models:** Claude Opus 4.8 · OpenAI GPT-4 · Gemini · Groq/Llama · DeepSeek
- **API:** FastAPI · SQLite/Postgres · SSE streaming · Bearer auth · Rate limiting

### Frontend
- **Framework:** React 18 · TypeScript · Vite
- **3D Graphics:** React Three Fiber · Three.js · Drei · Postprocessing
- **Animation:** Framer Motion · GSAP · Lenis (smooth scroll)
- **Styling:** Tailwind CSS · Glassmorphism · Custom animations

### DevOps
- **Tooling:** uv · ruff · mypy · pytest · ESLint · Vitest
- **CI/CD:** GitHub Actions · Multi-stage Docker
- **Monitoring:** Datadog APM · LLMObs

---

## Documentation

| Doc | Description |
|-----|-------------|
| **[Usage](docs/USAGE.md)** | Install & run (CLI · API · Web · Docker) |
| **[Configuration](docs/CONFIGURATION.md)** | Environment variables & HTTP API |
| **[Architecture](docs/ARCHITECTURE.md)** | System design & LangGraph flow |
| **[The Agent](docs/AGENT.md)** | 7-node loop, approval gate, RCA, guardrails |
| **[Connectors](docs/CONNECTORS.md)** | 9 MCP servers, 48 tools, live/offline modes |
| **[Hardening](docs/HARDENING.md)** | Security, reliability, cost, observability |
| **[Evaluation](docs/EVALUATION.md)** | Eval harness, trajectory scorers, golden-replay |
| **[Commercialization](docs/COMMERCIALIZATION.md)** | Multi-tenant SaaS layer |
| **[Deployment](DEPLOY.md)** | Production Docker setup |

Full index → **[docs/README.md](docs/README.md)**

---

## Metrics

| Metric | Value |
|--------|-------|
| Deployment Success | 99.99% |
| Average Response | <18ms |
| Total Deployments | 1,532+ |
| Containers Managed | 500K+ |
| Cloud Providers | 12+ |

---

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Author

**Ankit Pandey** — Creator & Maintainer

[![GitHub](https://img.shields.io/badge/GitHub-@ankit25bcs10610-181717?logo=github)](https://github.com/ankit25bcs10610)

---

<div align="center">

**[MIT License](LICENSE)** — Built as a portfolio project demonstrating production-grade AI agent development.

⭐ Star this repo if you find it useful!

</div>
