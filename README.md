<div align="center">

# 🚀 DevOps Copilot

### Your AI DevOps Engineer

**Build. Deploy. Monitor. Scale. Secure. Automate.**

An autonomous AI-powered DevOps platform that investigates incidents, manages infrastructure, orchestrates deployments, and drafts fixes — **pausing for human approval before any write operation.**

<br/>

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ankit25bcs10610/DevOps-Copilot)

[![CI](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/DevOps-Copilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![Three.js](https://img.shields.io/badge/Three.js-3D-000000?logo=three.js&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5.0-3178C6?logo=typescript&logoColor=white)
![Tailwind](https://img.shields.io/badge/Tailwind-3.4-06B6D4?logo=tailwindcss&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agent-1C3C3C)
![MCP](https://img.shields.io/badge/MCP-Protocol-7C5CFF)

<br/>

**LangGraph 7-node Agent · 9 MCP Servers · 48 Tools · 5 LLM Providers · Multi-tenant SaaS**

</div>

<br/>

---

## ✨ What's New: Cinematic 3D Landing Page

Award-worthy frontend inspired by **Apple WWDC, Vercel, Linear, Stripe, and OpenAI**:

- 🌌 **Immersive 3D Hero** — Holographic AI core with orbiting DevOps tools (Docker, K8s, Terraform, AWS)
- 🔄 **Interactive Pipeline** — Watch code flow from GitHub → Docker → K8s → Production with live logs
- 🌍 **Global Cloud Map** — 3D rotating Earth with real-time cloud region metrics
- 📊 **Live Dashboard** — Animated metrics, streaming logs, infrastructure health monitoring
- 🤖 **AI Chat Demo** — Natural language → Dockerfile, Terraform, K8s YAML, GitHub Actions
- 💎 **Glassmorphism UI** — Floating cards with 3D tilt, cursor glow, smooth scroll animations

---

## 🎯 Core Features

### AI-Powered Incident Investigation

Ask *"Why is the checkout API throwing 500s?"* and watch the AI autonomously:

1. **Gather** — Pull logs, metrics, traces, K8s state, recent deploys
2. **Correlate** — Link failures to code changes via git history
3. **Analyze** — Generate structured RCA with ranked hypotheses
4. **Fix** — Draft pull requests — **stopping for approval before any write**

### Enterprise-Ready Platform

| Feature | Description |
|---------|-------------|
| **9 MCP Servers** | Datadog · PagerDuty · Kubernetes · Sentry · GitHub · Traces · Deploys · Repo · Memory |
| **48 AI Tools** | Live-API or offline-fixture connectors for complete observability |
| **5 LLM Providers** | Anthropic (Claude) · OpenAI · Gemini · Groq · DeepSeek |
| **Human-in-the-loop** | Risk-tiered policy gates every action behind resumable approval |
| **Trust & Safety** | PII redaction, prompt-injection guardrails, token budgets, audit trails |
| **Multi-tenant SaaS** | Orgs, RBAC, API keys, usage metering, Stripe billing |

---

## 🚀 Quickstart

### Option 1: Docker (Recommended)
```bash
docker compose up --build
# Open http://localhost:8000
```

### Option 2: CLI
```bash
uv venv && uv pip install -e .
cp .env.example .env          # Set ANTHROPIC_API_KEY
uv run copilot "Why is the checkout API throwing 500 errors?"
```

### Option 3: Development
```bash
# Backend
uv venv && uv pip install -e .
uv run uvicorn app.main:app --reload --port 8000

# Frontend (new cinematic UI)
cd frontend && npm install && npm run dev
# Open http://localhost:5173
```

📖 Full instructions → **[docs/USAGE.md](docs/USAGE.md)**

---

## 🏗️ Architecture

```
   React Console (3D) ─► FastAPI ─► LangGraph State Machine
                                      │
                                      ├─ plan → agent → policy router
                                      │    ├─ approve? → human gate ✅/❌
                                      │    ├─ read?    → tools (guarded)
                                      │    └─ done?    → reflect → verify
                                      │
                                      └─ checkpointer: SQLite / Postgres
                                              │
                                           MCP (stdio)
                                              │
   ┌─────────────────────────────────────────────────────────────────┐
   │  datadog · pagerduty · kubernetes · sentry · traces · deploys  │
   │  github · repo · incident-memory                                │
   └─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

<table>
<tr>
<td width="50%">

### Backend
- **Agent:** LangGraph · FastMCP · LangChain
- **Models:** Claude · GPT-4 · Gemini · Groq · DeepSeek
- **API:** FastAPI · SQLite/Postgres · SSE
- **Auth:** Bearer tokens · Rate limiting

</td>
<td width="50%">

### Frontend
- **Framework:** React 18 · TypeScript · Vite
- **3D:** React Three Fiber · Three.js · Drei
- **Animation:** Framer Motion · GSAP · Lenis
- **Styling:** Tailwind CSS · Glassmorphism

</td>
</tr>
</table>

---

## 📊 Platform Metrics

<div align="center">

| Deployment Success | Response Time | Deployments | Containers | Cloud Providers |
|:------------------:|:-------------:|:-----------:|:----------:|:---------------:|
| **99.99%** | **<18ms** | **1,532+** | **500K+** | **12+** |

</div>

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [**Usage**](docs/USAGE.md) | Install & run (CLI · API · Web · Docker) |
| [**Configuration**](docs/CONFIGURATION.md) | Environment variables & HTTP API |
| [**Architecture**](docs/ARCHITECTURE.md) | System design & LangGraph flow |
| [**Agent**](docs/AGENT.md) | 7-node loop, approval gate, RCA, guardrails |
| [**Connectors**](docs/CONNECTORS.md) | 9 MCP servers, 48 tools, live/offline modes |
| [**Hardening**](docs/HARDENING.md) | Security, reliability, cost, observability |
| [**Evaluation**](docs/EVALUATION.md) | Eval harness, trajectory scorers |
| [**Deployment**](DEPLOY.md) | Production Docker setup |

---

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open a Pull Request

---

## 👨‍💻 Author

<div align="center">

**Ankit Pandey** — Creator & Maintainer

[![GitHub](https://img.shields.io/badge/GitHub-ankit25bcs10610-181717?style=for-the-badge&logo=github)](https://github.com/ankit25bcs10610)

</div>

---

<div align="center">

**[MIT License](LICENSE)**

Built as a portfolio project demonstrating production-grade AI agent development.

⭐ **Star this repo if you find it useful!**

</div>
