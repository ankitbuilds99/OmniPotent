"""Customer dashboard module.

Provides:
- ROI metrics and cost savings
- MTTR tracking and trends
- Investigation analytics
- Usage statistics
- Team performance insights
"""

from app.dashboard.metrics import (
    DashboardMetrics,
    InvestigationSummary,
    MTTRStats,
    ROIMetrics,
    UsageStats,
    get_dashboard_metrics,
    calculate_roi,
)
from app.dashboard.trends import (
    TrendAnalyzer,
    Trend,
    TrendDirection,
    calculate_trends,
)

__all__ = [
    # Metrics
    "DashboardMetrics",
    "InvestigationSummary",
    "MTTRStats",
    "ROIMetrics",
    "UsageStats",
    "get_dashboard_metrics",
    "calculate_roi",
    # Trends
    "TrendAnalyzer",
    "Trend",
    "TrendDirection",
    "calculate_trends",
]
