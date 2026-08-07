"""Tests for VS Code extension code linking module."""

from datetime import datetime, timezone

from app.vscode_extension.code_linking import (
    CodeIncidentLink,
    CodeRiskProfile,
)


class TestCodeIncidentLink:
    """Tests for CodeIncidentLink dataclass."""

    def test_link_creation(self):
        """Link should be created with required fields."""
        link = CodeIncidentLink(
            link_id="link-123",
            file_path="src/api/handler.py",
            line_start=42,
            line_end=50,
            incident_id="inc-456",
            incident_title="API timeout",
            relationship="caused_by",
            confidence=0.85,
        )
        assert link.link_id == "link-123"
        assert link.file_path == "src/api/handler.py"
        assert link.line_start == 42
        assert link.line_end == 50
        assert link.incident_id == "inc-456"
        assert link.incident_title == "API timeout"
        assert link.relationship == "caused_by"
        assert link.confidence == 0.85
        assert isinstance(link.created_at, datetime)

    def test_link_to_dict(self):
        """Link should convert to dict correctly."""
        created = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        link = CodeIncidentLink(
            link_id="link-789",
            file_path="src/db/query.py",
            line_start=100,
            line_end=120,
            incident_id="inc-999",
            incident_title="Slow query",
            relationship="related_to",
            confidence=0.756,
            created_at=created,
        )
        data = link.to_dict()

        assert data["id"] == "link-789"
        assert data["file"] == "src/db/query.py"
        assert data["lines"] == {"start": 100, "end": 120}
        assert data["incident_id"] == "inc-999"
        assert data["incident_title"] == "Slow query"
        assert data["relationship"] == "related_to"
        assert data["confidence"] == 0.76  # Rounded to 2 decimals
        assert data["created_at"] == created.isoformat()

    def test_link_confidence_rounding(self):
        """Confidence should be rounded to 2 decimal places."""
        link = CodeIncidentLink(
            link_id="link-1",
            file_path="test.py",
            line_start=1,
            line_end=10,
            incident_id="inc-1",
            incident_title="Test",
            relationship="caused_by",
            confidence=0.33333333,
        )
        data = link.to_dict()
        assert data["confidence"] == 0.33


class TestCodeRiskProfile:
    """Tests for CodeRiskProfile dataclass."""

    def test_risk_profile_creation(self):
        """Risk profile should be created correctly."""
        last_incident = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        profile = CodeRiskProfile(
            file_path="src/critical/service.py",
            total_incidents=10,
            active_incidents=2,
            avg_severity=3.5,
            last_incident_at=last_incident,
            common_failure_modes=["timeout", "null_pointer"],
            risk_level="high",
        )
        assert profile.file_path == "src/critical/service.py"
        assert profile.total_incidents == 10
        assert profile.active_incidents == 2
        assert profile.avg_severity == 3.5
        assert profile.last_incident_at == last_incident
        assert profile.common_failure_modes == ["timeout", "null_pointer"]
        assert profile.risk_level == "high"

    def test_risk_profile_no_incidents(self):
        """Risk profile should handle no incidents."""
        profile = CodeRiskProfile(
            file_path="src/stable/module.py",
            total_incidents=0,
            active_incidents=0,
            avg_severity=0.0,
            last_incident_at=None,
            common_failure_modes=[],
            risk_level="low",
        )
        assert profile.total_incidents == 0
        assert profile.last_incident_at is None
        assert profile.common_failure_modes == []
        assert profile.risk_level == "low"

    def test_risk_profile_to_dict(self):
        """Risk profile should convert to dict correctly."""
        last_incident = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        profile = CodeRiskProfile(
            file_path="src/api/endpoint.py",
            total_incidents=5,
            active_incidents=1,
            avg_severity=2.56789,
            last_incident_at=last_incident,
            common_failure_modes=["memory_leak"],
            risk_level="medium",
        )
        data = profile.to_dict()

        assert data["file"] == "src/api/endpoint.py"
        assert data["total_incidents"] == 5
        assert data["active_incidents"] == 1
        assert data["avg_severity"] == 2.6  # Rounded to 1 decimal
        assert data["last_incident"] == last_incident.isoformat()
        assert data["failure_modes"] == ["memory_leak"]
        assert data["risk_level"] == "medium"

    def test_risk_profile_to_dict_no_last_incident(self):
        """Risk profile to_dict should handle None last_incident_at."""
        profile = CodeRiskProfile(
            file_path="test.py",
            total_incidents=0,
            active_incidents=0,
            avg_severity=0.0,
            last_incident_at=None,
            common_failure_modes=[],
            risk_level="low",
        )
        data = profile.to_dict()
        assert data["last_incident"] is None
