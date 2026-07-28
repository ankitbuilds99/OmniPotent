# ADR-0005: Tenant Isolation Strategy

## Status
Accepted

## Context
Multi-tenant SaaS deployment requires:
- Data isolation between tenants
- Resource isolation (one tenant can't DOS another)
- Audit trail per tenant
- Compliance (tenant data doesn't leak)

Options considered:
1. **Separate databases per tenant**: Strong isolation, operational overhead
2. **Schema per tenant**: Medium isolation, migration complexity
3. **Row-Level Security (RLS)**: Shared schema, policy-enforced isolation
4. **Application-level filtering**: Weak isolation, error-prone

## Decision
Use PostgreSQL Row-Level Security (RLS) with application-level defense in depth.

### Implementation
1. **Database**: RLS policies on all tenant-scoped tables
2. **Application**: `tenant_id` injected via middleware, validated
3. **API**: Tenant context from JWT claims
4. **Audit**: All queries log tenant context

```sql
-- Example RLS policy
ALTER TABLE investigations ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON investigations
  USING (tenant_id = current_setting('app.current_tenant')::uuid);
```

## Consequences

### Positive
- Strong database-level isolation
- Single schema simplifies migrations
- Works with connection pooling (PgBouncer)
- Defense in depth (app + DB)

### Negative
- Must set app.current_tenant on every connection
- RLS adds query overhead (~2-5%)
- Testing requires tenant context setup

### Mitigations
- Middleware automatically sets tenant context
- Connection pool validates tenant setting
- Test fixtures create isolated tenant contexts
