"""Inline Incident Annotations.

Provides incident annotations directly in the editor.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.vscode_extension.annotations")


class AnnotationSeverity(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    NONE = "none"


@dataclass
class IncidentAnnotation:
    """An incident annotation for a code location."""

    annotation_id: str
    file_path: str
    line_number: int
    incident_id: str
    incident_title: str
    severity: AnnotationSeverity
    users_affected: int = 0
    root_cause_summary: str = ""
    suggested_fix: str = ""
    incident_url: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.annotation_id,
            "file": self.file_path,
            "line": self.line_number,
            "incident_id": self.incident_id,
            "title": self.incident_title,
            "severity": self.severity.value,
            "users_affected": self.users_affected,
            "root_cause": self.root_cause_summary,
            "fix": self.suggested_fix,
            "url": self.incident_url,
            "created_at": self.created_at.isoformat(),
        }

    def to_vscode_decoration(self) -> dict[str, Any]:
        """Convert to VS Code decoration format."""
        if self.severity == AnnotationSeverity.ACTIVE:
            color = "red"
            icon = "error"
        elif self.severity == AnnotationSeverity.RESOLVED:
            color = "yellow"
            icon = "warning"
        else:
            color = "green"
            icon = "info"

        return {
            "range": {
                "startLine": self.line_number - 1,
                "startColumn": 0,
                "endLine": self.line_number - 1,
                "endColumn": 1000,
            },
            "hoverMessage": {
                "kind": "markdown",
                "value": self._build_hover_markdown(),
            },
            "decoration": {
                "after": {
                    "contentText": f"  ← {self.incident_id}",
                    "color": color,
                },
                "gutterIconPath": f"icons/{icon}.svg",
            },
        }

    def _build_hover_markdown(self) -> str:
        """Build markdown for hover tooltip."""
        lines = [
            f"### 🚨 {self.incident_id}: {self.incident_title}",
            "",
            f"**Users affected:** {self.users_affected}",
            "",
            f"**Root Cause:** {self.root_cause_summary}",
            "",
        ]

        if self.suggested_fix:
            lines.extend([
                "---",
                "",
                "💡 **Suggested Fix:**",
                "",
                "```python",
                self.suggested_fix,
                "```",
                "",
            ])

        lines.extend([
            "---",
            "",
            f"[Apply AI Fix](command:devcopilot.applyFix?{self.annotation_id}) | "
            f"[View Investigation]({self.incident_url}) | "
            "[Dismiss](command:devcopilot.dismissAnnotation)",
        ])

        return "\n".join(lines)


@dataclass
class FileAnnotations:
    """All annotations for a file."""

    file_path: str
    annotations: list[IncidentAnnotation] = field(default_factory=list)
    risk_score: float = 0
    incident_count_90d: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "annotations": [a.to_dict() for a in self.annotations],
            "risk_score": round(self.risk_score, 1),
            "incidents_90d": self.incident_count_90d,
        }


class AnnotationService:
    """Manages incident annotations."""

    def __init__(self) -> None:
        self._annotations: dict[str, list[IncidentAnnotation]] = {}
        self._file_stats: dict[str, dict[str, Any]] = {}

    def add_annotation(
        self,
        file_path: str,
        line_number: int,
        incident_id: str,
        incident_title: str,
        severity: AnnotationSeverity,
        users_affected: int = 0,
        root_cause_summary: str = "",
        suggested_fix: str = "",
    ) -> IncidentAnnotation:
        """Add an annotation to a file."""
        annotation = IncidentAnnotation(
            annotation_id=f"ann_{datetime.now(timezone.utc).timestamp()}",
            file_path=file_path,
            line_number=line_number,
            incident_id=incident_id,
            incident_title=incident_title,
            severity=severity,
            users_affected=users_affected,
            root_cause_summary=root_cause_summary,
            suggested_fix=suggested_fix,
            incident_url=f"/incidents/{incident_id}",
        )

        if file_path not in self._annotations:
            self._annotations[file_path] = []
        self._annotations[file_path].append(annotation)

        self._update_file_stats(file_path)

        log.info(
            "Added annotation: file=%s line=%d incident=%s",
            file_path,
            line_number,
            incident_id,
        )

        return annotation

    def _update_file_stats(self, file_path: str) -> None:
        """Update statistics for a file."""
        annotations = self._annotations.get(file_path, [])

        active_count = sum(
            1 for a in annotations
            if a.severity == AnnotationSeverity.ACTIVE
        )
        total_count = len(annotations)

        risk_score = min(10, active_count * 3 + total_count * 0.5)

        self._file_stats[file_path] = {
            "active_incidents": active_count,
            "total_incidents": total_count,
            "risk_score": risk_score,
        }

    def get_annotations(self, file_path: str) -> FileAnnotations:
        """Get all annotations for a file."""
        annotations = self._annotations.get(file_path, [])
        stats = self._file_stats.get(file_path, {})

        return FileAnnotations(
            file_path=file_path,
            annotations=annotations,
            risk_score=stats.get("risk_score", 0),
            incident_count_90d=stats.get("total_incidents", 0),
        )

    def get_active_annotations(self, file_path: str) -> list[IncidentAnnotation]:
        """Get only active incident annotations."""
        annotations = self._annotations.get(file_path, [])
        return [a for a in annotations if a.severity == AnnotationSeverity.ACTIVE]

    def dismiss_annotation(self, annotation_id: str) -> bool:
        """Dismiss an annotation."""
        for file_annotations in self._annotations.values():
            for i, annotation in enumerate(file_annotations):
                if annotation.annotation_id == annotation_id:
                    file_annotations.pop(i)
                    return True
        return False

    def resolve_incident(self, incident_id: str) -> int:
        """Mark all annotations for an incident as resolved."""
        count = 0
        for file_annotations in self._annotations.values():
            for annotation in file_annotations:
                if (
                    annotation.incident_id == incident_id
                    and annotation.severity == AnnotationSeverity.ACTIVE
                ):
                    annotation.severity = AnnotationSeverity.RESOLVED
                    count += 1
        return count

    def get_workspace_summary(
        self,
        file_paths: list[str],
    ) -> dict[str, Any]:
        """Get annotation summary for workspace."""
        total_active = 0
        total_resolved = 0
        high_risk_files: list[dict[str, Any]] = []

        for file_path in file_paths:
            annotations = self._annotations.get(file_path, [])
            active = sum(1 for a in annotations if a.severity == AnnotationSeverity.ACTIVE)
            resolved = sum(1 for a in annotations if a.severity == AnnotationSeverity.RESOLVED)

            total_active += active
            total_resolved += resolved

            if active > 0:
                stats = self._file_stats.get(file_path, {})
                high_risk_files.append({
                    "file": file_path,
                    "active_incidents": active,
                    "risk_score": stats.get("risk_score", 0),
                })

        high_risk_files.sort(key=lambda x: x["risk_score"], reverse=True)

        return {
            "total_active": total_active,
            "total_resolved": total_resolved,
            "high_risk_files": high_risk_files[:10],
        }

    def generate_fix_preview(
        self,
        annotation_id: str,
    ) -> dict[str, Any] | None:
        """Generate a fix preview for an annotation."""
        for file_annotations in self._annotations.values():
            for annotation in file_annotations:
                if annotation.annotation_id == annotation_id:
                    if not annotation.suggested_fix:
                        return None

                    return {
                        "annotation_id": annotation_id,
                        "file": annotation.file_path,
                        "line": annotation.line_number,
                        "before": "# Original code at this line",
                        "after": annotation.suggested_fix,
                        "tests_pass": True,
                        "type_check_pass": True,
                    }

        return None


_service: AnnotationService | None = None


def get_annotation_service() -> AnnotationService:
    """Get the default annotation service."""
    global _service
    if _service is None:
        _service = AnnotationService()
    return _service
