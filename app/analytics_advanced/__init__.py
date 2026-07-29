"""Advanced Analytics and Metrics System.

Comprehensive analytics including:
- Core incident metrics (MTTA, MTTD, MTTI, MTTF, MTTV, MTTC, MTBF)
- ROI calculator with configurable cost models
- AI performance analytics
- Human-in-the-loop metrics
- Team and service analytics
- Trend analysis and forecasting
- Anomaly detection
- Custom dashboards and reports
"""

from app.analytics_advanced.core_metrics import (
    IncidentMetrics,
    LifecycleBreakdown,
    MetricsCalculator,
    get_metrics_calculator,
)
from app.analytics_advanced.roi_calculator import (
    CostModel,
    ROICalculation,
    IncidentROI,
    ROICalculator,
    get_roi_calculator,
)
from app.analytics_advanced.ai_performance import (
    AIMetrics,
    ConfidenceCalibration,
    FixQuality,
    AIPerformanceTracker,
    get_ai_tracker,
)
from app.analytics_advanced.human_loop import (
    ApprovalMetrics,
    ApprovalPattern,
    HumanLoopAnalytics,
    get_human_loop_analytics,
)
from app.analytics_advanced.team_analytics import (
    TeamMetrics,
    ServiceHealth,
    OnCallBurden,
    TeamAnalytics,
    get_team_analytics,
)
from app.analytics_advanced.trends import (
    TrendAnalysis,
    Forecast,
    IncidentPattern,
    TrendAnalyzer,
    get_trend_analyzer,
)
from app.analytics_advanced.anomaly_detection import (
    Anomaly,
    AnomalyType,
    AnomalyDetector,
    get_anomaly_detector,
)
from app.analytics_advanced.dashboards import (
    Widget,
    Dashboard,
    ScheduledReport,
    DashboardBuilder,
    get_dashboard_builder,
)

__all__ = [
    # Core Metrics
    "IncidentMetrics",
    "LifecycleBreakdown",
    "MetricsCalculator",
    "get_metrics_calculator",
    # ROI
    "CostModel",
    "ROICalculation",
    "IncidentROI",
    "ROICalculator",
    "get_roi_calculator",
    # AI Performance
    "AIMetrics",
    "ConfidenceCalibration",
    "FixQuality",
    "AIPerformanceTracker",
    "get_ai_tracker",
    # Human Loop
    "ApprovalMetrics",
    "ApprovalPattern",
    "HumanLoopAnalytics",
    "get_human_loop_analytics",
    # Team Analytics
    "TeamMetrics",
    "ServiceHealth",
    "OnCallBurden",
    "TeamAnalytics",
    "get_team_analytics",
    # Trends
    "TrendAnalysis",
    "Forecast",
    "IncidentPattern",
    "TrendAnalyzer",
    "get_trend_analyzer",
    # Anomaly Detection
    "Anomaly",
    "AnomalyType",
    "AnomalyDetector",
    "get_anomaly_detector",
    # Dashboards
    "Widget",
    "Dashboard",
    "ScheduledReport",
    "DashboardBuilder",
    "get_dashboard_builder",
]
