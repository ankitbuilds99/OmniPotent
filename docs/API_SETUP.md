# API Keys Setup Guide

DevOps Copilot runs in **offline demo mode** by default using realistic fixtures. To connect to real APIs, configure the following environment variables.

## Quick Start

Create a `.env` file in the project root:

```bash
cp .env.example .env
# Edit .env with your API keys
```

Or export directly:

```bash
export DD_API_KEY="your-datadog-api-key"
export DD_APP_KEY="your-datadog-app-key"
# ... other keys
```

## Supported Integrations

### Datadog (Logs & Metrics)

```bash
DD_API_KEY=your-api-key          # Required
DD_APP_KEY=your-application-key  # Required
DD_SITE=datadoghq.com            # Optional (default: datadoghq.com)
```

**How to get keys:**
1. Go to [Datadog Organization Settings](https://app.datadoghq.com/organization-settings/api-keys)
2. Create a new API key
3. Go to Application Keys and create an app key
4. Required scopes: `logs_read_data`, `metrics_read`

### PagerDuty (Incidents & Alerts)

```bash
PAGERDUTY_API_TOKEN=your-api-token    # Required
PAGERDUTY_FROM_EMAIL=user@company.com # Required for write operations
```

**How to get token:**
1. Go to [PagerDuty API Access](https://support.pagerduty.com/docs/api-access-keys)
2. Create a User API Token or General Access Token
3. Required scopes: `read`, `write` (for acknowledge/resolve)

### GitHub (Commits & PRs)

```bash
GITHUB_TOKEN=ghp_xxxxxxxxxxxx  # Required
GITHUB_REPO=owner/repo         # Required (e.g., "acme/checkout-svc")
```

**How to get token:**
1. Go to [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens)
2. Create a Fine-grained token or Classic token
3. Required scopes: `repo` (read), `workflow` (for CI logs), `pull_request:write` (for PR creation)

### Kubernetes (Pods & Deployments)

```bash
KUBE_CONFIG_PATH=/path/to/.kube/config  # Required
KUBE_NAMESPACE=default                   # Optional (default: "default")
```

**How to configure:**
1. Ensure you have `kubectl` access to your cluster
2. Point to your kubeconfig file
3. The configured context will be used

### Sentry (Error Tracking)

```bash
SENTRY_API_TOKEN=sntryu_xxxxx  # Required
SENTRY_ORG=your-org            # Required
SENTRY_PROJECT=your-project    # Required
SENTRY_BASE_URL=https://sentry.io  # Optional (for self-hosted)
```

**How to get token:**
1. Go to [Sentry Settings > Auth Tokens](https://sentry.io/settings/auth-tokens/)
2. Create a User Auth Token
3. Required scopes: `project:read`, `event:read`

### ArgoCD (GitOps)

```bash
ARGOCD_SERVER_URL=https://argocd.example.com  # Required
ARGOCD_AUTH_TOKEN=your-token                   # Required
```

**How to get token:**
1. Create a local user in ArgoCD or use SSO
2. Generate a token: `argocd account generate-token --account your-user`
3. Or via API: POST `/api/v1/session` with username/password

### Terraform Cloud

```bash
TERRAFORM_CLOUD_TOKEN=your-team-token  # Required
TERRAFORM_ORGANIZATION=your-org        # Required
```

**How to get token:**
1. Go to [Terraform Cloud User Settings](https://app.terraform.io/app/settings/tokens)
2. Create an API token (User, Team, or Organization)
3. Team tokens are recommended for production

## Demo Scenarios

Even in offline mode, you can switch between different incident scenarios:

```bash
# Default: checkout service discount bug
export COPILOT_DEMO_SCENARIO=checkout-discount

# Memory leak in payment service
export COPILOT_DEMO_SCENARIO=memory-leak

# Cascading failure from auth to downstream services
export COPILOT_DEMO_SCENARIO=cascading-failure

# Config drift from Terraform apply
export COPILOT_DEMO_SCENARIO=config-drift

# False alarm (agent should NOT escalate)
export COPILOT_DEMO_SCENARIO=false-alarm
```

Each scenario provides consistent fixtures across ALL MCP servers, so the agent sees a coherent incident narrative.

## Verification

Check which integrations are connected:

```bash
# Start the backend
cd app && uvicorn api.main:app --reload

# Check health endpoint
curl http://localhost:8000/healthz

# Or use the frontend status page
open http://localhost:5173  # Click "Launch Console"
```

## Security Best Practices

1. **Never commit `.env` files** — `.env` is already in `.gitignore`
2. **Use read-only tokens** where possible (all read tools work with read-only access)
3. **Rotate tokens regularly** — especially after team member changes
4. **Use environment-specific tokens** — separate dev/staging/prod
5. **For production:** Use a secrets manager (Vault, AWS Secrets Manager, etc.)

## Troubleshooting

### "GITHUB_REPO is not set"
Set `GITHUB_REPO=owner/repo` alongside your `GITHUB_TOKEN`.

### "DD_API_KEY is not set" / offline mode
The system runs in offline demo mode when API keys are missing. This is intentional — set the keys to switch to live mode.

### "unauthorized" / 401 errors
Check that your token has the required scopes and hasn't expired.

### Connection timeouts
Check network access to the API endpoints. Some corporate networks block direct API access.
