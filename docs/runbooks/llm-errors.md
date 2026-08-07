# Runbook: LLM API Errors

**Alert:** `LLMErrorRate`
**Severity:** Critical
**SLO Impact:** Investigation success rate degraded

## Overview

This alert fires when the LLM API error rate exceeds 10% over a 5-minute window. This directly impacts the ability to run investigations.

## Impact

- New investigations fail to start
- In-progress investigations may timeout or produce incomplete results
- Users see "LLM provider rejected the API key" or similar errors

## Diagnosis Steps

### 1. Check Provider Status Pages

| Provider | Status Page |
|----------|-------------|
| Anthropic | https://status.anthropic.com |
| OpenAI | https://status.openai.com |
| Google AI | https://status.cloud.google.com |
| Groq | https://status.groq.com |

### 2. Identify Error Type

```bash
# Check recent LLM errors
kubectl logs -l app=omnipotent --since=10m | \
  jq 'select(.logger=="omnipotent.llm" and .level=="error")' | \
  jq -r '.msg' | sort | uniq -c | sort -rn
```

### 3. Check Circuit Breaker State

```bash
# If circuit breaker is open, requests are being rejected locally
kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | \
  grep copilot_circuit_breaker_state
```

### 4. Check Rate Limit Headers

```bash
# View rate limit info from recent responses
kubectl logs -l app=omnipotent --since=5m | \
  grep -i "rate.limit\|x-ratelimit\|429" | tail -20
```

## Common Causes & Fixes

### 1. API Key Invalid or Expired

**Symptoms:**
- 401 errors
- "Invalid API key" messages

**Fix:**
```bash
# Verify key is set
kubectl get secret omnipotent-secrets -o jsonpath='{.data.ANTHROPIC_API_KEY}' | base64 -d | head -c 10

# Update key
kubectl create secret generic omnipotent-secrets \
  --from-literal=ANTHROPIC_API_KEY="new-key-here" \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart to pick up new key
kubectl rollout restart deployment/omnipotent
```

### 2. Rate Limiting (429 Too Many Requests)

**Symptoms:**
- 429 errors
- Retry-After headers in logs
- Errors spike during high traffic

**Fix:**
```bash
# Option 1: Switch to backup provider
kubectl set env deployment/omnipotent COPILOT_PROVIDER=openai

# Option 2: Enable multi-provider failover
kubectl set env deployment/omnipotent COPILOT_FAILOVER_PROVIDERS="openai,gemini"

# Option 3: Reduce concurrent investigations
kubectl set env deployment/omnipotent COPILOT_MAX_SESSIONS=10
```

### 3. Provider Outage

**Symptoms:**
- 500/503 errors from provider
- Timeouts
- Status page shows incident

**Fix:**
```bash
# Switch to healthy provider
kubectl set env deployment/omnipotent COPILOT_PROVIDER=openai

# If all providers affected, enable maintenance mode
kubectl set env deployment/omnipotent COPILOT_MAINTENANCE_MODE=true
```

### 4. Model Deprecation

**Symptoms:**
- 404 errors
- "Model not found" messages

**Fix:**
```bash
# Check current model
kubectl exec -it POD -- curl -s localhost:8000/config | jq '.model'

# Update to current model
kubectl set env deployment/omnipotent COPILOT_MODEL="claude-sonnet-4-20250514"
```

### 5. Quota Exceeded (Billing)

**Symptoms:**
- 402 Payment Required
- "Insufficient credits" or quota messages

**Fix:**
1. Check billing dashboard for the provider
2. Add credits or upgrade plan
3. If urgent, switch to provider with available quota

## Circuit Breaker

The circuit breaker protects the system from cascading failures:

| State | Meaning |
|-------|---------|
| 0 (CLOSED) | Normal operation |
| 1 (HALF_OPEN) | Testing if provider recovered |
| 2 (OPEN) | Provider failing, requests rejected locally |

```bash
# Check circuit breaker state
kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | \
  grep copilot_circuit_breaker

# Force circuit breaker reset (use carefully)
kubectl exec -it POD -- curl -X POST localhost:8000/reset
```

## Multi-Provider Failover

Configure automatic failover:

```yaml
# In deployment/values.yaml
env:
  - name: COPILOT_PROVIDER
    value: "anthropic"
  - name: COPILOT_FAILOVER_PROVIDERS
    value: "openai,gemini"
  - name: COPILOT_FAILOVER_ON_ERROR_CODES
    value: "429,500,502,503"
```

## Escalation

1. **Provider support:** If errors persist after provider switch
2. **On-call secondary:** If all providers affected
3. **Incident commander:** If complete outage > 15 minutes

## Recovery Verification

```bash
# Error rate should drop below 5%
watch -n5 'kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | grep llm_errors'

# Circuit breaker should close
kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | \
  grep copilot_circuit_breaker_state
# Should show 0 (CLOSED)

# Test investigation
curl -X POST localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test","message":"what is 2+2"}'
```

## Related Runbooks

- [High Error Rate](./high-error-rate.md)
- [Circuit Breaker Open](./circuit-breaker.md)
- [Cost Spike](./cost-spike.md)
