# Runbook: [INCIDENT TYPE]

> **Severity**: [SEV1/SEV2/SEV3/SEV4]
> **Owner**: [Team/Individual]
> **Last Updated**: [Date]
> **Review Cadence**: Quarterly

## Overview

Brief description of this incident type and when this runbook applies.

## Detection

### Alerts
- **Alert Name**: `copilot_investigation_latency_high`
- **Threshold**: P95 latency > 30s for 5 minutes
- **PagerDuty Service**: DevOps Copilot

### Symptoms
- [ ] Symptom 1: Description
- [ ] Symptom 2: Description

### Dashboard Links
- [Grafana: Copilot Overview](https://grafana.internal/d/copilot-overview)
- [Datadog: LLM Metrics](https://app.datadoghq.com/dashboard/copilot-llm)

## Triage

### Quick Assessment (< 2 minutes)

```bash
# Check health endpoints
curl -s https://copilot.internal/healthz | jq .
curl -s https://copilot.internal/readyz | jq .

# Check pod status
kubectl get pods -n omnipotent

# Check recent events
kubectl get events -n omnipotent --sort-by='.lastTimestamp' | tail -20
```

### Impact Assessment

| Question | How to Check |
|----------|--------------|
| Users affected? | `kubectl logs -l app=copilot --tail=100 \| grep -c "ERROR"` |
| Investigations running? | Check /metrics/slo for active_investigations |
| SLO violation? | Check error budget dashboard |

## Mitigation

### Immediate Actions

1. **If LLM provider is failing**:
```bash
# Switch to fallback provider
kubectl set env deployment/omnipotent \
  COPILOT_FALLBACK_PROVIDER=openai -n omnipotent
```

2. **If rate limited**:
```bash
# Temporarily increase limits
kubectl set env deployment/omnipotent \
  COPILOT_RATE_LIMIT=200 -n omnipotent
```

3. **If pods are crashing**:
```bash
# Check recent logs
kubectl logs -l app=copilot --tail=200 -n omnipotent

# Describe pod for events
kubectl describe pod -l app=copilot -n omnipotent | tail -50
```

### Rollback Procedure

```bash
# Get current revision
kubectl rollout history deployment/omnipotent -n omnipotent

# Rollback to previous version
kubectl rollout undo deployment/omnipotent -n omnipotent

# Or rollback to specific revision
kubectl rollout undo deployment/omnipotent --to-revision=3 -n omnipotent
```

## Root Cause Analysis

### Common Causes

| Cause | Evidence | Solution |
|-------|----------|----------|
| LLM rate limiting | 429 errors in logs | Implement request queuing |
| Memory leak | OOMKilled events | Restart + investigate heap |
| Redis connection exhaustion | Connection pool errors | Increase pool size |
| Database connection timeout | Connection refused errors | Check PgBouncer status |

### Log Queries

```bash
# Find error patterns
kubectl logs -l app=copilot --since=1h | grep ERROR | sort | uniq -c | sort -rn

# Find slow investigations
kubectl logs -l app=copilot --since=1h | jq 'select(.duration_ms > 30000)'

# Find LLM failures
kubectl logs -l app=copilot --since=1h | grep "llm" | grep -i "error\|fail\|timeout"
```

## Resolution

### Verification Checklist

- [ ] Health endpoint returns 200
- [ ] Readiness endpoint returns 200
- [ ] New investigations completing successfully
- [ ] P95 latency within SLO (<30s)
- [ ] Error rate < 1%

### Communication

1. **Internal**: Update #omnipotent Slack channel
2. **Status Page**: Update status.internal.com if user-facing
3. **Ticket**: Link to incident ticket

## Post-Incident

### Required Actions

- [ ] Create post-mortem document
- [ ] Update this runbook if gaps found
- [ ] File follow-up issues for improvements
- [ ] Schedule post-incident review

### Metrics to Review

- Time to detection (TTD)
- Time to mitigation (TTM)
- Time to resolution (TTR)
- User impact (investigations affected)

## Appendix

### Contact Information

| Role | Contact |
|------|---------|
| Primary Oncall | @omnipotent-oncall |
| Secondary | @platform-team |
| Escalation | @engineering-manager |

### Related Runbooks

- [LLM Provider Failover](./LLM_FAILOVER.md)
- [Redis Cluster Recovery](./REDIS_RECOVERY.md)
- [Database Connection Issues](./DATABASE_ISSUES.md)
