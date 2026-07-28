"""Usage and product analytics module.

Provides:
- Event tracking
- Usage metrics collection
- Dashboard data aggregation
- Trend analysis
- Reporting
- MRR and revenue metrics
- Cohort retention analysis
- Customer lifetime value (LTV)
"""

from app.analytics.tracker import (
    AnalyticsTracker,
    AnalyticsEvent,
    EventType,
    track_event,
    track_investigation,
    track_runbook_execution,
    track_user_action,
    get_usage_summary,
    get_trend_data,
    get_top_users,
    get_investigation_metrics,
)
from app.analytics.product_metrics import (
    RevenueMetrics,
    CohortAnalysis,
    CohortRetention,
    UsageMetrics,
    AnalyticsManager,
    MetricPeriod,
)

__all__ = [
    "AnalyticsTracker",
    "AnalyticsEvent",
    "EventType",
    "track_event",
    "track_investigation",
    "track_runbook_execution",
    "track_user_action",
    "get_usage_summary",
    "get_trend_data",
    "get_top_users",
    "get_investigation_metrics",
    "RevenueMetrics",
    "CohortAnalysis",
    "CohortRetention",
    "UsageMetrics",
    "AnalyticsManager",
    "MetricPeriod",
]
