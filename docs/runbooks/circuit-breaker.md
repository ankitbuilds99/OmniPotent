# Runbook: Circuit Breaker Open

**Alert:** `CircuitBreakerOpen`
**Severity:** Critical
**SLO Impact:** Investigations blocked for affected provider

## Overview

The circuit breaker protects the system from cascading failures by stopping requests to a failing downstream service. When open, all requests to that provider are rejected locally without making network calls.

## Circuit Breaker States

| State | Value | Behavior |
|-------|-------|----------|
| CLOSED | 0 | Normal operation, requests flow through |
| HALF_OPEN | 1 | Testing recovery, limited requests allowed |
| OPEN | 2 | Provider failing, requests rejected locally |

## Impact

When the circuit breaker is OPEN:
- New investigations using that provider fail immediately
- Error message: "Circuit breaker open, try again later"
- Prevents overwhelming a struggling service
- Reduces latency (fast fail vs. timeout wait)

## Diagnosis Steps

### 1. Identify Affected Provider

```bash
# Check which provider's circuit breaker is open
kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | \
  grep copilot_circuit_breaker_state

# Example output:
# copilot_circuit_breaker_state{provider="anthropic"} 2
```

### 2. Check Error History

```bash
# View recent errors that triggered the breaker
kubectl logs -l app=omnipotent --since=15m | \
  jq 'select(.logger=="omnipotent.llm" and .level=="error")' | \
  tail -20
```

### 3. Check Provider Status

| Provider | Status Page | API Health |
|----------|-------------|------------|
| Anthropic | status.anthropic.com | api.anthropic.com/health |
| OpenAI | status.openai.com | api.openai.com/health |
| Google | status.cloud.google.com | - |
| Groq | status.groq.com | - |

### 4. Check Circuit Breaker Config

```bash
# View current thresholds
kubectl exec -it POD -- env | grep CIRCUIT
# CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
# CIRCUIT_BREAKER_RECOVERY_TIMEOUT=60
# CIRCUIT_BREAKER_HALF_OPEN_REQUESTS=3
```

## Common Causes

### 1. Provider Outage

**Symptoms:**
- Status page shows incident
- All requests to provider fail
- Multiple customers affected

**Fix:**
```bash
# Switch to backup provider
kubectl set env deployment/omnipotent COPILOT_PROVIDER=openai

# Or enable automatic failover
kubectl set env deployment/omnipotent \
  COPILOT_FAILOVER_PROVIDERS="openai,gemini"
```

### 2. Rate Limiting

**Symptoms:**
- 429 errors in logs
- Happens during high traffic
- Provider status is healthy

**Fix:**
```bash
# Reduce request rate
kubectl set env deployment/omnipotent COPILOT_MAX_SESSIONS=10

# Or upgrade provider plan for higher limits

# Enable request queuing
kubectl set env deployment/omnipotent COPILOT_RATE_LIMIT_QUEUE=true
```

### 3. Invalid Credentials

**Symptoms:**
- 401/403 errors
- Happened after deployment
- Only one provider affected

**Fix:**
```bash
# Verify credentials
kubectl get secret omnipotent-secrets -o jsonpath='{.data.ANTHROPIC_API_KEY}' | \
  base64 -d | head -c 10

# Update if invalid
kubectl create secret generic omnipotent-secrets \
  --from-literal=ANTHROPIC_API_KEY="sk-..." \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4. Network Issues

**Symptoms:**
- Timeout errors
- Intermittent failures
- Provider status healthy

**Fix:**
```bash
# Test connectivity
kubectl exec -it POD -- curl -I https://api.anthropic.com/health

# Check egress network policies
kubectl get networkpolicy -A | grep omnipotent

# Check if proxy required
kubectl set env deployment/omnipotent \
  HTTPS_PROXY=http://proxy:8080
```

## Recovery Behavior

The circuit breaker automatically recovers:

1. **OPEN → HALF_OPEN** after `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` (default 60s)
2. **HALF_OPEN** allows `CIRCUIT_BREAKER_HALF_OPEN_REQUESTS` (default 3) test requests
3. If test requests succeed → **CLOSED**
4. If test requests fail → **OPEN** (restart recovery timer)

## Manual Reset

If you're confident the provider has recovered:

```bash
# Reset all circuit breakers (forces re-test)
kubectl exec -it POD -- curl -X POST localhost:8000/reset

# Restart deployment (clean state)
kubectl rollout restart deployment/omnipotent
```

## Tuning Circuit Breaker

Adjust thresholds based on traffic patterns:

```yaml
# More aggressive (faster open, slower recovery)
env:
  - name: CIRCUIT_BREAKER_FAILURE_THRESHOLD
    value: "3"
  - name: CIRCUIT_BREAKER_RECOVERY_TIMEOUT
    value: "120"

# More lenient (slower open, faster recovery)
env:
  - name: CIRCUIT_BREAKER_FAILURE_THRESHOLD
    value: "10"
  - name: CIRCUIT_BREAKER_RECOVERY_TIMEOUT
    value: "30"
```

## Multi-Provider Failover

Best practice: configure automatic failover so circuit breaker opening triggers provider switch:

```yaml
env:
  - name: COPILOT_PROVIDER
    value: "anthropic"
  - name: COPILOT_FAILOVER_PROVIDERS
    value: "openai,gemini"
  - name: COPILOT_FAILOVER_ON_CIRCUIT_OPEN
    value: "true"
```

## Escalation

1. **Provider support:** If outage confirmed
2. **Network team:** If connectivity issues
3. **On-call secondary:** If all providers affected

## Recovery Verification

```bash
# Circuit breaker should be CLOSED (0)
kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | \
  grep copilot_circuit_breaker_state
# copilot_circuit_breaker_state{provider="anthropic"} 0

# Test investigation
curl -X POST localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test","message":"hello"}'

# Monitor for re-opening
watch -n10 'kubectl exec -it POD -- curl -s localhost:8000/metrics/slo | grep circuit'
```

## Related Runbooks

- [LLM Errors](./llm-errors.md)
- [High Error Rate](./high-error-rate.md)
