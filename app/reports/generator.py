"""Report generation system.

Generates PDF, CSV, JSON, and Markdown reports for:
- Investigation reports
- Postmortem documents
- Analytics dashboards
- Data exports
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.reports.generator")


class ReportFormat(Enum):
    """Available report formats."""

    PDF = "pdf"
    CSV = "csv"
    JSON = "json"
    MARKDOWN = "md"
    HTML = "html"


@dataclass
class ReportSection:
    """A section in a report."""

    title: str
    content: str | list[dict[str, Any]] | dict[str, Any]
    section_type: str = "text"
    chart_type: str | None = None
    level: int = 1


@dataclass
class ReportConfig:
    """Configuration for report generation."""

    title: str
    subtitle: str = ""
    author: str = "DevOps Copilot"
    logo_url: str = ""
    theme: str = "default"
    page_size: str = "A4"
    orientation: str = "portrait"
    include_toc: bool = True
    include_page_numbers: bool = True
    header_text: str = ""
    footer_text: str = ""
    watermark: str = ""


@dataclass
class GeneratedReport:
    """A generated report."""

    id: str
    format: ReportFormat
    filename: str
    content: bytes
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class ReportGenerator:
    """Generates reports in various formats."""

    def __init__(self, config: ReportConfig):
        self.config = config
        self.sections: list[ReportSection] = []

    def add_section(self, section: ReportSection) -> None:
        """Add a section to the report."""
        self.sections.append(section)

    def add_text(self, title: str, content: str, level: int = 1) -> None:
        """Add a text section."""
        self.add_section(ReportSection(
            title=title,
            content=content,
            section_type="text",
            level=level,
        ))

    def add_table(
        self,
        title: str,
        data: list[dict[str, Any]],
        level: int = 1,
    ) -> None:
        """Add a table section."""
        self.add_section(ReportSection(
            title=title,
            content=data,
            section_type="table",
            level=level,
        ))

    def add_chart(
        self,
        title: str,
        data: list[dict[str, Any]],
        chart_type: str = "bar",
        level: int = 1,
    ) -> None:
        """Add a chart section."""
        self.add_section(ReportSection(
            title=title,
            content=data,
            section_type="chart",
            chart_type=chart_type,
            level=level,
        ))

    def add_key_value(
        self,
        title: str,
        data: dict[str, Any],
        level: int = 1,
    ) -> None:
        """Add a key-value section."""
        self.add_section(ReportSection(
            title=title,
            content=data,
            section_type="key_value",
            level=level,
        ))

    def add_timeline(
        self,
        title: str,
        events: list[dict[str, Any]],
        level: int = 1,
    ) -> None:
        """Add a timeline section."""
        self.add_section(ReportSection(
            title=title,
            content=events,
            section_type="timeline",
            level=level,
        ))

    def generate(self, format: ReportFormat) -> GeneratedReport:
        """Generate the report in the specified format."""
        import secrets

        report_id = f"report_{secrets.token_hex(8)}"

        if format == ReportFormat.PDF:
            content = self._generate_pdf()
            ext = "pdf"
        elif format == ReportFormat.CSV:
            content = self._generate_csv()
            ext = "csv"
        elif format == ReportFormat.JSON:
            content = self._generate_json()
            ext = "json"
        elif format == ReportFormat.MARKDOWN:
            content = self._generate_markdown()
            ext = "md"
        elif format == ReportFormat.HTML:
            content = self._generate_html()
            ext = "html"
        else:
            raise ValueError(f"Unsupported format: {format}")

        filename = f"{self.config.title.lower().replace(' ', '_')}_{report_id}.{ext}"

        return GeneratedReport(
            id=report_id,
            format=format,
            filename=filename,
            content=content,
            metadata={
                "title": self.config.title,
                "sections": len(self.sections),
            },
        )

    def _generate_pdf(self) -> bytes:
        """Generate PDF report."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4, letter
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.platypus import (
                SimpleDocTemplate,
                Paragraph,
                Spacer,
                Table,
                TableStyle,
            )

            buffer = io.BytesIO()

            page_size = A4 if self.config.page_size == "A4" else letter
            if self.config.orientation == "landscape":
                page_size = (page_size[1], page_size[0])

            doc = SimpleDocTemplate(
                buffer,
                pagesize=page_size,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72,
            )

            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle(
                name="CustomTitle",
                parent=styles["Title"],
                fontSize=24,
                spaceAfter=30,
            ))
            styles.add(ParagraphStyle(
                name="CustomHeading1",
                parent=styles["Heading1"],
                fontSize=18,
                spaceAfter=12,
                spaceBefore=20,
            ))
            styles.add(ParagraphStyle(
                name="CustomHeading2",
                parent=styles["Heading2"],
                fontSize=14,
                spaceAfter=8,
                spaceBefore=15,
            ))

            story = []

            story.append(Paragraph(self.config.title, styles["CustomTitle"]))
            if self.config.subtitle:
                story.append(Paragraph(self.config.subtitle, styles["Normal"]))
            story.append(Spacer(1, 0.5 * inch))

            story.append(Paragraph(
                f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                styles["Normal"],
            ))
            story.append(Paragraph(f"Author: {self.config.author}", styles["Normal"]))
            story.append(Spacer(1, 0.5 * inch))

            for section in self.sections:
                heading_style = (
                    styles["CustomHeading1"]
                    if section.level == 1
                    else styles["CustomHeading2"]
                )
                story.append(Paragraph(section.title, heading_style))

                if section.section_type == "text":
                    text = str(section.content)
                    for para in text.split("\n\n"):
                        story.append(Paragraph(para, styles["Normal"]))
                        story.append(Spacer(1, 0.1 * inch))

                elif section.section_type == "table":
                    if isinstance(section.content, list) and section.content:
                        headers = list(section.content[0].keys())
                        data = [headers]
                        for row in section.content:
                            data.append([str(row.get(h, "")) for h in headers])

                        table = Table(data)
                        table.setStyle(TableStyle([
                            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, 0), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                            ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                            ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
                            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                            ("FONTSIZE", (0, 1), (-1, -1), 9),
                            ("GRID", (0, 0), (-1, -1), 1, colors.black),
                        ]))
                        story.append(table)

                elif section.section_type == "key_value":
                    if isinstance(section.content, dict):
                        data = [[k, str(v)] for k, v in section.content.items()]
                        table = Table(data, colWidths=[2 * inch, 4 * inch])
                        table.setStyle(TableStyle([
                            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                            ("ALIGN", (1, 0), (1, -1), "LEFT"),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("PADDING", (0, 0), (-1, -1), 6),
                        ]))
                        story.append(table)

                elif section.section_type == "timeline":
                    if isinstance(section.content, list):
                        for event in section.content:
                            ts = event.get("timestamp", "")
                            title = event.get("title", "")
                            desc = event.get("description", "")
                            story.append(Paragraph(
                                f"<b>{ts}</b> - {title}",
                                styles["Normal"],
                            ))
                            if desc:
                                story.append(Paragraph(desc, styles["Normal"]))
                            story.append(Spacer(1, 0.1 * inch))

                story.append(Spacer(1, 0.2 * inch))

            doc.build(story)
            return buffer.getvalue()

        except ImportError:
            log.warning("reportlab not installed, generating HTML fallback")
            return self._generate_html()

    def _generate_csv(self) -> bytes:
        """Generate CSV export of table data."""
        output = io.StringIO()
        writer = None

        for section in self.sections:
            if section.section_type == "table" and isinstance(section.content, list):
                if section.content:
                    if writer is None:
                        headers = list(section.content[0].keys())
                        writer = csv.DictWriter(output, fieldnames=headers)
                        writer.writeheader()

                    for row in section.content:
                        writer.writerow(row)

        return output.getvalue().encode("utf-8")

    def _generate_json(self) -> bytes:
        """Generate JSON export."""
        sections_list: list[dict[str, Any]] = []
        for section in self.sections:
            sections_list.append({
                "title": section.title,
                "type": section.section_type,
                "content": section.content,
            })

        data: dict[str, Any] = {
            "title": self.config.title,
            "subtitle": self.config.subtitle,
            "author": self.config.author,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections_list,
        }

        return json.dumps(data, indent=2, default=str).encode("utf-8")

    def _generate_markdown(self) -> bytes:
        """Generate Markdown report."""
        lines = []

        lines.append(f"# {self.config.title}")
        if self.config.subtitle:
            lines.append(f"*{self.config.subtitle}*")
        lines.append("")
        lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append(f"**Author:** {self.config.author}")
        lines.append("")
        lines.append("---")
        lines.append("")

        for section in self.sections:
            heading = "#" * (section.level + 1)
            lines.append(f"{heading} {section.title}")
            lines.append("")

            if section.section_type == "text":
                lines.append(str(section.content))

            elif section.section_type == "table":
                if isinstance(section.content, list) and section.content:
                    headers = list(section.content[0].keys())
                    lines.append("| " + " | ".join(headers) + " |")
                    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                    for row in section.content:
                        values = [str(row.get(h, "")) for h in headers]
                        lines.append("| " + " | ".join(values) + " |")

            elif section.section_type == "key_value":
                if isinstance(section.content, dict):
                    for k, v in section.content.items():
                        lines.append(f"- **{k}:** {v}")

            elif section.section_type == "timeline":
                if isinstance(section.content, list):
                    for event in section.content:
                        ts = event.get("timestamp", "")
                        title = event.get("title", "")
                        desc = event.get("description", "")
                        lines.append(f"- **{ts}** - {title}")
                        if desc:
                            lines.append(f"  {desc}")

            lines.append("")

        return "\n".join(lines).encode("utf-8")

    def _generate_html(self) -> bytes:
        """Generate HTML report."""
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.config.title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #1f2937;
            max-width: 900px;
            margin: 0 auto;
            padding: 40px 20px;
        }}
        .header {{
            border-bottom: 2px solid #e5e7eb;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}
        h1 {{ font-size: 2.5rem; margin-bottom: 8px; color: #111827; }}
        .subtitle {{ font-size: 1.1rem; color: #6b7280; }}
        .meta {{ font-size: 0.875rem; color: #9ca3af; margin-top: 16px; }}
        h2 {{ font-size: 1.5rem; margin: 32px 0 16px; color: #1f2937; border-bottom: 1px solid #e5e7eb; padding-bottom: 8px; }}
        h3 {{ font-size: 1.25rem; margin: 24px 0 12px; color: #374151; }}
        p {{ margin-bottom: 16px; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
            font-size: 0.875rem;
        }}
        th, td {{
            border: 1px solid #e5e7eb;
            padding: 12px;
            text-align: left;
        }}
        th {{ background: #f9fafb; font-weight: 600; }}
        tr:hover {{ background: #f9fafb; }}
        .key-value {{ margin: 16px 0; }}
        .key-value dt {{ font-weight: 600; color: #374151; }}
        .key-value dd {{ margin: 4px 0 12px 0; color: #6b7280; }}
        .timeline {{ border-left: 2px solid #e5e7eb; padding-left: 20px; margin: 16px 0; }}
        .timeline-item {{ position: relative; margin-bottom: 20px; }}
        .timeline-item::before {{
            content: '';
            position: absolute;
            left: -26px;
            top: 6px;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #6366f1;
        }}
        .timeline-time {{ font-weight: 600; color: #374151; }}
        .timeline-title {{ color: #1f2937; }}
        .timeline-desc {{ color: #6b7280; font-size: 0.875rem; }}
        @media print {{
            body {{ max-width: none; padding: 20px; }}
            .header {{ break-after: avoid; }}
            h2 {{ break-after: avoid; }}
            table {{ break-inside: avoid; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{self.config.title}</h1>
        {'<p class="subtitle">' + self.config.subtitle + '</p>' if self.config.subtitle else ''}
        <p class="meta">
            Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}<br>
            Author: {self.config.author}
        </p>
    </div>
"""

        for section in self.sections:
            tag = "h2" if section.level == 1 else "h3"
            html += f"    <{tag}>{section.title}</{tag}>\n"

            if section.section_type == "text":
                text = str(section.content)
                for para in text.split("\n\n"):
                    html += f"    <p>{para}</p>\n"

            elif section.section_type == "table":
                if isinstance(section.content, list) and section.content:
                    html += "    <table>\n        <thead>\n            <tr>\n"
                    headers = list(section.content[0].keys())
                    for h in headers:
                        html += f"                <th>{h}</th>\n"
                    html += "            </tr>\n        </thead>\n        <tbody>\n"
                    for row in section.content:
                        html += "            <tr>\n"
                        for h in headers:
                            html += f"                <td>{row.get(h, '')}</td>\n"
                        html += "            </tr>\n"
                    html += "        </tbody>\n    </table>\n"

            elif section.section_type == "key_value":
                if isinstance(section.content, dict):
                    html += '    <dl class="key-value">\n'
                    for k, v in section.content.items():
                        html += f"        <dt>{k}</dt>\n        <dd>{v}</dd>\n"
                    html += "    </dl>\n"

            elif section.section_type == "timeline":
                if isinstance(section.content, list):
                    html += '    <div class="timeline">\n'
                    for event in section.content:
                        ts = event.get("timestamp", "")
                        title = event.get("title", "")
                        desc = event.get("description", "")
                        html += f"""        <div class="timeline-item">
            <span class="timeline-time">{ts}</span>
            <span class="timeline-title"> - {title}</span>
            {'<p class="timeline-desc">' + desc + '</p>' if desc else ''}
        </div>
"""
                    html += "    </div>\n"

        html += """</body>
</html>"""

        return html.encode("utf-8")


async def generate_investigation_report(
    investigation_id: str,
    title: str,
    summary: str,
    findings: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    recommendations: list[str],
    metadata: dict[str, Any],
    format: ReportFormat = ReportFormat.PDF,
) -> GeneratedReport:
    """Generate an investigation report."""
    config = ReportConfig(
        title=f"Investigation Report: {title}",
        subtitle=f"Investigation ID: {investigation_id}",
    )

    gen = ReportGenerator(config)

    gen.add_text("Executive Summary", summary)

    gen.add_key_value("Investigation Details", {
        "Investigation ID": investigation_id,
        "Status": metadata.get("status", "completed"),
        "Severity": metadata.get("severity", "unknown"),
        "Started": metadata.get("started_at", ""),
        "Completed": metadata.get("completed_at", ""),
        "Duration": metadata.get("duration", ""),
    })

    if timeline:
        gen.add_timeline("Incident Timeline", timeline)

    if findings:
        gen.add_table("Findings", findings)

    if recommendations:
        rec_text = "\n".join(f"• {r}" for r in recommendations)
        gen.add_text("Recommendations", rec_text)

    return gen.generate(format)


async def generate_postmortem_report(
    incident_id: str,
    title: str,
    impact: str,
    root_cause: str,
    timeline: list[dict[str, Any]],
    action_items: list[dict[str, Any]],
    lessons_learned: list[str],
    metadata: dict[str, Any],
    format: ReportFormat = ReportFormat.PDF,
) -> GeneratedReport:
    """Generate a postmortem report."""
    config = ReportConfig(
        title=f"Postmortem: {title}",
        subtitle=f"Incident ID: {incident_id}",
    )

    gen = ReportGenerator(config)

    gen.add_key_value("Incident Summary", {
        "Incident ID": incident_id,
        "Severity": metadata.get("severity", ""),
        "Duration": metadata.get("duration", ""),
        "Affected Services": ", ".join(metadata.get("affected_services", [])),
        "Customer Impact": impact,
    })

    gen.add_text("Root Cause", root_cause)

    if timeline:
        gen.add_timeline("Incident Timeline", timeline)

    if action_items:
        gen.add_table("Action Items", action_items)

    if lessons_learned:
        lessons_text = "\n".join(f"• {lesson}" for lesson in lessons_learned)
        gen.add_text("Lessons Learned", lessons_text)

    return gen.generate(format)


async def generate_analytics_report(
    title: str,
    date_range: str,
    metrics: dict[str, Any],
    charts: list[dict[str, Any]],
    insights: list[str],
    format: ReportFormat = ReportFormat.PDF,
) -> GeneratedReport:
    """Generate an analytics report."""
    config = ReportConfig(
        title=title,
        subtitle=f"Period: {date_range}",
    )

    gen = ReportGenerator(config)

    gen.add_key_value("Key Metrics", metrics)

    for chart in charts:
        gen.add_chart(
            title=chart.get("title", "Chart"),
            data=chart.get("data", []),
            chart_type=chart.get("type", "bar"),
        )

    if insights:
        insights_text = "\n".join(f"• {i}" for i in insights)
        gen.add_text("Key Insights", insights_text)

    return gen.generate(format)


async def export_data(
    data: list[dict[str, Any]],
    filename: str,
    format: ReportFormat = ReportFormat.CSV,
) -> GeneratedReport:
    """Export data to CSV or JSON."""
    import secrets

    report_id = f"export_{secrets.token_hex(8)}"

    config = ReportConfig(title=filename)
    gen = ReportGenerator(config)
    gen.add_table("Data", data)

    if format == ReportFormat.CSV:
        content = gen._generate_csv()
        ext = "csv"
    else:
        content = gen._generate_json()
        ext = "json"

    return GeneratedReport(
        id=report_id,
        format=format,
        filename=f"{filename}.{ext}",
        content=content,
        metadata={"rows": len(data)},
    )
