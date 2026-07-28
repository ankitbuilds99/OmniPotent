"""Embeddable status widgets for customer dashboards.

Generates embeddable widgets that customers can add to their own dashboards.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.status_page.widgets")


class WidgetTheme(Enum):
    """Widget color themes."""

    LIGHT = "light"
    DARK = "dark"
    AUTO = "auto"
    CUSTOM = "custom"


class WidgetSize(Enum):
    """Widget size options."""

    COMPACT = "compact"
    STANDARD = "standard"
    EXPANDED = "expanded"


@dataclass
class WidgetConfig:
    """Configuration for a status widget."""

    widget_id: str
    customer_id: str
    components: list[str]
    theme: WidgetTheme = WidgetTheme.AUTO
    size: WidgetSize = WidgetSize.STANDARD
    show_uptime: bool = True
    show_incidents: bool = True
    custom_css: str | None = None
    custom_colors: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    embed_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "widget_id": self.widget_id,
            "components": self.components,
            "theme": self.theme.value,
            "size": self.size.value,
            "show_uptime": self.show_uptime,
            "show_incidents": self.show_incidents,
            "embed_token": self.embed_token,
        }


@dataclass
class WidgetData:
    """Data rendered in a widget."""

    components: list[dict[str, Any]]
    overall_status: str
    has_incident: bool
    incident_title: str | None
    uptime_30d: float | None
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "components": self.components,
            "overall_status": self.overall_status,
            "has_incident": self.has_incident,
            "incident_title": self.incident_title,
            "uptime_30d": self.uptime_30d,
            "updated_at": self.updated_at.isoformat(),
        }


class WidgetGenerator:
    """Generates embeddable status widgets."""

    def __init__(
        self,
        base_url: str = "https://status.devopscopilot.com",
    ) -> None:
        self.base_url = base_url
        self._widgets: dict[str, WidgetConfig] = {}
        self._widget_counter = 0

    def create_widget(
        self,
        customer_id: str,
        components: list[str],
        theme: WidgetTheme = WidgetTheme.AUTO,
        size: WidgetSize = WidgetSize.STANDARD,
        show_uptime: bool = True,
        show_incidents: bool = True,
    ) -> WidgetConfig:
        """Create a new widget configuration."""
        import secrets

        self._widget_counter += 1
        widget_id = f"widget_{self._widget_counter}"
        embed_token = secrets.token_urlsafe(24)

        config = WidgetConfig(
            widget_id=widget_id,
            customer_id=customer_id,
            components=components,
            theme=theme,
            size=size,
            show_uptime=show_uptime,
            show_incidents=show_incidents,
            embed_token=embed_token,
        )

        self._widgets[widget_id] = config

        log.info(
            "widget created",
            extra={
                "widget_id": widget_id,
                "customer_id": customer_id,
                "components": components,
            },
        )

        return config

    def get_widget(self, widget_id: str) -> WidgetConfig | None:
        """Get a widget configuration."""
        return self._widgets.get(widget_id)

    def validate_token(self, widget_id: str, token: str) -> bool:
        """Validate a widget embed token."""
        widget = self._widgets.get(widget_id)
        if widget:
            return widget.embed_token == token
        return False

    def get_embed_code(
        self,
        widget_id: str,
        format: str = "script",
    ) -> str:
        """Generate embed code for a widget."""
        widget = self._widgets.get(widget_id)
        if not widget:
            raise ValueError(f"Widget not found: {widget_id}")

        components_param = ",".join(widget.components)

        if format == "script":
            return f'''<script src="{self.base_url}/widget.js"
        data-widget-id="{widget_id}"
        data-token="{widget.embed_token}"
        data-components="{components_param}"
        data-theme="{widget.theme.value}">
</script>'''

        elif format == "iframe":
            return f'''<iframe
    src="{self.base_url}/embed/{widget_id}?token={widget.embed_token}"
    style="width: 300px; height: 200px; border: none;"
    title="Status Widget">
</iframe>'''

        elif format == "react":
            components_str = "', '".join(widget.components)
            return f'''import {{ StatusWidget }} from '@devopscopilot/status-widget';

<StatusWidget
    widgetId="{widget_id}"
    token="{widget.embed_token}"
    components={{['{components_str}']}}
    theme="{widget.theme.value}"
/>'''

        else:
            raise ValueError(f"Unknown format: {format}")

    def generate_widget_data(
        self,
        widget_id: str,
        component_statuses: dict[str, str],
        active_incident: dict[str, Any] | None = None,
        uptimes: dict[str, float] | None = None,
    ) -> WidgetData:
        """Generate data for a widget."""
        widget = self._widgets.get(widget_id)
        if not widget:
            raise ValueError(f"Widget not found: {widget_id}")

        components = []
        worst_status = "operational"
        status_priority = {
            "operational": 0,
            "maintenance": 1,
            "degraded": 2,
            "partial_outage": 3,
            "major_outage": 4,
        }

        for comp_id in widget.components:
            status = component_statuses.get(comp_id, "operational")
            components.append({
                "id": comp_id,
                "status": status,
            })

            if status_priority.get(status, 0) > status_priority.get(worst_status, 0):
                worst_status = status

        avg_uptime = None
        if widget.show_uptime and uptimes:
            relevant = [uptimes.get(c, 100.0) for c in widget.components]
            if relevant:
                avg_uptime = sum(relevant) / len(relevant)

        return WidgetData(
            components=components,
            overall_status=worst_status,
            has_incident=active_incident is not None,
            incident_title=active_incident.get("title") if active_incident else None,
            uptime_30d=avg_uptime,
            updated_at=datetime.now(timezone.utc),
        )

    def render_widget_html(
        self,
        widget_id: str,
        data: WidgetData,
    ) -> str:
        """Render widget as HTML."""
        widget = self._widgets.get(widget_id)
        if not widget:
            raise ValueError(f"Widget not found: {widget_id}")

        status_colors = {
            "operational": "#22c55e",
            "degraded": "#eab308",
            "partial_outage": "#f97316",
            "major_outage": "#ef4444",
            "maintenance": "#3b82f6",
        }

        bg_color = "#ffffff" if widget.theme == WidgetTheme.LIGHT else "#1f2937"
        text_color = "#111827" if widget.theme == WidgetTheme.LIGHT else "#f9fafb"
        border_color = "#e5e7eb" if widget.theme == WidgetTheme.LIGHT else "#374151"

        status_color = status_colors.get(data.overall_status, "#6b7280")

        components_html = ""
        for comp in data.components:
            comp_color = status_colors.get(comp["status"], "#6b7280")
            components_html += f'''
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid {border_color};">
                <span>{comp["id"]}</span>
                <span style="width: 10px; height: 10px; border-radius: 50%; background: {comp_color};"></span>
            </div>'''

        uptime_html = ""
        if data.uptime_30d is not None:
            uptime_html = f'<div style="margin-top: 12px; font-size: 12px; color: #6b7280;">30-day uptime: {data.uptime_30d:.2f}%</div>'

        incident_html = ""
        if data.has_incident and data.incident_title:
            incident_html = f'''
            <div style="margin-top: 12px; padding: 8px; background: {status_color}22; border-radius: 4px; font-size: 12px;">
                {data.incident_title}
            </div>'''

        return f'''
<!DOCTYPE html>
<html>
<head>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
    </style>
</head>
<body>
    <div style="
        background: {bg_color};
        color: {text_color};
        padding: 16px;
        border-radius: 8px;
        border: 1px solid {border_color};
        max-width: 300px;
    ">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <span style="font-weight: 600;">Status</span>
            <span style="
                padding: 4px 8px;
                border-radius: 4px;
                background: {status_color}22;
                color: {status_color};
                font-size: 12px;
                font-weight: 500;
            ">{data.overall_status.replace('_', ' ').title()}</span>
        </div>
        {components_html}
        {uptime_html}
        {incident_html}
        <div style="margin-top: 12px; font-size: 10px; color: #9ca3af;">
            Updated {data.updated_at.strftime("%H:%M UTC")}
        </div>
    </div>
</body>
</html>'''

    def get_widget_js(self) -> str:
        """Get the JavaScript code for embedding widgets."""
        return f'''
(function() {{
    const script = document.currentScript;
    const widgetId = script.getAttribute('data-widget-id');
    const token = script.getAttribute('data-token');
    const components = script.getAttribute('data-components');
    const theme = script.getAttribute('data-theme') || 'auto';

    const container = document.createElement('div');
    container.id = 'status-widget-' + widgetId;
    script.parentNode.insertBefore(container, script);

    async function fetchStatus() {{
        try {{
            const response = await fetch(
                '{self.base_url}/api/widget/' + widgetId + '?token=' + token
            );
            const data = await response.json();
            renderWidget(data);
        }} catch (e) {{
            console.error('Status widget error:', e);
        }}
    }}

    function renderWidget(data) {{
        const statusColors = {{
            operational: '#22c55e',
            degraded: '#eab308',
            partial_outage: '#f97316',
            major_outage: '#ef4444',
            maintenance: '#3b82f6'
        }};

        const color = statusColors[data.overall_status] || '#6b7280';

        container.innerHTML = `
            <div style="
                padding: 12px 16px;
                border-radius: 8px;
                border: 1px solid #e5e7eb;
                font-family: -apple-system, sans-serif;
                font-size: 14px;
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span>Your Platform Status</span>
                    <span style="
                        width: 8px;
                        height: 8px;
                        border-radius: 50%;
                        background: ${{color}};
                    "></span>
                </div>
                ${{data.has_incident ? `<div style="margin-top: 8px; font-size: 12px; color: #ef4444;">${{data.incident_title}}</div>` : ''}}
            </div>
        `;
    }}

    fetchStatus();
    setInterval(fetchStatus, 60000);
}})();
'''

    def list_customer_widgets(self, customer_id: str) -> list[WidgetConfig]:
        """List all widgets for a customer."""
        return [w for w in self._widgets.values() if w.customer_id == customer_id]

    def delete_widget(self, widget_id: str) -> bool:
        """Delete a widget."""
        if widget_id in self._widgets:
            del self._widgets[widget_id]
            return True
        return False


_generator: WidgetGenerator | None = None


def get_widget_generator() -> WidgetGenerator:
    """Get the default widget generator."""
    global _generator
    if _generator is None:
        _generator = WidgetGenerator()
    return _generator
