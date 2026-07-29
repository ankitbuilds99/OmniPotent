"""Dashboard Builder.

Configurable dashboards, widgets, and scheduled reports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("devcopilot.analytics_advanced.dashboards")


class WidgetType(str, Enum):
    METRIC_CARD = "metric_card"
    TIME_SERIES = "time_series"
    PIE_CHART = "pie_chart"
    BAR_CHART = "bar_chart"
    TABLE = "table"
    HEATMAP = "heatmap"
    FUNNEL = "funnel"
    LEADERBOARD = "leaderboard"
    TEXT = "text"
    STATUS_INDICATOR = "status_indicator"


class ReportFrequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DashboardTheme(str, Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


@dataclass
class WidgetConfig:
    """Configuration for a widget."""

    widget_id: str
    widget_type: WidgetType
    title: str
    metric_name: str | None = None
    data_source: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (1, 1)
    refresh_interval_seconds: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.widget_id,
            "type": self.widget_type.value,
            "title": self.title,
            "metric": self.metric_name,
            "data_source": self.data_source,
            "filters": self.filters,
            "options": self.options,
            "position": {"row": self.position[0], "col": self.position[1]},
            "size": {"rows": self.size[0], "cols": self.size[1]},
            "refresh_interval": self.refresh_interval_seconds,
        }


@dataclass
class Widget:
    """A rendered widget with data."""

    config: WidgetConfig
    data: Any
    rendered_at: datetime
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "data": self.data,
            "rendered_at": self.rendered_at.isoformat(),
            "error": self.error,
        }


@dataclass
class DashboardConfig:
    """Configuration for a dashboard."""

    dashboard_id: str
    name: str
    description: str
    owner: str
    widgets: list[WidgetConfig]
    theme: DashboardTheme = DashboardTheme.SYSTEM
    default_time_range_hours: int = 24
    auto_refresh_seconds: int = 60
    is_public: bool = False
    tags: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.dashboard_id,
            "name": self.name,
            "description": self.description,
            "owner": self.owner,
            "theme": self.theme.value,
            "time_range_hours": self.default_time_range_hours,
            "auto_refresh": self.auto_refresh_seconds,
            "is_public": self.is_public,
            "tags": self.tags,
            "filters": self.filters,
            "widget_count": len(self.widgets),
        }


@dataclass
class Dashboard:
    """A rendered dashboard."""

    config: DashboardConfig
    widgets: list[Widget]
    rendered_at: datetime
    render_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "widgets": [w.to_dict() for w in self.widgets],
            "rendered_at": self.rendered_at.isoformat(),
            "render_time_ms": round(self.render_time_ms, 1),
        }


@dataclass
class ScheduledReport:
    """Configuration for a scheduled report."""

    report_id: str
    name: str
    dashboard_id: str
    frequency: ReportFrequency
    recipients: list[str]
    delivery_channels: list[str]
    enabled: bool = True
    last_sent_at: datetime | None = None
    next_scheduled_at: datetime | None = None
    include_summary: bool = True
    include_insights: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.report_id,
            "name": self.name,
            "dashboard_id": self.dashboard_id,
            "frequency": self.frequency.value,
            "recipients": self.recipients,
            "channels": self.delivery_channels,
            "enabled": self.enabled,
            "last_sent": (
                self.last_sent_at.isoformat() if self.last_sent_at else None
            ),
            "next_scheduled": (
                self.next_scheduled_at.isoformat() if self.next_scheduled_at else None
            ),
            "options": {
                "include_summary": self.include_summary,
                "include_insights": self.include_insights,
            },
        }


DataFetcher = Callable[[str, dict[str, Any]], Any]


class DashboardBuilder:
    """Builds and manages dashboards."""

    def __init__(self) -> None:
        self._dashboards: dict[str, DashboardConfig] = {}
        self._reports: dict[str, ScheduledReport] = {}
        self._data_fetchers: dict[str, DataFetcher] = {}
        self._dashboard_counter = 0
        self._widget_counter = 0
        self._report_counter = 0

        self._register_default_fetchers()

    def _register_default_fetchers(self) -> None:
        """Register default data fetchers."""
        self._data_fetchers["incident_metrics"] = self._fetch_incident_metrics
        self._data_fetchers["roi_summary"] = self._fetch_roi_summary
        self._data_fetchers["ai_performance"] = self._fetch_ai_performance
        self._data_fetchers["team_comparison"] = self._fetch_team_comparison
        self._data_fetchers["service_health"] = self._fetch_service_health
        self._data_fetchers["trends"] = self._fetch_trends
        self._data_fetchers["anomalies"] = self._fetch_anomalies

    def register_data_fetcher(
        self,
        source_name: str,
        fetcher: DataFetcher,
    ) -> None:
        """Register a custom data fetcher."""
        self._data_fetchers[source_name] = fetcher

    def create_dashboard(
        self,
        name: str,
        description: str,
        owner: str,
        **kwargs: Any,
    ) -> DashboardConfig:
        """Create a new dashboard."""
        self._dashboard_counter += 1

        config = DashboardConfig(
            dashboard_id=f"dashboard_{self._dashboard_counter}",
            name=name,
            description=description,
            owner=owner,
            widgets=[],
            **kwargs,
        )

        self._dashboards[config.dashboard_id] = config
        return config

    def add_widget(
        self,
        dashboard_id: str,
        widget_type: WidgetType,
        title: str,
        **kwargs: Any,
    ) -> WidgetConfig:
        """Add a widget to a dashboard."""
        if dashboard_id not in self._dashboards:
            raise ValueError(f"Dashboard not found: {dashboard_id}")

        self._widget_counter += 1

        widget = WidgetConfig(
            widget_id=f"widget_{self._widget_counter}",
            widget_type=widget_type,
            title=title,
            **kwargs,
        )

        self._dashboards[dashboard_id].widgets.append(widget)
        return widget

    def render_dashboard(
        self,
        dashboard_id: str,
        filters: dict[str, Any] | None = None,
    ) -> Dashboard:
        """Render a dashboard with all widgets."""
        if dashboard_id not in self._dashboards:
            raise ValueError(f"Dashboard not found: {dashboard_id}")

        start_time = datetime.now(timezone.utc)
        config = self._dashboards[dashboard_id]

        merged_filters = {**config.filters, **(filters or {})}

        widgets = []
        for widget_config in config.widgets:
            rendered = self._render_widget(widget_config, merged_filters)
            widgets.append(rendered)

        end_time = datetime.now(timezone.utc)
        render_time_ms = (end_time - start_time).total_seconds() * 1000

        return Dashboard(
            config=config,
            widgets=widgets,
            rendered_at=start_time,
            render_time_ms=render_time_ms,
        )

    def _render_widget(
        self,
        config: WidgetConfig,
        filters: dict[str, Any],
    ) -> Widget:
        """Render a single widget."""
        now = datetime.now(timezone.utc)

        try:
            merged_filters = {**config.filters, **filters}

            data = None
            if config.data_source and config.data_source in self._data_fetchers:
                fetcher = self._data_fetchers[config.data_source]
                data = fetcher(config.metric_name or "", merged_filters)
            else:
                data = self._generate_placeholder_data(config)

            return Widget(
                config=config,
                data=data,
                rendered_at=now,
            )
        except Exception as e:
            log.exception(f"Error rendering widget {config.widget_id}")
            return Widget(
                config=config,
                data=None,
                rendered_at=now,
                error=str(e),
            )

    def _generate_placeholder_data(
        self,
        config: WidgetConfig,
    ) -> Any:
        """Generate placeholder data for demo."""
        if config.widget_type == WidgetType.METRIC_CARD:
            return {
                "value": 42,
                "unit": "",
                "trend": "up",
                "change_percent": 12.5,
            }
        elif config.widget_type == WidgetType.TIME_SERIES:
            return {
                "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "datasets": [
                    {"label": "Value", "data": [10, 15, 8, 22, 18]},
                ],
            }
        elif config.widget_type == WidgetType.PIE_CHART:
            return {
                "labels": ["A", "B", "C"],
                "data": [40, 35, 25],
            }
        elif config.widget_type == WidgetType.TABLE:
            return {
                "columns": ["Name", "Value"],
                "rows": [["Item 1", "100"], ["Item 2", "200"]],
            }
        elif config.widget_type == WidgetType.STATUS_INDICATOR:
            return {
                "status": "healthy",
                "message": "All systems operational",
            }

        return {"placeholder": True}

    def _fetch_incident_metrics(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch incident metrics data."""
        return {
            "total_incidents": 47,
            "mttr_minutes": 28.5,
            "p1_count": 3,
            "p2_count": 12,
            "ai_resolution_rate": 0.78,
        }

    def _fetch_roi_summary(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch ROI summary data."""
        return {
            "total_savings": 125000,
            "time_saved_hours": 312,
            "per_incident_average": 2659,
            "engineer_cost_saved": 46800,
            "downtime_prevented_hours": 8.5,
        }

    def _fetch_ai_performance(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch AI performance data."""
        return {
            "accuracy": 0.87,
            "first_hypothesis_correct": 0.72,
            "fix_acceptance_rate": 0.81,
            "escalation_rate": 0.08,
        }

    def _fetch_team_comparison(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch team comparison data."""
        return {
            "teams": [
                {"name": "Platform", "mttr": 22, "incidents": 15},
                {"name": "API", "mttr": 35, "incidents": 18},
                {"name": "Frontend", "mttr": 28, "incidents": 8},
            ],
        }

    def _fetch_service_health(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch service health data."""
        return {
            "services": [
                {"name": "api-gateway", "status": "healthy", "score": 0.95},
                {"name": "auth-service", "status": "degraded", "score": 0.72},
                {"name": "payment-svc", "status": "healthy", "score": 0.88},
            ],
        }

    def _fetch_trends(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch trend data."""
        return {
            "direction": "down",
            "change_percent": -15.2,
            "is_significant": True,
        }

    def _fetch_anomalies(
        self,
        metric_name: str,
        filters: dict[str, Any],
    ) -> dict[str, Any]:
        """Fetch anomaly data."""
        return {
            "total": 3,
            "critical": 0,
            "high": 1,
            "recent": [
                {"type": "incident_spike", "severity": "high", "metric": "error_rate"},
            ],
        }

    def create_executive_dashboard(self, owner: str) -> DashboardConfig:
        """Create a pre-configured executive dashboard."""
        dashboard = self.create_dashboard(
            name="Executive Overview",
            description="High-level metrics and ROI for leadership",
            owner=owner,
            default_time_range_hours=720,
            tags=["executive", "roi", "overview"],
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.METRIC_CARD,
            "Total Savings (30d)",
            data_source="roi_summary",
            metric_name="total_savings",
            position=(0, 0),
            size=(1, 1),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.METRIC_CARD,
            "Time Saved (hours)",
            data_source="roi_summary",
            metric_name="time_saved_hours",
            position=(0, 1),
            size=(1, 1),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.METRIC_CARD,
            "AI Accuracy",
            data_source="ai_performance",
            metric_name="accuracy",
            position=(0, 2),
            size=(1, 1),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.TIME_SERIES,
            "Incident Trend",
            data_source="trends",
            metric_name="incident_count",
            position=(1, 0),
            size=(1, 2),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.BAR_CHART,
            "Team Comparison",
            data_source="team_comparison",
            position=(1, 2),
            size=(1, 1),
        )

        return dashboard

    def create_ops_dashboard(self, owner: str) -> DashboardConfig:
        """Create a pre-configured operations dashboard."""
        dashboard = self.create_dashboard(
            name="Operations Center",
            description="Real-time incident monitoring and response",
            owner=owner,
            default_time_range_hours=24,
            auto_refresh_seconds=30,
            tags=["ops", "realtime", "incidents"],
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.STATUS_INDICATOR,
            "System Status",
            data_source="service_health",
            position=(0, 0),
            size=(1, 1),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.METRIC_CARD,
            "Active Incidents",
            data_source="incident_metrics",
            metric_name="active_count",
            position=(0, 1),
            size=(1, 1),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.METRIC_CARD,
            "MTTR (today)",
            data_source="incident_metrics",
            metric_name="mttr_minutes",
            position=(0, 2),
            size=(1, 1),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.TABLE,
            "Service Health",
            data_source="service_health",
            position=(1, 0),
            size=(2, 2),
        )

        self.add_widget(
            dashboard.dashboard_id,
            WidgetType.TABLE,
            "Recent Anomalies",
            data_source="anomalies",
            position=(1, 2),
            size=(2, 1),
        )

        return dashboard

    def schedule_report(
        self,
        dashboard_id: str,
        name: str,
        frequency: ReportFrequency,
        recipients: list[str],
        channels: list[str] | None = None,
    ) -> ScheduledReport:
        """Schedule a report for a dashboard."""
        if dashboard_id not in self._dashboards:
            raise ValueError(f"Dashboard not found: {dashboard_id}")

        self._report_counter += 1

        now = datetime.now(timezone.utc)

        if frequency == ReportFrequency.DAILY:
            next_run = (now + timedelta(days=1)).replace(
                hour=8, minute=0, second=0, microsecond=0
            )
        elif frequency == ReportFrequency.WEEKLY:
            days_until_monday = (7 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_until_monday)).replace(
                hour=8, minute=0, second=0, microsecond=0
            )
        else:
            next_run = now.replace(day=1, hour=8, minute=0, second=0, microsecond=0)
            if next_run <= now:
                if next_run.month == 12:
                    next_run = next_run.replace(year=next_run.year + 1, month=1)
                else:
                    next_run = next_run.replace(month=next_run.month + 1)

        report = ScheduledReport(
            report_id=f"report_{self._report_counter}",
            name=name,
            dashboard_id=dashboard_id,
            frequency=frequency,
            recipients=recipients,
            delivery_channels=channels or ["email"],
            next_scheduled_at=next_run,
        )

        self._reports[report.report_id] = report
        return report

    def get_dashboard(self, dashboard_id: str) -> DashboardConfig | None:
        """Get a dashboard by ID."""
        return self._dashboards.get(dashboard_id)

    def list_dashboards(
        self,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[DashboardConfig]:
        """List dashboards with optional filters."""
        dashboards = list(self._dashboards.values())

        if owner:
            dashboards = [d for d in dashboards if d.owner == owner]

        if tag:
            dashboards = [d for d in dashboards if tag in d.tags]

        return dashboards

    def get_report(self, report_id: str) -> ScheduledReport | None:
        """Get a scheduled report by ID."""
        return self._reports.get(report_id)

    def list_reports(
        self,
        dashboard_id: str | None = None,
    ) -> list[ScheduledReport]:
        """List scheduled reports."""
        reports = list(self._reports.values())

        if dashboard_id:
            reports = [r for r in reports if r.dashboard_id == dashboard_id]

        return reports

    def generate_report_content(
        self,
        report_id: str,
    ) -> dict[str, Any]:
        """Generate content for a scheduled report."""
        report = self._reports.get(report_id)
        if not report:
            raise ValueError(f"Report not found: {report_id}")

        dashboard = self.render_dashboard(report.dashboard_id)

        content: dict[str, Any] = {
            "report_name": report.name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dashboard_name": dashboard.config.name,
            "widgets": [w.to_dict() for w in dashboard.widgets],
        }

        if report.include_summary:
            content["summary"] = self._generate_summary(dashboard)

        if report.include_insights:
            content["insights"] = self._generate_insights(dashboard)

        return content

    def _generate_summary(self, dashboard: Dashboard) -> str:
        """Generate text summary for report."""
        widget_count = len(dashboard.widgets)
        successful = sum(1 for w in dashboard.widgets if w.error is None)

        return (
            f"Dashboard '{dashboard.config.name}' rendered with "
            f"{successful}/{widget_count} widgets successful. "
            f"Render time: {dashboard.render_time_ms:.0f}ms."
        )

    def _generate_insights(self, dashboard: Dashboard) -> list[str]:
        """Generate insights from dashboard data."""
        insights = []

        for widget in dashboard.widgets:
            if widget.error:
                continue

            if widget.config.data_source == "trends":
                data = widget.data
                if isinstance(data, dict) and data.get("is_significant"):
                    direction = data.get("direction", "stable")
                    change = data.get("change_percent", 0)
                    if direction == "down":
                        insights.append(
                            f"Incidents trending down {abs(change):.0f}% - "
                            "recent improvements are working"
                        )
                    elif direction == "up":
                        insights.append(
                            f"Incidents trending up {abs(change):.0f}% - "
                            "investigate recent changes"
                        )

            if widget.config.data_source == "anomalies":
                data = widget.data
                if isinstance(data, dict) and data.get("critical", 0) > 0:
                    insights.append(
                        f"{data['critical']} critical anomalies detected - "
                        "immediate attention required"
                    )

        if not insights:
            insights.append("All metrics within normal ranges")

        return insights


_builder: DashboardBuilder | None = None


def get_dashboard_builder() -> DashboardBuilder:
    """Get the default dashboard builder."""
    global _builder
    if _builder is None:
        _builder = DashboardBuilder()
    return _builder
