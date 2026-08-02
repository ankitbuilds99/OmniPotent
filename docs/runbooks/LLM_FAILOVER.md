# Runbook: LLM Provider Failover

> **Severity**: SEV2
> **Owner**: Platform Team
> **Last Updated**: 2026-07-28
> **Review Cadence**: Quarterly

## Overview

This runbook covers handling LLM provider outages, including automatic failover behavior and manual intervention procedures.

## Detection

### Alerts
- **Alert Name**: `copilot_llm_error_rate_high`
- **Threshold**: >5% LLM errors over 5 minutes
- **PagerDuty Service**: DevOps Copilot

### Secondary Alerts
- `copilot_llm_latency_p95_high`: P95 > 10s
- `copilot_circuit_breaker_open`: Circuit breaker tripped
- `copilot_fallback_activated`: Failover to secondary provider

### Dashboard Links
- [LLM Provider Health](https://grafana.internal/d/copilot-llm)
- [Circuit Breaker Status](https://grafana.internal/d/copilot-resilience)

## Triage

### Quick Assessment

```bash
# Check current provider status
curl -s https://copilot.internal/config | jq '.provider, .fallback_provider'

# Check circuit breaker status
curl -s https://copilot.internal/metrics/slo | jq '.circuit_breaker_open'

# Check LLM error rate
kubectl logs -l app=copilot --since=5m | grep -c "LLM.*error\|LLM.*fail"

# Check provider-specific errors
kubectl logs -l app=copilot --since=5m | grep -E "anthropic|openai|429|rate.limit"
```

### Provider Status Pages
- [Anthropic Status](https://status.anthropic.com)
- [OpenAI Status](https://status.openai.com)
- [Google AI Status](https://status.cloud.google.com)

## Automatic Failover

The system implements automatic failover:

1. **Circuit Breaker**: Opens after 5 failures in 60 seconds
2. **Failover Trigger**: Redirects to `COPILOT_FALLBACK_PROVIDER`
3. **Recovery Check**: Half-open state tests primary every 30 seconds
4. **Full Recovery**: Closes after 3 successful requests

### Failover Chain
```
Primary (Anthropic) → Fallback (OpenAI) → Emergency (Groq)
```

## Manual Intervention

### Force Switch to Fallback

```bash
# Immediate switch to OpenAI
kubectl set env deployment/omnipotent \
  COPILOT_PROVIDER=openai \
  COPILOT_MODEL=gpt-4o \
  -n omnipotent

# Or switch to Groq (faster, lower quality)
kubectl set env deployment/omnipotent \
  COPILOT_PROVIDER=groq \
  COPILOT_MODEL=llama-3.3-70b-versatile \
  -n omnipotent
```

### Reset Circuit Breaker

```bash
# Restart pods to reset circuit breaker state
kubectl rollout restart deployment/omnipotent -n omnipotent
```

### Verify Failover

```bash
# Test investigation
curl -X POST https://copilot.internal/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"test","thread_id":"failover-test"}'

# Check which provider handled it
kubectl logs -l app=copilot --tail=10 | grep "Using provider"
```

## Root Cause Analysis

### Common Causes

| Cause | Evidence | Solution |
|-------|----------|----------|
| Provider outage | Status page shows issues | Wait or switch provider |
| Rate limiting (429) | 429 in logs | Reduce load or upgrade tier |
| API key expired | 401/403 errors | Rotate API key |
| Network issues | Connection timeouts | Check DNS, firewall |
| Model deprecated | Model not found errors | Update model configuration |

### Provider-Specific Issues

**Anthropic**:
- Rate limits: 60 RPM (standard), 4000 RPM (enterprise)
- Context limit: 200K tokens (Claude 3.5)
- Known issues: Occasional 529 overloaded errors

**OpenAI**:
- Rate limits: Vary by tier
- Context limit: 128K tokens (GPT-4o)
- Known issues: Occasional capacity issues

**Groq**:
- Rate limits: 30 RPM (free), 100 RPM (paid)
- Context limit: 128K tokens
- Known issues: Lower quality for complex reasoning

## Resolution

### Verification Checklist

- [ ] Primary provider responding
- [ ] Circuit breaker closed
- [ ] No 429 errors in last 5 minutes
- [ ] Investigation latency within SLO
- [ ] Error rate < 1%

### Restore Primary Provider

```bash
# Restore to Anthropic
kubectl set env deployment/omnipotent \
  COPILOT_PROVIDER=anthropic \
  COPILOT_MODEL=claude-sonnet-4-20250514 \
  -n omnipotent

# Verify restoration
kubectl rollout status deployment/omnipotent -n omnipotent
```

## Prevention

### Rate Limit Management

```bash
# Check current usage
curl -s https://api.anthropic.com/v1/usage \
  -H "x-api-key: $ANTHROPIC_API_KEY"

# Implement request queuing
kubectl set env deployment/omnipotent \
  COPILOT_LLM_QUEUE_SIZE=100 \
  COPILOT_LLM_CONCURRENCY=5 \
  -n omnipotent
```

### Multi-Provider Setup

Ensure fallback providers are configured:
```yaml
# Required environment variables
COPILOT_PROVIDER: anthropic
COPILOT_FALLBACK_PROVIDER: openai
ANTHROPIC_API_KEY: <key>
OPENAI_API_KEY: <key>
```

## Appendix

### API Key Rotation

```bash
# Update Anthropic key
kubectl create secret generic copilot-secrets \
  --from-literal=ANTHROPIC_API_KEY=<new-key> \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart to pick up new key
kubectl rollout restart deployment/omnipotent -n omnipotent
```

### Related ADRs
- [ADR-0004: Multi-Provider LLM Support](../adr/0004-multi-provider-llm-support.md)
