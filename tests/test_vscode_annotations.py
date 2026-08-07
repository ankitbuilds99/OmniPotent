"""Tests for VS Code extension annotations module."""

from datetime import datetime, timezone

from app.vscode_extension.annotations import (
    AnnotationSeverity,
    IncidentAnnotation,
)


class TestAnnotationSeverity:
    """Tests for AnnotationSeverity enum."""

    def test_severity_values(self):
        """All severity values should be correct."""
        assert AnnotationSeverity.ACTIVE.value == "active"
        assert AnnotationSeverity.RESOLVED.value == "resolved"
        assert AnnotationSeverity.NONE.value == "none"

    def test_severity_from_string(self):
        """Severities should be creatable from strings."""
        assert AnnotationSeverity("active") == AnnotationSeverity.ACTIVE
        assert AnnotationSeverity("resolved") == AnnotationSeverity.RESOLVED
        assert AnnotationSeverity("none") == AnnotationSeverity.NONE


class TestIncidentAnnotation:
    """Tests for IncidentAnnotation dataclass."""

    def test_annotation_creation(self):
        """Annotation should be created with required fields."""
        annotation = IncidentAnnotation(
            annotation_id="ann-123",
            file_path="src/api/handler.py",
            line_number=42,
            incident_id="inc-456",
            incident_title="High latency in API",
            severity=AnnotationSeverity.ACTIVE,
        )
        assert annotation.annotation_id == "ann-123"
        assert annotation.file_path == "src/api/handler.py"
        assert annotation.line_number == 42
        assert annotation.incident_id == "inc-456"
        assert annotation.incident_title == "High latency in API"
        assert annotation.severity == AnnotationSeverity.ACTIVE
        assert annotation.users_affected == 0
        assert annotation.root_cause_summary == ""
        assert annotation.suggested_fix == ""
        assert annotation.incident_url == ""
        assert isinstance(annotation.created_at, datetime)

    def test_annotation_with_all_fields(self):
        """Annotation should accept all optional fields."""
        created = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        annotation = IncidentAnnotation(
            annotation_id="ann-789",
            file_path="src/db/connection.py",
            line_number=100,
            incident_id="inc-999",
            incident_title="Database connection pool exhausted",
            severity=AnnotationSeverity.RESOLVED,
            users_affected=1500,
            root_cause_summary="Connection pool size too small",
            suggested_fix="Increase pool size from 10 to 50",
            incident_url="https://incidents.example.com/inc-999",
            created_at=created,
        )
        assert annotation.users_affected == 1500
        assert annotation.root_cause_summary == "Connection pool size too small"
        assert annotation.suggested_fix == "Increase pool size from 10 to 50"
        assert annotation.incident_url == "https://incidents.example.com/inc-999"
        assert annotation.created_at == created

    def test_annotation_to_dict(self):
        """Annotation should convert to dict correctly."""
        annotation = IncidentAnnotation(
            annotation_id="ann-123",
            file_path="src/handler.py",
            line_number=42,
            incident_id="inc-456",
            incident_title="Test Incident",
            severity=AnnotationSeverity.ACTIVE,
            users_affected=100,
            root_cause_summary="Root cause here",
            suggested_fix="Fix here",
            incident_url="https://example.com/inc",
        )
        data = annotation.to_dict()

        assert data["id"] == "ann-123"
        assert data["file"] == "src/handler.py"
        assert data["line"] == 42
        assert data["incident_id"] == "inc-456"
        assert data["title"] == "Test Incident"
        assert data["severity"] == "active"
        assert data["users_affected"] == 100
        assert data["root_cause"] == "Root cause here"
        assert data["fix"] == "Fix here"
        assert data["url"] == "https://example.com/inc"
        assert "created_at" in data

    def test_annotation_to_vscode_decoration_active(self):
        """Active annotation should have red decoration."""
        annotation = IncidentAnnotation(
            annotation_id="ann-1",
            file_path="test.py",
            line_number=10,
            incident_id="inc-1",
            incident_title="Active Incident",
            severity=AnnotationSeverity.ACTIVE,
        )
        decoration = annotation.to_vscode_decoration()

        assert "decoration" in decoration
        assert decoration["decoration"]["after"]["color"] == "red"
        assert "error" in decoration["decoration"]["gutterIconPath"]

    def test_annotation_to_vscode_decoration_resolved(self):
        """Resolved annotation should have yellow decoration."""
        annotation = IncidentAnnotation(
            annotation_id="ann-2",
            file_path="test.py",
            line_number=20,
            incident_id="inc-2",
            incident_title="Resolved Incident",
            severity=AnnotationSeverity.RESOLVED,
        )
        decoration = annotation.to_vscode_decoration()

        assert decoration["decoration"]["after"]["color"] == "yellow"
        assert "warning" in decoration["decoration"]["gutterIconPath"]

    def test_annotation_to_vscode_decoration_none(self):
        """None severity annotation should have green decoration."""
        annotation = IncidentAnnotation(
            annotation_id="ann-3",
            file_path="test.py",
            line_number=30,
            incident_id="inc-3",
            incident_title="No Severity Incident",
            severity=AnnotationSeverity.NONE,
        )
        decoration = annotation.to_vscode_decoration()

        assert decoration["decoration"]["after"]["color"] == "green"
        assert "info" in decoration["decoration"]["gutterIconPath"]

    def test_annotation_hover_content(self):
        """Decoration should include hover content."""
        annotation = IncidentAnnotation(
            annotation_id="ann-4",
            file_path="test.py",
            line_number=40,
            incident_id="inc-4",
            incident_title="Test Incident Title",
            severity=AnnotationSeverity.ACTIVE,
            root_cause_summary="This is the root cause",
            suggested_fix="Apply this fix",
        )
        decoration = annotation.to_vscode_decoration()

        assert "hoverMessage" in decoration
        hover = decoration["hoverMessage"]
        assert hover["kind"] == "markdown"
        assert "Test Incident Title" in hover["value"]
        assert "This is the root cause" in hover["value"]

    def test_annotation_range(self):
        """Decoration should have correct line range."""
        annotation = IncidentAnnotation(
            annotation_id="ann-5",
            file_path="test.py",
            line_number=50,
            incident_id="inc-5",
            incident_title="Test",
            severity=AnnotationSeverity.ACTIVE,
        )
        decoration = annotation.to_vscode_decoration()

        assert "range" in decoration
        # Line numbers are 0-indexed in VS Code
        assert decoration["range"]["startLine"] == 49
        assert decoration["range"]["endLine"] == 49
