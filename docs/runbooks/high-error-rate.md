# Runbook: High Error Rate

**Alert:** `HighErrorRate`
**Severity:** Critical
**SLO Impact:** Availability SLO breach imminent

## Overview

This alert fires when the HTTP error rate (5xx responses) exceeds 5% over a 5-minute window.

## Impact

- Users experiencing failed requests
- Potential cascade failures in dependent services
- Error budget consumption accelerating

## Diagnosis Steps

### 1. Check Error Distribution

```bash
# View error breakdown by endpoint
kubectl logs -l app=omnipotent --since=10m | \
  jq 'select(.level=="error")' | \
  jq -r '.path' | sort | uniq -c | sort -rn | head -20
```

### 2. Identify Error Types

```bash
# Check for specific error patterns
kubectl logs -l app=omnipotent --since=10m | \
  jq 'select(.level=="error")' | \
  jq -r '.msg' | sort | uniq -c | sort -rn | head -10
```

### 3. Check Resource Utilization

```bash
# Pod CPU/memory
kubectl top pods -l app=omnipotent

# Database connections
kubectl exec -it $(kubectl get pod -l app=omnipotent -o jsonpath='{.items[0].metadata.name}') -- \
  curl -s localhost:8000/cache/stats
```

### 4. Check Dependent Services

```bash
# LLM provider status
curl -s https://status.anthropic.com/api/v2/status.json | jq '.status'
curl -s https://status.openai.com/api/v2/status.json | jq '.status'

# Check circuit breaker state
kubectl exec -it $(kubectl get pod -l app=omnipotent -o jsonpath='{.items[0].metadata.name}') -- \
  curl -s localhost:8000/metrics/slo | grep circuit_breaker
```

## Common Causes & Fixes

### 1. LLM Provider Rate Limiting (429s)

**Symptoms:**
- Errors contain "rate_limit" or "429"
- Spike in LLM latency

**Fix:**
```bash
# Check current provider
kubectl exec -it POD -- curl -s localhost:8000/config | jq '.provider'

# Switch to backup provider if available
kubectl set env deployment/omnipotent COPILOT_PROVIDER=openai
```

### 2. Database Connection Exhaustion

**Symptoms:**
- "connection pool exhausted" errors
- Slow response times across all endpoints

**Fix:**
```bash
# Scale up connection pool
kubectl set env deployment/omnipotent COPILOT_DB_POOL_SIZE=20

# Or restart to clear zombie connections
kubectl rollout restart deployment/omnipotent
```

### 3. Memory Pressure / OOM

**Symptoms:**
- Container restarts
- "killed" or "OOMKilled" in pod events

**Fix:**
```bash
# Check for OOM events
kubectl describe pod -l app=omnipotent | grep -A5 "State:"

# Increase memory limit
kubectl patch deployment omnipotent -p '{"spec":{"template":{"spec":{"containers":[{"name":"omnipotent","resources":{"limits":{"memory":"2Gi"}}}]}}}}'
```

### 4. Downstream Service Failure

**Symptoms:**
- Errors referencing specific MCP server (datadog, kubernetes, etc.)
- Timeout errors

**Fix:**
```bash
# Check MCP subprocess health
kubectl exec -it POD -- curl -s localhost:8000/healthz | jq '.mcp'

# Restart specific session to reset MCP connections
# (handled automatically by session eviction)
```

## Escalation

If error rate persists after 15 minutes of investigation:

1. **Page secondary on-call** if primary cannot resolve
2. **Enable maintenance mode** if errors affect all users:
   ```bash
   kubectl set env deployment/omnipotent COPILOT_MAINTENANCE_MODE=true
   ```
3. **Engage vendor support** if LLM provider is root cause

## Recovery Verification

After fix applied, verify:

```bash
# Error rate should drop below 1%
curl -s localhost:9090/api/v1/query?query='sum(rate(omnipotent_http_requests_total{status=~"5.."}[5m]))/sum(rate(omnipotent_http_requests_total[5m]))' | jq '.data.result[0].value[1]'

# Active investigations should complete
curl -s localhost:8000/healthz | jq '.active_sessions'
```

## Related Runbooks

- [LLM Errors](./llm-errors.md)
- [Database Connection Issues](./database-connections.md)
- [Circuit Breaker Open](./circuit-breaker.md)
