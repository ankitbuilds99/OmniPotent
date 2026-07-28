"""Code-to-Incident Linking.

Links code locations to incidents for traceability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("devcopilot.vscode_extension.code_linking")


@dataclass
class CodeIncidentLink:
    """A link between code and an incident."""

    link_id: str
    file_path: str
    line_start: int
    line_end: int
    incident_id: str
    incident_title: str
    relationship: str
    confidence: float
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.link_id,
            "file": self.file_path,
            "lines": {"start": self.line_start, "end": self.line_end},
            "incident_id": self.incident_id,
            "incident_title": self.incident_title,
            "relationship": self.relationship,
            "confidence": round(self.confidence, 2),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CodeRiskProfile:
    """Risk profile for a code location."""

    file_path: str
    total_incidents: int
    active_incidents: int
    avg_severity: float
    last_incident_at: datetime | None
    common_failure_modes: list[str]
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "total_incidents": self.total_incidents,
            "active_incidents": self.active_incidents,
            "avg_severity": round(self.avg_severity, 1),
            "last_incident": self.last_incident_at.isoformat() if self.last_incident_at else None,
            "failure_modes": self.common_failure_modes,
            "risk_level": self.risk_level,
        }


class CodeLinkingService:
    """Service for linking code to incidents."""

    def __init__(self) -> None:
        self._links: dict[str, list[CodeIncidentLink]] = {}
        self._incidents: dict[str, dict[str, Any]] = {}

    def create_link(
        self,
        file_path: str,
        line_start: int,
        line_end: int,
        incident_id: str,
        incident_title: str,
        relationship: str = "caused_by",
        confidence: float = 0.9,
    ) -> CodeIncidentLink:
        """Create a link between code and an incident."""
        link = CodeIncidentLink(
            link_id=f"link_{datetime.now(timezone.utc).timestamp()}",
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            incident_id=incident_id,
            incident_title=incident_title,
            relationship=relationship,
            confidence=confidence,
        )

        if file_path not in self._links:
            self._links[file_path] = []
        self._links[file_path].append(link)

        log.info(
            "Created link: file=%s lines=%d-%d incident=%s",
            file_path,
            line_start,
            line_end,
            incident_id,
        )

        return link

    def get_links_for_file(self, file_path: str) -> list[CodeIncidentLink]:
        """Get all links for a file."""
        return self._links.get(file_path, [])

    def get_links_for_line(
        self,
        file_path: str,
        line_number: int,
    ) -> list[CodeIncidentLink]:
        """Get links for a specific line."""
        links = self._links.get(file_path, [])
        return [
            link for link in links
            if link.line_start <= line_number <= link.line_end
        ]

    def get_incidents_for_error(
        self,
        error_message: str,
        stack_trace: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Find incidents that match an error."""
        results: list[dict[str, Any]] = []

        for frame in stack_trace:
            file_path = frame.get("file", "")
            line_number = frame.get("line", 0)

            links = self.get_links_for_line(file_path, line_number)
            for link in links:
                results.append({
                    "incident_id": link.incident_id,
                    "incident_title": link.incident_title,
                    "file": file_path,
                    "line": line_number,
                    "confidence": link.confidence,
                    "relationship": link.relationship,
                })

        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results[:5]

    def get_risk_profile(self, file_path: str) -> CodeRiskProfile:
        """Get risk profile for a file."""
        links = self._links.get(file_path, [])

        if not links:
            return CodeRiskProfile(
                file_path=file_path,
                total_incidents=0,
                active_incidents=0,
                avg_severity=0,
                last_incident_at=None,
                common_failure_modes=[],
                risk_level="LOW",
            )

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        active = sum(1 for link in links if link.created_at >= cutoff)

        last_incident = max(links, key=lambda lnk: lnk.created_at)

        failure_modes: dict[str, int] = {}
        for link in links:
            mode = link.relationship
            failure_modes[mode] = failure_modes.get(mode, 0) + 1

        common_modes = sorted(
            failure_modes.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:3]

        total = len(links)
        if total >= 5 or active >= 2:
            risk_level = "HIGH"
        elif total >= 2 or active >= 1:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        return CodeRiskProfile(
            file_path=file_path,
            total_incidents=total,
            active_incidents=active,
            avg_severity=2.5,
            last_incident_at=last_incident.created_at,
            common_failure_modes=[m[0] for m in common_modes],
            risk_level=risk_level,
        )

    def search_incidents(
        self,
        query: str,
        file_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for incidents matching a query."""
        results: list[dict[str, Any]] = []

        for file_path, links in self._links.items():
            if file_filter and file_filter not in file_path:
                continue

            for link in links:
                if (
                    query.lower() in link.incident_title.lower()
                    or query.lower() in link.relationship.lower()
                ):
                    results.append({
                        "incident_id": link.incident_id,
                        "incident_title": link.incident_title,
                        "file": file_path,
                        "lines": f"{link.line_start}-{link.line_end}",
                        "relationship": link.relationship,
                    })

        return results[:20]

    def get_related_files(self, incident_id: str) -> list[str]:
        """Get all files related to an incident."""
        files = []
        for file_path, links in self._links.items():
            for link in links:
                if link.incident_id == incident_id:
                    files.append(file_path)
                    break
        return files


_service: CodeLinkingService | None = None


def get_code_linking_service() -> CodeLinkingService:
    """Get the default code linking service."""
    global _service
    if _service is None:
        _service = CodeLinkingService()
    return _service
