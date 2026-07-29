"""Multi-format export for postmortems."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.postmortem_advanced.models import Postmortem

log = logging.getLogger("devcopilot.postmortem_advanced.export")


class ExportFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    HTML = "html"
    PDF = "pdf"
    CONFLUENCE = "confluence"
    NOTION = "notion"
    GOOGLE_DOCS = "google_docs"


class AudienceType(str, Enum):
    ENGINEERING = "engineering"
    LEADERSHIP = "leadership"
    CUSTOMER = "customer"
    COMPLIANCE = "compliance"


@dataclass
class ExportConfig:
    """Configuration for postmortem export."""

    format: ExportFormat = ExportFormat.MARKDOWN
    audience: AudienceType = AudienceType.ENGINEERING
    include_sections: list[str] = field(default_factory=list)
    exclude_sections: list[str] = field(default_factory=list)
    include_code_snippets: bool = True
    include_raw_logs: bool = False
    sanitize_names: bool = False
    max_length: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format.value,
            "audience": self.audience.value,
            "include_sections": self.include_sections,
            "exclude_sections": self.exclude_sections,
            "include_code_snippets": self.include_code_snippets,
            "include_raw_logs": self.include_raw_logs,
            "sanitize_names": self.sanitize_names,
            "max_length": self.max_length,
        }


@dataclass
class ExportResult:
    """Result of postmortem export."""

    content: str
    format: ExportFormat
    audience: AudienceType
    word_count: int
    exported_at: datetime
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format.value,
            "audience": self.audience.value,
            "word_count": self.word_count,
            "exported_at": self.exported_at.isoformat(),
            "warnings": self.warnings,
        }


class PostmortemExporter:
    """Exports postmortems to various formats."""

    def __init__(self) -> None:
        self._audience_configs: dict[AudienceType, dict[str, Any]] = {
            AudienceType.ENGINEERING: {
                "include_all": True,
                "include_code": True,
                "include_technical": True,
                "max_length": None,
            },
            AudienceType.LEADERSHIP: {
                "sections": [
                    "executive_summary",
                    "impact",
                    "root_cause",
                    "action_items",
                ],
                "include_code": False,
                "include_technical": False,
                "max_length": 1000,
            },
            AudienceType.CUSTOMER: {
                "sections": [
                    "executive_summary",
                    "impact",
                    "resolution",
                    "prevention",
                ],
                "include_code": False,
                "include_technical": False,
                "sanitize": True,
                "max_length": 500,
            },
            AudienceType.COMPLIANCE: {
                "include_all": True,
                "include_code": True,
                "include_technical": True,
                "formal_format": True,
            },
        }

    def export(
        self,
        postmortem: Postmortem,
        config: ExportConfig | None = None,
    ) -> ExportResult:
        """Export a postmortem."""
        config = config or ExportConfig()
        warnings: list[str] = []

        audience_config = self._audience_configs.get(
            config.audience, self._audience_configs[AudienceType.ENGINEERING]
        )

        if config.format == ExportFormat.MARKDOWN:
            content = self._export_markdown(postmortem, config, audience_config)
        elif config.format == ExportFormat.JSON:
            content = self._export_json(postmortem, config, audience_config)
        elif config.format == ExportFormat.HTML:
            content = self._export_html(postmortem, config, audience_config)
        elif config.format == ExportFormat.CONFLUENCE:
            content = self._export_confluence(postmortem, config, audience_config)
        elif config.format == ExportFormat.NOTION:
            content = self._export_notion(postmortem, config, audience_config)
        else:
            content = self._export_markdown(postmortem, config, audience_config)
            warnings.append(f"Format {config.format.value} not fully supported, using Markdown")

        if config.sanitize_names or audience_config.get("sanitize"):
            content = self._sanitize_content(content)

        max_length = config.max_length or audience_config.get("max_length")
        if max_length and len(content.split()) > max_length:
            content = self._truncate_content(content, max_length)
            warnings.append(f"Content truncated to {max_length} words")

        word_count = len(content.split())

        return ExportResult(
            content=content,
            format=config.format,
            audience=config.audience,
            word_count=word_count,
            exported_at=datetime.now(timezone.utc),
            warnings=warnings,
        )

    def _export_markdown(
        self,
        postmortem: Postmortem,
        config: ExportConfig,
        audience_config: dict[str, Any],
    ) -> str:
        """Export to Markdown format."""
        if audience_config.get("include_all"):
            return postmortem.to_markdown()

        lines = [
            f"# Postmortem: {postmortem.title}",
            "",
            f"**Incident ID:** {postmortem.incident_id}",
            f"**Severity:** {postmortem.severity}",
            "",
        ]

        sections = audience_config.get("sections", [])

        if "executive_summary" in sections:
            summary = postmortem.executive_summary
            if not audience_config.get("include_technical"):
                summary = self._simplify_technical_content(summary)
            lines.extend([
                "## Summary",
                "",
                summary,
                "",
            ])

        if "impact" in sections and postmortem.impact:
            lines.extend([
                "## Impact",
                "",
                f"- Affected users: {postmortem.impact.affected_users:,}",
                f"- Estimated revenue impact: ${postmortem.impact.estimated_lost_revenue:,.2f}",
                "",
            ])

        if "root_cause" in sections and postmortem.root_cause_analysis:
            summary = postmortem.root_cause_analysis.summary
            if not audience_config.get("include_technical"):
                summary = self._simplify_technical_content(summary)
            lines.extend([
                "## Root Cause",
                "",
                summary,
                "",
            ])

        if "action_items" in sections and postmortem.action_items:
            items = postmortem.action_items[:3]
            lines.extend([
                "## Key Actions",
                "",
            ])
            for item in items:
                owner = item.actual_owner or item.suggested_owner or "TBD"
                lines.append(f"- **{item.title}** (Owner: {owner})")
            lines.append("")

        if "resolution" in sections:
            lines.extend([
                "## Resolution",
                "",
                "The issue was identified and a fix was deployed.",
                "",
            ])

        if "prevention" in sections:
            lines.extend([
                "## Prevention Measures",
                "",
                "We are implementing safeguards to prevent this from recurring.",
                "",
            ])

        return "\n".join(lines)

    def _export_json(
        self,
        postmortem: Postmortem,
        config: ExportConfig,
        audience_config: dict[str, Any],
    ) -> str:
        """Export to JSON format."""
        data = postmortem.to_dict()

        if not audience_config.get("include_all"):
            sections = audience_config.get("sections", [])
            filtered_data: dict[str, Any] = {
                "id": data.get("id"),
                "incident_id": data.get("incident_id"),
                "header": data.get("header"),
            }

            if "executive_summary" in sections:
                filtered_data["executive_summary"] = data.get("executive_summary")
            if "impact" in sections:
                filtered_data["impact"] = data.get("impact")
            if "timeline" in sections:
                filtered_data["timeline"] = data.get("timeline")
            if "root_cause" in sections:
                filtered_data["root_cause_analysis"] = data.get("root_cause_analysis")
            if "action_items" in sections:
                items = data.get("action_items", [])[:5]
                filtered_data["action_items"] = items

            data = filtered_data

        if not audience_config.get("include_code"):
            if "root_cause_analysis" in data and data["root_cause_analysis"]:
                data["root_cause_analysis"].pop("code_snippets", None)

        return json.dumps(data, indent=2, default=str)

    def _export_html(
        self,
        postmortem: Postmortem,
        config: ExportConfig,
        audience_config: dict[str, Any],
    ) -> str:
        """Export to HTML format."""
        md_content = self._export_markdown(postmortem, config, audience_config)

        html_lines = [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            f"  <title>Postmortem: {postmortem.title}</title>",
            "  <style>",
            "    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }",
            "    h1 { color: #1a1a1a; border-bottom: 2px solid #e5e5e5; padding-bottom: 10px; }",
            "    h2 { color: #333; margin-top: 30px; }",
            "    table { border-collapse: collapse; width: 100%; margin: 20px 0; }",
            "    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }",
            "    th { background-color: #f5f5f5; }",
            "    code { background-color: #f5f5f5; padding: 2px 6px; border-radius: 3px; }",
            "    pre { background-color: #f5f5f5; padding: 15px; border-radius: 5px; overflow-x: auto; }",
            "    .severity-p1 { color: #dc3545; font-weight: bold; }",
            "    .severity-p2 { color: #fd7e14; font-weight: bold; }",
            "  </style>",
            "</head>",
            "<body>",
        ]

        for line in md_content.split("\n"):
            if line.startswith("# "):
                html_lines.append(f"  <h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html_lines.append(f"  <h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                html_lines.append(f"  <h3>{line[4:]}</h3>")
            elif line.startswith("**") and ":**" in line:
                parts = line.split(":**", 1)
                label = parts[0][2:]
                value = parts[1].strip() if len(parts) > 1 else ""
                html_lines.append(f"  <p><strong>{label}:</strong> {value}</p>")
            elif line.startswith("- "):
                html_lines.append(f"  <li>{line[2:]}</li>")
            elif line.startswith("|"):
                pass
            elif line.strip():
                html_lines.append(f"  <p>{line}</p>")

        html_lines.extend([
            "</body>",
            "</html>",
        ])

        return "\n".join(html_lines)

    def _export_confluence(
        self,
        postmortem: Postmortem,
        config: ExportConfig,
        audience_config: dict[str, Any],
    ) -> str:
        """Export to Confluence wiki format."""
        lines = [
            f"h1. Postmortem: {postmortem.title}",
            "",
            f"*Incident ID:* {postmortem.incident_id}",
            f"*Severity:* {postmortem.severity}",
            f"*Author:* {postmortem.author}",
            "",
            "----",
            "",
            "h2. Executive Summary",
            "",
            postmortem.executive_summary,
            "",
        ]

        if postmortem.impact:
            lines.extend([
                "h2. Impact Analysis",
                "",
                "|| Metric || Value ||",
                f"| Failed requests | {postmortem.impact.failed_requests:,} |",
                f"| Affected users | {postmortem.impact.affected_users:,} |",
                f"| Lost revenue | ${postmortem.impact.estimated_lost_revenue:,.2f} |",
                "",
            ])

        if postmortem.timeline:
            lines.extend([
                "h2. Timeline",
                "",
                "|| Time || Event || Source ||",
            ])
            for event in postmortem.timeline[:15]:
                lines.append(
                    f"| {event.timestamp.strftime('%H:%M:%S')} | "
                    f"{event.event} | {event.source.value} |"
                )
            lines.append("")

        if postmortem.root_cause_analysis:
            lines.extend([
                "h2. Root Cause Analysis",
                "",
                postmortem.root_cause_analysis.summary,
                "",
            ])

            if audience_config.get("include_code") and postmortem.root_cause_analysis.code_snippets:
                for snippet in postmortem.root_cause_analysis.code_snippets:
                    lines.extend([
                        f"h3. {snippet.file_path}",
                        "",
                        "{code:language=" + snippet.language + "}",
                    ])
                    if snippet.before_code:
                        lines.append(f"// BEFORE:\n{snippet.before_code}")
                    if snippet.after_code:
                        lines.append(f"\n// AFTER:\n{snippet.after_code}")
                    lines.extend([
                        "{code}",
                        "",
                    ])

        if postmortem.action_items:
            lines.extend([
                "h2. Action Items",
                "",
                "|| ID || Action || Owner || Due || Status ||",
            ])
            for item in postmortem.action_items:
                owner = item.actual_owner or item.suggested_owner or "TBD"
                due = item.due_date.strftime("%Y-%m-%d") if item.due_date else "TBD"
                lines.append(
                    f"| {item.item_id} | {item.title} | {owner} | {due} | {item.status.value} |"
                )
            lines.append("")

        return "\n".join(lines)

    def _export_notion(
        self,
        postmortem: Postmortem,
        config: ExportConfig,
        audience_config: dict[str, Any],
    ) -> str:
        """Export to Notion-compatible markdown."""
        lines = [
            f"# {postmortem.title}",
            "",
            "## Metadata",
            "",
            f"- **Incident ID:** {postmortem.incident_id}",
            f"- **Severity:** {postmortem.severity}",
            f"- **Author:** {postmortem.author}",
            f"- **Status:** {postmortem.status.value}",
            "",
            "---",
            "",
        ]

        lines.extend([
            "## Executive Summary",
            "",
            postmortem.executive_summary,
            "",
        ])

        if postmortem.impact:
            lines.extend([
                "## Impact",
                "",
                "| Metric | Value |",
                "| --- | --- |",
                f"| Failed Requests | {postmortem.impact.failed_requests:,} |",
                f"| Affected Users | {postmortem.impact.affected_users:,} |",
                f"| Lost Revenue | ${postmortem.impact.estimated_lost_revenue:,.2f} |",
                "",
            ])

        if postmortem.root_cause_analysis:
            lines.extend([
                "## Root Cause",
                "",
                postmortem.root_cause_analysis.summary,
                "",
            ])

            if postmortem.root_cause_analysis.five_whys:
                lines.extend([
                    "### Five Whys",
                    "",
                    "| # | Why | Answer |",
                    "| --- | --- | --- |",
                ])
                for why in postmortem.root_cause_analysis.five_whys:
                    lines.append(f"| {why.level} | {why.question} | {why.answer} |")
                lines.append("")

        if postmortem.action_items:
            lines.extend([
                "## Action Items",
                "",
            ])
            for item in postmortem.action_items:
                status_emoji = {
                    "open": "🔴",
                    "in_progress": "🟡",
                    "completed": "✅",
                }.get(item.status.value, "⚪")
                owner = item.actual_owner or item.suggested_owner or "TBD"
                lines.append(f"- {status_emoji} **{item.title}** ({owner})")
            lines.append("")

        if postmortem.lessons_learned:
            lines.extend([
                "## Lessons Learned",
                "",
            ])
            for lesson in postmortem.lessons_learned:
                lines.append(f"- **{lesson.title}:** {lesson.description}")
            lines.append("")

        return "\n".join(lines)

    def _sanitize_content(self, content: str) -> str:
        """Remove internal names and sensitive information."""
        import re

        content = re.sub(r"@\w+", "[team member]", content)

        content = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[email]", content)

        content = re.sub(r"(?i)(internal|confidential|private)", "", content)

        return content

    def _simplify_technical_content(self, content: str) -> str:
        """Simplify technical content for non-technical audiences."""
        import re

        content = re.sub(r"```[\s\S]*?```", "[technical details omitted]", content)

        content = re.sub(
            r"\b(ConnectionPoolExhausted|NullPointerException|OutOfMemoryError)\b",
            "a system error",
            content
        )

        content = re.sub(r"\.py:\d+", "", content)
        content = re.sub(r"line \d+", "", content)

        return content

    def _truncate_content(self, content: str, max_words: int) -> str:
        """Truncate content to maximum word count."""
        words = content.split()
        if len(words) <= max_words:
            return content

        truncated = " ".join(words[:max_words])

        last_period = truncated.rfind(".")
        if last_period > max_words * 0.8:
            truncated = truncated[:last_period + 1]

        truncated += "\n\n[Content truncated for length]"

        return truncated

    def get_supported_formats(self) -> list[dict[str, str]]:
        """Get list of supported export formats."""
        return [
            {"format": "markdown", "name": "Markdown", "extension": ".md"},
            {"format": "json", "name": "JSON", "extension": ".json"},
            {"format": "html", "name": "HTML", "extension": ".html"},
            {"format": "confluence", "name": "Confluence Wiki", "extension": ".wiki"},
            {"format": "notion", "name": "Notion", "extension": ".md"},
        ]

    def get_audience_descriptions(self) -> dict[str, str]:
        """Get descriptions of audience types."""
        return {
            "engineering": "Full technical detail with code snippets and logs",
            "leadership": "Summary with business impact and top action items",
            "customer": "Sanitized external communication",
            "compliance": "Formal format with evidence chain",
        }


_exporter: PostmortemExporter | None = None


def get_postmortem_exporter() -> PostmortemExporter:
    """Get the default postmortem exporter."""
    global _exporter
    if _exporter is None:
        _exporter = PostmortemExporter()
    return _exporter
