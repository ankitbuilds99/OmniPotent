# Post-Mortem: [INCIDENT TITLE]

**Date**: [YYYY-MM-DD]
**Duration**: [Start Time] - [End Time] ([X hours Y minutes])
**Severity**: [SEV1/SEV2/SEV3]
**Author**: [Name]
**Reviewers**: [Names]

## Executive Summary

[2-3 sentence summary of what happened and the impact]

## Timeline

All times in UTC.

| Time | Event |
|------|-------|
| 14:00 | Alert fired: `copilot_error_rate_high` |
| 14:02 | Oncall acknowledged, began investigation |
| 14:10 | Root cause identified: LLM provider rate limiting |
| 14:15 | Mitigation applied: Switched to fallback provider |
| 14:20 | Service restored to normal operation |
| 14:30 | Monitoring confirmed stable |

## Impact

### User Impact
- **Investigations affected**: ~150 (25% failure rate during incident)
- **Users impacted**: ~50 active users
- **Error messages**: "Investigation failed: please retry"

### Business Impact
- **SLO violation**: Availability dropped to 97.5% (target 99.5%)
- **Error budget consumed**: 2.5% (monthly budget 0.5%)

### Data Impact
- No data loss
- No data corruption
- No security implications

## Root Cause

[Detailed technical explanation of what caused the incident]

**Primary cause**: Anthropic API rate limit exceeded due to concurrent investigation spike.

**Contributing factors**:
1. No backpressure mechanism for queuing investigations
2. Rate limit retry logic had insufficient backoff
3. Fallback provider not triggered due to error classification bug

## Detection

**How was this detected?**
- PagerDuty alert: `copilot_error_rate_high` (threshold: >5% errors over 5 minutes)
- Time from incident start to detection: 2 minutes

**Were existing alerts effective?**
- Yes: Error rate alert caught the issue quickly
- Gap: No alert for approaching rate limits (would have been proactive)

## Response

**What went well:**
- Oncall responded within 2 minutes
- Root cause identified within 10 minutes
- Runbook was accurate and helpful

**What could be improved:**
- Fallback provider should have activated automatically
- Rate limit metrics should be visible in primary dashboard

## Action Items

| Priority | Action | Owner | Due Date | Status |
|----------|--------|-------|----------|--------|
| P0 | Fix fallback provider activation bug | @alice | 2026-07-20 | Done |
| P1 | Add rate limit approach alerting | @bob | 2026-07-25 | In Progress |
| P1 | Implement investigation queue with backpressure | @carol | 2026-08-01 | Not Started |
| P2 | Add rate limit metrics to primary dashboard | @dave | 2026-08-15 | Not Started |
| P3 | Update runbook with rate limit scenario | @alice | 2026-07-30 | Not Started |

## Lessons Learned

### What went well
1. Alert fired quickly and accurately
2. Runbook guided effective response
3. Team communication was clear

### What went poorly
1. Fallback provider didn't activate as designed
2. No visibility into rate limit headroom
3. Retry logic made the problem worse

### Where we got lucky
1. Incident happened during business hours
2. Secondary provider had capacity available
3. No customer-facing SLA breaches

## Supporting Information

### Relevant Graphs

[Link to Grafana dashboard snapshot]

### Related Incidents

- [INC-123](link): Similar rate limit issue in 2026-05
- [INC-456](link): Fallback provider configuration issue

### References

- [ADR-0004: Multi-Provider LLM Support](../../adr/0004-multi-provider-llm-support.md)
- [Runbook: LLM Provider Failover](../LLM_FAILOVER.md)
