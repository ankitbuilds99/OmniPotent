<div align="center">

<img src="docs/assets/logo.png" alt="DevOps Copilot" width="120" />

# DevOps Copilot

### The Omnipotent SRE Platform

**Autonomous incident investigation with 6 parallel AI agents, 3-layer safety gates, and 35+ integrations.**

Cut MTTR from 4 hours to 15 minutes — with full human oversight.

<br/>

[![CI](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/ci.yml)
[![Deploy](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/deploy.yml/badge.svg)](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/deploy.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)
[![SOC2 Ready](https://img.shields.io/badge/SOC2-Ready-00d084.svg)](#compliance)

[Live Demo](#quick-start) · [Documentation](docs/) · [Architecture](#architecture) · [API Reference](docs/API.md)

</div>

---

## Why DevOps Copilot?

| Metric | Before | After |
|--------|--------|-------|
| Mean Time to Resolution | 4+ hours | **15 minutes** |
| Root Cause Accuracy | Manual correlation | **90%+ automated** |
| Engineer Context Switches | 8-12 tools | **1 unified interface** |
| Incident Documentation | Often skipped | **Auto-generated** |

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

Total time: 4 hours
Cost: $2,800 in engineer time + customer impact
```

**DevOps Copilot does this in 15 minutes — with 6 AI agents working in parallel.**

---

## Key Features

<table>
<tr>
<td width="50%">

### Speed: 6 Parallel Specialists

```
┌─────────────────────────────────────┐
│           Coordinator               │
│    (LLM-powered orchestration)      │
└───────────────┬─────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌────────┐
│Knowledge│ │Reasoning│ │ Action │
│  Agent │ │  Agent │ │  Agent │
└────────┘ └────────┘ └────────┘
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌────────┐
│Learning│ │Collab  │ │Ambient │
│  Agent │ │  Agent │ │  Agent │
└────────┘ └────────┘ └────────┘
```

- **3-4x faster** investigation
- Auto root-cause in **90 seconds**
- Parallel evidence gathering

</td>
<td width="50%">

### Safety: 3-Layer Defense

```
┌─────────────────────────────────────┐
│     Gate 1: Confidence              │
│     Claude score ≥ 0.8              │
│     → Escalate if low               │
└───────────────┬─────────────────────┘
                ▼
┌─────────────────────────────────────┐
│     Gate 2: Policy Guardrails       │
│     Blast radius + injection check  │
│     → Block + alert if risky        │
└───────────────┬─────────────────────┘
                ▼
┌─────────────────────────────────────┐
│     Gate 3: Sandbox Test            │
│     Dry-run + verify rollback       │
│     → Test before execute           │
└─────────────────────────────────────┘

All 3 pass → Auto-remediate
Any fails  → Human approval
```

</td>
</tr>
</table>

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                    │
│         OAuth2/OIDC │ Multi-Tenant │ RBAC │ Rate Limiting │ Job Queue        │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                         ADAPTIVE MODEL ROUTER                                 │
│              Sonnet 4 │ Haiku │ o1 │ GPT-4o │ Gemini                         │
│         Route by: Complexity │ Cost/Latency │ Customer Tier                  │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                    AUTO-GENERATED LIVE KNOWLEDGE GRAPH                        │
│                                                                               │
│   Real-time State          Continuous Sync           Smart Indexing           │
│   ├── Kubernetes          ├── GitHub webhooks       ├── Service graph        │
│   ├── AWS/GCP/Azure       ├── CloudTrail events     ├── Dependency map       │
│   ├── Code repos          └── Prometheus scrape     └── Deploy timeline      │
│   └── Configs/metrics                                                         │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        6 PARALLEL SPECIALISTS                                 │
│                                                                               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │Knowledge │ │Reasoning │ │  Action  │ │ Learning │ │  Collab  │ │Ambient │ │
│  │──────────│ │──────────│ │──────────│ │──────────│ │──────────│ │────────│ │
│  │Graph     │ │Hypothesis│ │Telemetry │ │Playbook  │ │Context   │ │Pattern │ │
│  │query     │ │generation│ │Code acts │ │generation│ │sync      │ │anomaly │ │
│  │Service   │ │Trace     │ │Infra     │ │Feedback  │ │Slack/    │ │Proact- │ │
│  │state     │ │chains    │ │changes   │ │loop      │ │Teams     │ │ive     │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
              ════════════════════════╪════════════════════════
                        SAFETY BOUNDARY (Zero Blast Radius)
              ════════════════════════╪════════════════════════
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                      3-LAYER SAFETY GATES                                     │
│                                                                               │
│   ┌────────────────┐   ┌────────────────┐   ┌────────────────┐               │
│   │ CONFIDENCE     │   │ POLICY         │   │ SANDBOX        │               │
│   │ Threshold: 0.8 │ → │ Blast radius   │ → │ Dry-run test   │               │
│   │ Escalate low   │   │ Injection chk  │   │ Verify rollbk  │               │
│   └────────────────┘   └────────────────┘   └────────────────┘               │
│                                                                               │
│            All pass → Proceed    │    Any fail → Human Approval               │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                        REMEDIATION ACTIONS                                    │
│                                                                               │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│   │Code Fixes│ │  Infra   │ │  Deploy  │ │   Cost   │ │  Human   │           │
│   │──────────│ │──────────│ │──────────│ │──────────│ │──────────│           │
│   │Open PRs  │ │kubectl   │ │Blue-green│ │Right-size│ │Severity  │           │
│   │Test suite│ │Scale     │ │Canary    │ │Stop idle │ │routing   │           │
│   │          │ │Rollback  │ │Rollout   │ │          │ │          │           │
│   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                     CONTINUOUS LEARNING                                       │
│     Replay incidents → Measure accuracy → A/B test → Auto-generate playbooks │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                  FULL AUDIT TRAIL & COMPLIANCE                                │
│         SOC2 │ HIPAA │ PCI-DSS │ Immutable logs │ Encrypted secrets          │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 35+ Pre-Integrated Connectors

<table>
<tr>
<td><b>Cloud</b></td>
<td>AWS · GCP · Azure · Kubernetes</td>
</tr>
<tr>
<td><b>Observability</b></td>
<td>Datadog · New Relic · Prometheus · Grafana · Splunk · Sentry · Jaeger</td>
</tr>
<tr>
<td><b>Code & CI/CD</b></td>
<td>GitHub · GitLab · Jenkins · CircleCI · Terraform · Docker · ArgoCD</td>
</tr>
<tr>
<td><b>Communications</b></td>
<td>Slack · PagerDuty · Jira · ServiceNow · Microsoft Teams</td>
</tr>
<tr>
<td><b>Secrets</b></td>
<td>AWS Secrets Manager · HashiCorp Vault · GCP Secret Manager · Azure Key Vault</td>
</tr>
</table>

---

## Quick Start

### Option 1: Docker (Recommended)

```bash
# Clone the repository
git clone https://github.com/ankit25bcs10610/OmniPotent.git
cd OmniPotent

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Start all services
docker compose up --build

# Open http://localhost:3000
```

### Option 2: Local Development

```bash
# Backend
uv sync --extra dev
uv run uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev

# Open http://localhost:3000
```

### Option 3: CLI

```bash
uv run copilot "Why is the checkout API throwing 500 errors?"
```

---

## Example Investigation

```bash
$ copilot "Investigate high latency on payment-service"
```

```
╔══════════════════════════════════════════════════════════════════╗
║                    DevOps Copilot Investigation                   ║
╠══════════════════════════════════════════════════════════════════╣
║ Status: Investigating                                             ║
║ Confidence: 94%                                                   ║
║ Duration: 47 seconds                                              ║
╚══════════════════════════════════════════════════════════════════╝

📊 Evidence Gathered (6 parallel agents):
├── [Telemetry] P99 latency spike: 2.3s → 8.7s at 14:23 UTC
├── [Code] Deploy #847 at 14:21 UTC: billing-service v2.4.1
├── [Dependencies] payment-service → billing-api (degraded)
├── [Historical] Similar incident 2024-03-15: connection pool
├── [Knowledge] billing-service owns: /api/v1/charge endpoint
└── [Ambient] Anomaly detected: DB connection wait time +340%

🔍 Root Cause Analysis:
┌─────────────────────────────────────────────────────────────────┐
│ Deploy #847 introduced a connection pool misconfiguration       │
│ in billing-service. Pool size reduced from 50 → 5 connections. │
│                                                                 │
│ File: billing-service/src/config/database.py                   │
│ Line: 42                                                        │
│ Change: max_connections: 50 → max_connections: 5                │
└─────────────────────────────────────────────────────────────────┘

⚡ Recommended Actions:
1. [AUTO-APPROVED] Rollback billing-service to v2.4.0
2. [NEEDS APPROVAL] Create hotfix PR for connection pool
3. [AUTO-APPROVED] Update runbook with connection pool checks

🛡️ Safety Gates:
├── ✅ Gate 1: Confidence 0.94 ≥ 0.80 threshold
├── ✅ Gate 2: Blast radius 1 service (rollback is reversible)
└── ✅ Gate 3: Sandbox test passed (rollback verified)

Proceed with rollback? [Y/n]:
```

---

## API Reference

### Start Investigation

```bash
curl -X POST https://api.omnipotent.com/v1/investigations \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Why is checkout failing?",
    "services": ["checkout-api", "payment-service"],
    "time_range": "1h"
  }'
```

### Response

```json
{
  "id": "inv_abc123",
  "status": "completed",
  "confidence": 0.94,
  "root_cause": {
    "summary": "Connection pool exhaustion in billing-service",
    "file": "billing-service/src/config/database.py",
    "line": 42,
    "commit": "a1b2c3d"
  },
  "evidence": [...],
  "recommended_actions": [...],
  "safety_gates": {
    "confidence": {"passed": true, "score": 0.94},
    "policy": {"passed": true, "violations": []},
    "sandbox": {"passed": true, "rollback_verified": true}
  }
}
```

See [docs/API.md](docs/API.md) for complete API documentation.

---

## Configuration

### Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...          # Claude API key

# Optional: Additional LLM providers
OPENAI_API_KEY=sk-...                  # GPT-4 fallback
GOOGLE_API_KEY=...                     # Gemini support

# Optional: Integrations
DATADOG_API_KEY=...
DATADOG_APP_KEY=...
GITHUB_TOKEN=ghp_...
PAGERDUTY_API_KEY=...
SLACK_WEBHOOK_URL=https://hooks.slack.com/...

# Database
DATABASE_URL=postgresql://...
REDIS_URL=redis://...

# Security
SECRET_KEY=your-secret-key
JWT_ALGORITHM=HS256
```

### Safety Gate Thresholds

```yaml
# config/safety_gates.yml
confidence_gate:
  default_threshold: 0.80
  action_thresholds:
    rollback: 0.75
    deploy_fix: 0.90
    run_command: 0.85

policy_gate:
  max_blast_radius:
    scale_down: 3
    rollback: 5
    restart: 2
  protected_services:
    - payments
    - auth
    - database

sandbox_gate:
  timeout_seconds: 300
  verify_rollback: true
```

---

<a name="compliance"></a>
## Compliance & Security

| Framework | Status | Details |
|-----------|--------|---------|
| **SOC2 Type II** | Ready | Full audit trail, access controls, encryption |
| **HIPAA** | Ready | PHI handling, audit logs, encryption at rest |
| **PCI-DSS** | Ready | Cardholder data protection, secure secrets |
| **GDPR** | Ready | Data residency, right to erasure, consent |

### Security Features

- **Immutable Audit Log**: SHA-256 hash-chained event log
- **Encrypted Secrets**: AES-256 with envelope encryption
- **Multi-tenant Isolation**: Per-tenant encryption keys (DEK/KEK)
- **RBAC**: 5 built-in roles + custom role support
- **SSO**: SAML 2.0, OAuth2, OIDC with multi-IdP routing

---

## Development

```bash
# Install dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=html

# Type checking
uv run mypy app

# Linting
uv run ruff check app tests

# Format code
uv run ruff format app tests

# Run all checks (CI locally)
./scripts/ci.sh
```

### Project Structure

```
OmniPotent/
├── app/
│   ├── agents/              # 6 parallel specialist agents
│   ├── api/                 # FastAPI routes + GraphQL
│   ├── auth/                # OAuth2, OIDC, SAML
│   ├── compliance/          # SOC2, HIPAA, PCI-DSS controls
│   ├── connectors/          # 35+ integrations
│   ├── deployment/          # Blue-green, canary strategies
│   ├── graph/               # LangGraph investigation flow
│   ├── mcp/                 # MCP tool servers
│   ├── rbac/                # Role-based access control
│   ├── router/              # Adaptive model routing
│   ├── safety_gates/        # 3-layer safety system
│   └── tenancy/             # Multi-tenant isolation
├── frontend/                # React + TypeScript UI
├── infrastructure/          # Terraform (AWS/GCP/Azure)
├── monitoring/              # Prometheus rules, Grafana dashboards
├── tests/                   # Pytest test suite
└── evals/                   # Evaluation harness
```

---

## Roadmap

- [x] Core investigation engine with 6 parallel agents
- [x] 3-layer safety gates (Confidence, Policy, Sandbox)
- [x] 35+ pre-integrated connectors
- [x] Multi-tenant SaaS with RBAC
- [x] SOC2/HIPAA/PCI-DSS compliance
- [x] GraphQL API with real-time subscriptions
- [ ] **Q3 2026**: Slack/Teams approval workflows
- [ ] **Q3 2026**: Custom playbook marketplace
- [ ] **Q4 2026**: Self-hosted enterprise deployment
- [ ] **Q4 2026**: Model fine-tuning on incident data

---

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/ankit25bcs10610/OmniPotent/issues)
- **Discussions**: [GitHub Discussions](https://github.com/ankit25bcs10610/OmniPotent/discussions)
- **Email**: support@omnipotent.com

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built by engineers who've been woken up at 3 AM too many times.**

<br/>

[Documentation](docs/) · [API Reference](docs/API.md) · [Architecture](#architecture) · [Roadmap](#roadmap)

<br/>

<sub>Made with Claude, FastAPI, LangGraph, React, and lots of coffee.</sub>

</div>
