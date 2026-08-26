"""The documents a brand new org's knowledge base starts with.

These used to be built inline by the API router and pushed into a per-process
`KnowledgeBase`. They are seeded into `knowledge_documents` now, once per org,
so an org that edits or deletes one keeps that decision.

Nothing here is mutated — `app.knowledge_base.store` only reads these to build
rows — so one module-level list is safe to share.
"""

from __future__ import annotations

from app.knowledge_base.rag import Document, DocumentType

DEFAULT_DOCUMENTS: list[Document] = [
    Document(
        id="kb-001",
        title="Database Connection Pool Exhaustion",
        content="""## Symptoms
- High latency on database queries
- Errors: "Connection pool exhausted"
- Increasing queue depth in metrics

## Root Cause Analysis
Connection pool exhaustion typically occurs when:
1. Connection leaks (not returning connections)
2. Long-running queries holding connections
3. Pool size too small for load

## Resolution Steps
1. Check current pool usage: `SELECT count(*) FROM pg_stat_activity`
2. Identify long-running queries
3. If urgent: Restart the affected service
4. For permanent fix: Increase pool size or fix leaks

## Prevention
- Set connection timeouts
- Use connection pool monitoring
- Regular load testing""",
        doc_type=DocumentType.RUNBOOK,
        tags=["database", "postgres", "performance"],
        author="Platform Team",
        service="database",
    ),
    Document(
        id="kb-002",
        title="Kubernetes Pod Crash Loop",
        content="""## Definition
A CrashLoopBackOff occurs when a container repeatedly fails to start.

## Debugging Steps
1. Get pod status: `kubectl describe pod <name>`
2. Check logs: `kubectl logs <pod> --previous`
3. Check events: `kubectl get events --sort-by=.metadata.creationTimestamp`

## Common Causes
- Missing config/secrets
- Resource limits too low
- Image pull failures
- Application startup errors

## Quick Fixes
- Increase memory limits
- Fix readiness/liveness probes
- Check image tag exists""",
        doc_type=DocumentType.RUNBOOK,
        tags=["kubernetes", "k8s", "pods", "debugging"],
        author="SRE Team",
    ),
    Document(
        id="kb-003",
        title="MTTR - Mean Time To Recovery",
        content="""## Definition
Mean Time To Recovery (MTTR) measures the average time to recover from a failure.

## Formula
MTTR = Total Downtime / Number of Incidents

## Best Practices
- Target: < 30 minutes for critical services
- Track by service and team
- Include detection + diagnosis + recovery time

## Improvement Strategies
- Automated rollback procedures
- Pre-built runbooks
- AI-assisted root cause analysis""",
        doc_type=DocumentType.DOCUMENTATION,
        tags=["metrics", "sre", "reliability"],
        author="SRE Team",
    ),
    Document(
        id="kb-004",
        title="High CPU Usage Investigation",
        content="""## Symptoms
- CPU utilization > 80%
- Slow response times
- Service degradation

## Investigation Steps
1. Identify hot processes: `top` or `htop`
2. Check for runaway queries or jobs
3. Review recent deployments
4. Check for memory pressure causing swapping

## Resolution
- Scale horizontally (add pods)
- Optimize hot code paths
- Add rate limiting
- Review and cancel runaway jobs""",
        doc_type=DocumentType.RUNBOOK,
        tags=["cpu", "performance", "investigation"],
        author="Platform Team",
    ),
    Document(
        id="kb-005",
        title="Service Mesh Troubleshooting",
        content="""## Common Issues
- Sidecar injection not working
- mTLS certificate issues
- Service discovery failures

## Debugging Commands
```bash
# Check Istio proxy status
istioctl proxy-status

# Analyze configuration
istioctl analyze

# Check sidecar logs
kubectl logs <pod> -c istio-proxy
```

## Best Practices
- Use strict mTLS mode
- Configure proper timeouts
- Set up circuit breakers""",
        doc_type=DocumentType.RUNBOOK,
        tags=["istio", "service-mesh", "networking"],
        author="Platform Team",
    ),
]
