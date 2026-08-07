# Runbook: Database Connection Issues

**Alert:** `DatabaseConnectionsLow`
**Severity:** Critical
**SLO Impact:** All database operations blocked

## Overview

This alert fires when available database connections drop below 5. This indicates connection pool exhaustion and will cause request failures.

## Impact

- New investigations cannot start (cannot create threads)
- Tenant auth fails (cannot validate API keys)
- Audit logging fails (compliance risk)
- Session state cannot be persisted

## Diagnosis Steps

### 1. Check Connection Pool Status

```bash
# Current pool state
kubectl exec -it POD -- curl -s localhost:8000/cache/stats | jq '.'

# If using PostgreSQL, check active connections
kubectl exec -it POD -- psql $DATABASE_URL -c "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database();"
```

### 2. Check for Connection Leaks

```bash
# Look for long-running queries
kubectl exec -it POD -- psql $DATABASE_URL -c "
SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.query_start) > interval '5 minutes'
AND state != 'idle'
ORDER BY duration DESC;
"
```

### 3. Check for Blocked Queries

```bash
# Check for lock contention
kubectl exec -it POD -- psql $DATABASE_URL -c "
SELECT blocked_locks.pid AS blocked_pid,
       blocked_activity.usename AS blocked_user,
       blocking_locks.pid AS blocking_pid,
       blocking_activity.usename AS blocking_user,
       blocked_activity.query AS blocked_statement
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
"
```

### 4. Check Pod Memory

```bash
# High memory can cause connection issues
kubectl top pod -l app=omnipotent

# Check for memory pressure
kubectl describe pod POD | grep -A10 "Conditions:"
```

## Common Causes & Fixes

### 1. Connection Leak in Application

**Symptoms:**
- Connections grow over time
- No corresponding active queries
- Restarts temporarily fix

**Fix:**
```bash
# Immediate: restart pods to release connections
kubectl rollout restart deployment/omnipotent

# Permanent: ensure connection context managers are used
# Check code for missing `async with` on db sessions
```

### 2. Slow Queries Holding Connections

**Symptoms:**
- Long-running queries in pg_stat_activity
- High query latency metrics

**Fix:**
```bash
# Kill long-running queries (careful!)
kubectl exec -it POD -- psql $DATABASE_URL -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE duration > interval '10 minutes'
AND query NOT LIKE '%pg_stat_activity%';
"

# Add query timeout
kubectl set env deployment/omnipotent COPILOT_DB_STATEMENT_TIMEOUT=30000
```

### 3. Pool Size Too Small for Load

**Symptoms:**
- Connection wait time increasing
- Healthy database, but app can't get connections

**Fix:**
```bash
# Increase pool size (ensure DB can handle it)
kubectl set env deployment/omnipotent COPILOT_DB_POOL_SIZE=25 COPILOT_DB_MAX_OVERFLOW=10

# Check DB max_connections
kubectl exec -it POD -- psql $DATABASE_URL -c "SHOW max_connections;"
```

### 4. Database Max Connections Reached

**Symptoms:**
- "too many connections" errors in DB logs
- Multiple apps sharing same DB

**Fix:**
```bash
# Check who's using connections
kubectl exec -it POD -- psql $DATABASE_URL -c "
SELECT usename, application_name, count(*)
FROM pg_stat_activity
GROUP BY usename, application_name
ORDER BY count DESC;
"

# Increase max_connections (requires DB restart)
# Or use PgBouncer for connection pooling
```

### 5. Network Issues to Database

**Symptoms:**
- Intermittent connection errors
- "connection refused" or timeouts

**Fix:**
```bash
# Test connectivity
kubectl exec -it POD -- nc -zv $DB_HOST 5432

# Check DNS resolution
kubectl exec -it POD -- nslookup $DB_HOST

# Check network policies
kubectl get networkpolicy -A
```

## Connection Pool Tuning

Recommended settings based on load:

| Concurrent Users | Pool Size | Max Overflow | Statement Timeout |
|-----------------|-----------|--------------|-------------------|
| < 50 | 10 | 5 | 30s |
| 50-200 | 20 | 10 | 30s |
| 200-500 | 30 | 15 | 45s |
| > 500 | 50+ | 20 | 60s |

```bash
# Apply settings
kubectl set env deployment/omnipotent \
  COPILOT_DB_POOL_SIZE=20 \
  COPILOT_DB_MAX_OVERFLOW=10 \
  COPILOT_DB_POOL_TIMEOUT=30 \
  COPILOT_DB_STATEMENT_TIMEOUT=30000
```

## Emergency: Force Release Connections

If connections are stuck and restart doesn't help:

```bash
# Terminate all omnipotent connections (data loss risk!)
kubectl exec -it POD -- psql $DATABASE_URL -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE application_name LIKE '%omnipotent%'
AND pid <> pg_backend_pid();
"
```

## Escalation

1. **DBA:** If database-side issues (max_connections, replication lag)
2. **Platform team:** If network/infrastructure issues
3. **On-call secondary:** If cannot resolve in 15 minutes

## Recovery Verification

```bash
# Available connections should be > 5
kubectl exec -it POD -- curl -s localhost:8000/cache/stats | jq '.db_pool_available'

# No connection errors in logs
kubectl logs -l app=omnipotent --since=5m | grep -i "connection" | grep -i "error"

# Test database operations
curl -X POST localhost:8000/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test","message":"hello"}'
```

## Related Runbooks

- [High Error Rate](./high-error-rate.md)
- [Slow Investigations](./slow-investigations.md)
