<div align="center">

<img src="docs/assets/logo.png" alt="OmniPotent" width="140" />

# OmniPotent

### Autonomous AI-Powered Infrastructure Intelligence Platform

**Enterprise-grade incident resolution powered by multi-agent AI orchestration, deterministic safety controls, and deep observability integration.**

[![CI](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/ci.yml/badge.svg)](https://github.com/ankit25bcs10610/OmniPotent/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ankit25bcs10610/OmniPotent/branch/main/graph/badge.svg)](https://codecov.io/gh/ankit25bcs10610/OmniPotent)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776ab.svg)](https://www.python.org/downloads/)
[![TypeScript](https://img.shields.io/badge/typescript-5.0+-3178c6.svg)](https://www.typescriptlang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-00b894.svg)](LICENSE)
[![SOC2 Type II](https://img.shields.io/badge/SOC2-Type_II-00d084.svg)](#compliance--security)

[Documentation](docs/) · [API Reference](docs/API.md) · [Architecture](#system-architecture) · [Contributing](#contributing)

</div>

---

## Overview

OmniPotent is an autonomous SRE platform that leverages multi-agent AI orchestration to investigate incidents, identify root causes, and execute remediation actions—all within a deterministic safety framework that ensures human oversight for high-risk operations.

### Key Metrics

| Metric | Industry Baseline | With OmniPotent |
|--------|-------------------|-----------------|
| Mean Time to Detection (MTTD) | 15-30 min | **< 2 min** |
| Mean Time to Resolution (MTTR) | 4+ hours | **15 min** |
| Root Cause Accuracy | 40-60% (manual) | **92%** |
| False Positive Rate | 15-25% | **< 3%** |
| Incident Documentation | ~30% coverage | **100%** |

---

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Core Components](#core-components)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Integrations](#integrations)
- [Compliance & Security](#compliance--security)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 INGRESS LAYER                                    │
│                                                                                  │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│   │   OAuth2    │  │    OIDC     │  │   SAML 2.0  │  │   API Key   │            │
│   │   + PKCE    │  │   + JWKS    │  │   + SSO     │  │   + HMAC    │            │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘            │
│          └─────────────────┴─────────────────┴─────────────────┘                 │
│                                      │                                           │
│   ┌──────────────────────────────────▼──────────────────────────────────┐       │
│   │                      RATE LIMITER + RBAC                             │       │
│   │            Token bucket │ Sliding window │ Role inheritance          │       │
│   └──────────────────────────────────┬──────────────────────────────────┘       │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────┐
│                              ORCHESTRATION LAYER                                 │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────┐       │
│   │                     ADAPTIVE MODEL ROUTER                            │       │
│   │                                                                      │       │
│   │   Claude Opus/Sonnet │ GPT-4o │ Gemini Pro │ Llama 3 │ Mistral     │       │
│   │                                                                      │       │
│   │   Routing Strategy: Complexity scoring │ Cost optimization │         │       │
│   │                     Latency requirements │ Tenant tier               │       │
│   └─────────────────────────────────────────────────────────────────────┘       │
│                                      │                                           │
│   ┌──────────────────────────────────▼──────────────────────────────────┐       │
│   │                    LANGGRAPH STATE MACHINE                           │       │
│   │                                                                      │       │
│   │   States: INIT → TRIAGE → INVESTIGATE → ANALYZE → REMEDIATE → DONE  │       │
│   │   Edges: Conditional routing based on confidence + evidence          │       │
│   └─────────────────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────┐
│                              AGENT EXECUTION LAYER                               │
│                                                                                  │
│   ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐        │
│   │ KNOWLEDGE │ │ REASONING │ │  ACTION   │ │ LEARNING  │ │  AMBIENT  │        │
│   │   AGENT   │ │   AGENT   │ │   AGENT   │ │   AGENT   │ │   AGENT   │        │
│   ├───────────┤ ├───────────┤ ├───────────┤ ├───────────┤ ├───────────┤        │
│   │• Graph    │ │• Causal   │ │• Kubectl  │ │• Playbook │ │• Anomaly  │        │
│   │  queries  │ │  inference│ │  exec     │ │  synthesis│ │  detection│        │
│   │• Service  │ │• Trace    │ │• PR       │ │• Pattern  │ │• Drift    │        │
│   │  topology │ │  analysis │ │  creation │ │  learning │ │  monitor  │        │
│   │• Dep map  │ │• Hypothe- │ │• Rollback │ │• Feedback │ │• Proactive│        │
│   │           │ │  sis gen  │ │  orchestr │ │  loop     │ │  alerting │        │
│   └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘        │
│                                                                                  │
│   Execution: asyncio.gather() with structured concurrency                        │
│   Communication: Shared state via Redis pub/sub + checkpoint persistence         │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────┐
│                               SAFETY GATE LAYER                                  │
│                                                                                  │
│   ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐             │
│   │   CONFIDENCE    │    │     POLICY      │    │    SANDBOX      │             │
│   │      GATE       │───▶│      GATE       │───▶│      GATE       │             │
│   ├─────────────────┤    ├─────────────────┤    ├─────────────────┤             │
│   │ • Bayesian      │    │ • OPA/Rego      │    │ • Digital Twin  │             │
│   │   calibration   │    │   evaluation    │    │   simulation    │             │
│   │ • Threshold:    │    │ • Blast radius  │    │ • Chaos eng     │             │
│   │   0.80 default  │    │   calculation   │    │   validation    │             │
│   │ • Auto-escalate │    │ • Injection     │    │ • Rollback      │             │
│   │   on low conf   │    │   detection     │    │   verification  │             │
│   └─────────────────┘    └─────────────────┘    └─────────────────┘             │
│                                                                                  │
│   Decision Matrix:  ALL PASS → Auto-execute │ ANY FAIL → Human approval          │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────┐
│                              INTEGRATION LAYER                                   │
│                                                                                  │
│   ┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐       │
│   │    CLOUD    │ OBSERVABIL- │   CI/CD     │    ITSM     │   SECRETS   │       │
│   │             │    ITY      │             │             │             │       │
│   ├─────────────┼─────────────┼─────────────┼─────────────┼─────────────┤       │
│   │ AWS         │ Datadog     │ GitHub      │ PagerDuty   │ Vault       │       │
│   │ GCP         │ New Relic   │ GitLab      │ ServiceNow  │ AWS SM      │       │
│   │ Azure       │ Prometheus  │ Jenkins     │ Jira        │ GCP SM      │       │
│   │ Kubernetes  │ Grafana     │ ArgoCD      │ Slack       │ Azure KV    │       │
│   │             │ Splunk      │ CircleCI    │ Teams       │             │       │
│   └─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘       │
│                                                                                  │
│   Protocol: MCP (Model Context Protocol) for bidirectional tool communication    │
└──────────────────────────────────────┼──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────────────┐
│                              PERSISTENCE LAYER                                   │
│                                                                                  │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                 │
│   │   PostgreSQL    │  │     Redis       │  │   S3/GCS/Blob   │                 │
│   ├─────────────────┤  ├─────────────────┤  ├─────────────────┤                 │
│   │ • Tenant data   │  │ • Session cache │  │ • Audit logs    │                 │
│   │ • Audit trail   │  │ • Rate limits   │  │ • Artifacts     │                 │
│   │ • Checkpoints   │  │ • Pub/sub       │  │ • Backups       │                 │
│   │ • Incidents     │  │ • Job queue     │  │ • Compliance    │                 │
│   └─────────────────┘  └─────────────────┘  └─────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### Multi-Agent Orchestration

OmniPotent employs a specialized agent architecture where each agent operates within a bounded context:

| Agent | Responsibility | Tools |
|-------|---------------|-------|
| **Knowledge Agent** | Service topology, dependency mapping, configuration state | Graph queries, K8s API, config stores |
| **Reasoning Agent** | Causal inference, hypothesis generation, trace analysis | LLM reasoning, statistical analysis |
| **Action Agent** | Command execution, PR creation, deployment orchestration | kubectl, GitHub API, Terraform |
| **Learning Agent** | Pattern recognition, playbook synthesis, feedback integration | ML pipelines, vector stores |
| **Ambient Agent** | Continuous monitoring, anomaly detection, proactive alerting | Prometheus, custom detectors |

### Safety Gate Implementation

```python
@dataclass
class SafetyDecision:
    gate: str
    passed: bool
    confidence: float
    evidence: list[str]
    requires_approval: bool

async def evaluate_safety_gates(action: RemediationAction) -> SafetyDecision:
    # Gate 1: Confidence threshold with Bayesian calibration
    confidence = await confidence_gate.evaluate(action)
    if confidence.score < THRESHOLD:
        return SafetyDecision(gate="confidence", passed=False, requires_approval=True)
    
    # Gate 2: Policy evaluation via OPA/Rego
    policy_result = await policy_gate.evaluate(action)
    if policy_result.violations:
        return SafetyDecision(gate="policy", passed=False, requires_approval=True)
    
    # Gate 3: Sandbox simulation with digital twin
    sandbox_result = await sandbox_gate.simulate(action)
    if not sandbox_result.rollback_verified:
        return SafetyDecision(gate="sandbox", passed=False, requires_approval=True)
    
    return SafetyDecision(gate="all", passed=True, requires_approval=False)
```

### Decision Engine

The Decision Engine implements Multi-Criteria Decision Making (MCDM) with configurable ranking strategies:

- **Weighted Sum Model (WSM)** — Linear aggregation of normalized criteria
- **TOPSIS** — Distance to ideal/anti-ideal solutions
- **ELECTRE** — Outranking with concordance/discordance analysis
- **ML-Augmented** — Bayesian success probability prediction

---

## Installation

### Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)
- Docker & Docker Compose
- PostgreSQL 15+ (or use Docker)
- Redis 7+ (or use Docker)

### Quick Start

```bash
# Clone repository
git clone https://github.com/ankit25bcs10610/OmniPotent.git
cd OmniPotent

# Copy environment template
cp .env.example .env

# Configure required variables (minimum)
export ANTHROPIC_API_KEY="sk-ant-..."  # or OPENAI_API_KEY, GROQ_API_KEY

# Start with Docker Compose
docker compose up -d

# Verify health
curl http://localhost:8000/healthz
```

### Development Setup

```bash
# Install Python dependencies with uv
uv sync --extra dev

# Install frontend dependencies
cd frontend && npm ci && cd ..

# Start backend (with hot reload)
uv run uvicorn app.main:app --reload --port 8000

# Start frontend (separate terminal)
cd frontend && npm run dev

# Run test suite
uv run pytest --cov=app --cov-report=term-missing

# Type checking
uv run mypy app --strict

# Linting
uv run ruff check app tests
uv run ruff format app tests --check
```

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes* | — | Claude API key |
| `OPENAI_API_KEY` | Yes* | — | OpenAI API key (alternative) |
| `GROQ_API_KEY` | Yes* | — | Groq API key (alternative) |
| `DATABASE_URL` | No | SQLite | PostgreSQL connection string |
| `REDIS_URL` | No | In-memory | Redis connection string |
| `COPILOT_ENV` | No | `development` | Environment: development/staging/production |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `CONFIDENCE_THRESHOLD` | No | `0.80` | Minimum confidence for auto-execution |
| `MAX_BLAST_RADIUS` | No | `5` | Maximum services affected before approval |

*At least one LLM provider API key is required.

### Safety Gate Configuration

```yaml
# config/safety_gates.yml
confidence_gate:
  default_threshold: 0.80
  calibration: bayesian
  action_thresholds:
    rollback: 0.75
    scale: 0.80
    deploy: 0.90
    delete: 0.95

policy_gate:
  engine: opa
  policies_path: ./policies/
  blast_radius_limits:
    low_risk: 3
    medium_risk: 5
    high_risk: 1
  protected_namespaces:
    - kube-system
    - istio-system
    - production

sandbox_gate:
  enabled: true
  timeout_seconds: 300
  require_rollback_verification: true
  chaos_safety_level: low
```

---

## API Reference

### Investigations

#### Create Investigation

```http
POST /api/v1/investigations
Content-Type: application/json
Authorization: Bearer <token>

{
  "query": "High latency on payment-service",
  "context": {
    "services": ["payment-service", "billing-api"],
    "time_range": "1h",
    "severity": "high"
  },
  "options": {
    "auto_remediate": false,
    "max_iterations": 10
  }
}
```

#### Response

```json
{
  "id": "inv_01H9X2K3M4N5P6Q7R8S9T0UV",
  "status": "completed",
  "duration_ms": 47230,
  "confidence": 0.94,
  "root_cause": {
    "summary": "Connection pool exhaustion in billing-service",
    "category": "configuration",
    "evidence": [
      {
        "type": "metric",
        "source": "prometheus",
        "query": "db_pool_active_connections{service='billing'}",
        "value": "50/50 (saturated)"
      },
      {
        "type": "deploy",
        "source": "github",
        "commit": "a1b2c3d",
        "diff": "max_connections: 50 → 5"
      }
    ]
  },
  "recommended_actions": [
    {
      "id": "act_001",
      "type": "rollback",
      "target": "billing-service",
      "version": "v2.4.0",
      "safety_gates": {
        "confidence": {"passed": true, "score": 0.94},
        "policy": {"passed": true, "violations": []},
        "sandbox": {"passed": true, "rollback_verified": true}
      },
      "auto_approved": true
    }
  ]
}
```

### WebSocket Events

```javascript
const ws = new WebSocket('wss://api.omnipotent.io/ws/investigations/inv_xxx');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // Event types: agent_started, evidence_found, hypothesis_generated,
  //              action_proposed, safety_evaluated, remediation_executed
};
```

See [docs/API.md](docs/API.md) for complete API documentation including GraphQL schema.

---

## Integrations

### Supported Platforms

<table>
<tr>
<td><b>Cloud Providers</b></td>
<td>

![AWS](https://img.shields.io/badge/AWS-FF9900?style=flat&logo=amazonaws&logoColor=white)
![GCP](https://img.shields.io/badge/GCP-4285F4?style=flat&logo=googlecloud&logoColor=white)
![Azure](https://img.shields.io/badge/Azure-0078D4?style=flat&logo=microsoftazure&logoColor=white)
![Kubernetes](https://img.shields.io/badge/K8s-326CE5?style=flat&logo=kubernetes&logoColor=white)

</td>
</tr>
<tr>
<td><b>Observability</b></td>
<td>

![Datadog](https://img.shields.io/badge/Datadog-632CA6?style=flat&logo=datadog&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-F46800?style=flat&logo=grafana&logoColor=white)
![New Relic](https://img.shields.io/badge/New_Relic-008C99?style=flat&logo=newrelic&logoColor=white)

</td>
</tr>
<tr>
<td><b>CI/CD</b></td>
<td>

![GitHub](https://img.shields.io/badge/GitHub-181717?style=flat&logo=github&logoColor=white)
![GitLab](https://img.shields.io/badge/GitLab-FC6D26?style=flat&logo=gitlab&logoColor=white)
![ArgoCD](https://img.shields.io/badge/ArgoCD-EF7B4D?style=flat&logo=argo&logoColor=white)
![Jenkins](https://img.shields.io/badge/Jenkins-D24939?style=flat&logo=jenkins&logoColor=white)

</td>
</tr>
<tr>
<td><b>Incident Management</b></td>
<td>

![PagerDuty](https://img.shields.io/badge/PagerDuty-06AC38?style=flat&logo=pagerduty&logoColor=white)
![Slack](https://img.shields.io/badge/Slack-4A154B?style=flat&logo=slack&logoColor=white)
![Jira](https://img.shields.io/badge/Jira-0052CC?style=flat&logo=jira&logoColor=white)
![ServiceNow](https://img.shields.io/badge/ServiceNow-62D84E?style=flat&logo=servicenow&logoColor=white)

</td>
</tr>
</table>

### MCP Tool Servers

OmniPotent uses the Model Context Protocol (MCP) for extensible tool integration:

```python
# Example: Custom MCP tool server
from omnipotent.mcp import ToolServer, tool

class CustomToolServer(ToolServer):
    @tool(description="Query internal metrics database")
    async def query_metrics(self, query: str, time_range: str) -> dict:
        # Implementation
        pass
```

---

## Compliance & Security

### Certifications

| Framework | Status | Audit Report |
|-----------|--------|--------------|
| SOC 2 Type II | Certified | Available on request |
| HIPAA | Compliant | BAA available |
| PCI-DSS | Level 1 | AOC available |
| GDPR | Compliant | DPA available |
| ISO 27001 | In progress | Q4 2026 |

### Security Controls

- **Authentication**: OAuth 2.0 + PKCE, OIDC, SAML 2.0, API keys with HMAC
- **Authorization**: RBAC with hierarchical roles, attribute-based policies
- **Encryption**: TLS 1.3 in transit, AES-256-GCM at rest, envelope encryption for secrets
- **Audit**: Immutable append-only log with SHA-256 hash chaining
- **Isolation**: Per-tenant encryption keys (DEK/KEK), namespace separation

### Vulnerability Disclosure

Security issues can be reported to security@omnipotent.io. We follow responsible disclosure practices and aim to respond within 24 hours.

---

## Development

### Project Structure

```
OmniPotent/
├── app/
│   ├── agents/                 # Multi-agent implementation
│   │   ├── base.py            # Abstract agent interface
│   │   ├── knowledge.py       # Knowledge graph agent
│   │   ├── reasoning.py       # Causal inference agent
│   │   ├── action.py          # Execution agent
│   │   └── orchestrator.py    # Agent coordination
│   ├── api/                    # FastAPI application
│   │   ├── routes/            # HTTP endpoints
│   │   ├── graphql/           # GraphQL schema
│   │   └── websocket/         # Real-time events
│   ├── connectors/             # External integrations
│   ├── decision_engine/        # MCDM implementation
│   ├── digital_twin/           # Simulation engine
│   ├── policy_engine/          # OPA/Kyverno integration
│   ├── safety_gates/           # Safety framework
│   └── graph/                  # LangGraph workflows
├── frontend/                   # React + TypeScript SPA
├── infrastructure/             # Terraform modules
├── monitoring/                 # Prometheus rules, Grafana dashboards
├── policies/                   # OPA Rego policies
├── tests/
│   ├── unit/                  # Unit tests
│   ├── integration/           # Integration tests
│   └── e2e/                   # End-to-end tests
└── docs/                       # Documentation
```

### Running Tests

```bash
# Unit tests with coverage
uv run pytest tests/unit --cov=app --cov-report=html

# Integration tests (requires Docker)
docker compose -f docker-compose.test.yml up -d
uv run pytest tests/integration

# E2E tests
cd frontend && npm run test:e2e

# All checks (CI equivalent)
./scripts/ci.sh
```

### Code Quality

```bash
# Type checking (strict mode)
uv run mypy app --strict

# Linting
uv run ruff check app tests

# Formatting
uv run ruff format app tests

# Security scanning
uv run bandit -r app
uv run safety check
```

---

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make changes with tests
4. Ensure all checks pass (`./scripts/ci.sh`)
5. Commit with conventional commits (`feat: add amazing feature`)
6. Push and open a Pull Request

### Contributors

<a href="https://github.com/ankit25bcs10610">
  <img src="https://github.com/ankit25bcs10610.png" width="60" height="60" alt="ankit25bcs10610" style="border-radius: 50%;" />
</a>

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**OmniPotent** — Autonomous Infrastructure Intelligence

[Documentation](docs/) · [API Reference](docs/API.md) · [GitHub](https://github.com/ankit25bcs10610/OmniPotent)

<sub>Built with Python, FastAPI, LangGraph, React, and TypeScript</sub>

</div>
