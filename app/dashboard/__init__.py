"""Customer dashboard module.

Provides:
- ROI metrics and cost savings
- MTTR tracking and trends
- Investigation analytics
- Usage statistics
- Team performance insights

`record_investigation`, `get_dashboard_metrics` and `clear_investigations` are
coroutines: investigation history lives in the `investigations` table, not in
this process. `calculate_roi` and everything in `trends` are pure functions over
data you pass in.
"""

from app.dashboard.metrics import (
    DashboardMetrics,
    InvestigationOutcome,
    InvestigationRecord,
    InvestigationSummary,
    MTTRStats,
    ROIMetrics,
    UsageStats,
    clear_investigations,
    get_dashboard_metrics,
    get_investigation_series,
    calculate_roi,
    record_investigation,
)
from app.dashboard.trends import (
    MetricPolarity,
    TrendAnalyzer,
    Trend,
    TrendDirection,
    calculate_trends,
    infer_polarity,
)

__all__ = [
    # Metrics
    "DashboardMetrics",
    "InvestigationOutcome",
    "InvestigationRecord",
    "InvestigationSummary",
    "MTTRStats",
    "ROIMetrics",
    "UsageStats",
    "clear_investigations",
    "get_dashboard_metrics",
    "get_investigation_series",
    "calculate_roi",
    "record_investigation",
    # Trends
    "MetricPolarity",
    "TrendAnalyzer",
    "Trend",
    "TrendDirection",
    "calculate_trends",
    "infer_polarity",
]
