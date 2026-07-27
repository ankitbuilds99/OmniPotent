"""Report generation module.

Supports multiple export formats:
- PDF reports with charts and styling
- CSV data exports
- JSON data exports
- Markdown reports
"""

from app.reports.generator import (
    ReportGenerator,
    ReportConfig,
    ReportSection,
    ReportFormat,
    generate_investigation_report,
    generate_postmortem_report,
    generate_analytics_report,
    export_data,
)

__all__ = [
    "ReportGenerator",
    "ReportConfig",
    "ReportSection",
    "ReportFormat",
    "generate_investigation_report",
    "generate_postmortem_report",
    "generate_analytics_report",
    "export_data",
]
