"""Tests for Status Page Widgets module."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from app.status_page.widgets import (
    WidgetTheme,
    WidgetSize,
    WidgetConfig,
    WidgetData,
    WidgetGenerator,
    get_widget_generator,
)


class TestWidgetEnums:
    """Tests for widget enumerations."""

    def test_widget_theme_values(self):
        """Test WidgetTheme enum values."""
        assert WidgetTheme.LIGHT.value == "light"
        assert WidgetTheme.DARK.value == "dark"
        assert WidgetTheme.AUTO.value == "auto"
        assert WidgetTheme.CUSTOM.value == "custom"

    def test_widget_size_values(self):
        """Test WidgetSize enum values."""
        assert WidgetSize.COMPACT.value == "compact"
        assert WidgetSize.STANDARD.value == "standard"
        assert WidgetSize.EXPANDED.value == "expanded"


class TestWidgetConfig:
    """Tests for WidgetConfig dataclass."""

    @pytest.fixture
    def sample_config(self):
        """Create a sample widget config."""
        return WidgetConfig(
            widget_id="widget_001",
            customer_id="customer_001",
            components=["api", "database", "cache"],
            theme=WidgetTheme.DARK,
            size=WidgetSize.EXPANDED,
            show_uptime=True,
            show_incidents=True,
            custom_css=".widget { border: 1px solid red; }",
            custom_colors={"operational": "#00ff00"},
            embed_token="abc123xyz",
        )

    def test_config_creation_minimal(self):
        """Test creating config with minimal fields."""
        config = WidgetConfig(
            widget_id="widget_001",
            customer_id="customer_001",
            components=["api"],
        )

        assert config.widget_id == "widget_001"
        assert config.customer_id == "customer_001"
        assert config.components == ["api"]
        assert config.theme == WidgetTheme.AUTO
        assert config.size == WidgetSize.STANDARD
        assert config.show_uptime is True
        assert config.show_incidents is True
        assert config.custom_css is None
        assert config.custom_colors == {}
        assert config.embed_token == ""

    def test_config_creation_full(self, sample_config):
        """Test creating config with all fields."""
        assert sample_config.widget_id == "widget_001"
        assert sample_config.theme == WidgetTheme.DARK
        assert sample_config.size == WidgetSize.EXPANDED
        assert sample_config.custom_css is not None
        assert "operational" in sample_config.custom_colors

    def test_config_to_dict(self, sample_config):
        """Test converting config to dictionary."""
        result = sample_config.to_dict()

        assert result["widget_id"] == "widget_001"
        assert result["components"] == ["api", "database", "cache"]
        assert result["theme"] == "dark"
        assert result["size"] == "expanded"
        assert result["show_uptime"] is True
        assert result["show_incidents"] is True
        assert result["embed_token"] == "abc123xyz"

    def test_config_created_at_default(self):
        """Test that created_at is set by default."""
        config = WidgetConfig(
            widget_id="widget_001",
            customer_id="customer_001",
            components=["api"],
        )

        assert config.created_at is not None
        assert isinstance(config.created_at, datetime)


class TestWidgetData:
    """Tests for WidgetData dataclass."""

    @pytest.fixture
    def sample_data(self):
        """Create sample widget data."""
        return WidgetData(
            components=[
                {"id": "api", "status": "operational"},
                {"id": "database", "status": "degraded"},
            ],
            overall_status="degraded",
            has_incident=True,
            incident_title="Database latency issues",
            uptime_30d=99.95,
            updated_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        )

    def test_data_creation(self, sample_data):
        """Test creating widget data."""
        assert len(sample_data.components) == 2
        assert sample_data.overall_status == "degraded"
        assert sample_data.has_incident is True
        assert sample_data.incident_title == "Database latency issues"
        assert sample_data.uptime_30d == 99.95

    def test_data_to_dict(self, sample_data):
        """Test converting data to dictionary."""
        result = sample_data.to_dict()

        assert len(result["components"]) == 2
        assert result["overall_status"] == "degraded"
        assert result["has_incident"] is True
        assert result["incident_title"] == "Database latency issues"
        assert result["uptime_30d"] == 99.95
        assert result["updated_at"] == "2024-01-15T10:30:00+00:00"

    def test_data_without_incident(self):
        """Test widget data without active incident."""
        data = WidgetData(
            components=[{"id": "api", "status": "operational"}],
            overall_status="operational",
            has_incident=False,
            incident_title=None,
            uptime_30d=99.99,
            updated_at=datetime.now(timezone.utc),
        )

        result = data.to_dict()

        assert result["has_incident"] is False
        assert result["incident_title"] is None

    def test_data_without_uptime(self):
        """Test widget data without uptime info."""
        data = WidgetData(
            components=[{"id": "api", "status": "operational"}],
            overall_status="operational",
            has_incident=False,
            incident_title=None,
            uptime_30d=None,
            updated_at=datetime.now(timezone.utc),
        )

        assert data.uptime_30d is None


class TestWidgetGenerator:
    """Tests for WidgetGenerator class."""

    @pytest.fixture
    def generator(self):
        """Create a generator instance with mocked settings."""
        with patch("app.status_page.widgets._settings") as mock_settings:
            mock_settings.status_page_url = "https://status.example.com"
            mock_settings.backend_url = "http://localhost:8000"
            return WidgetGenerator(base_url="https://status.example.com")

    def test_generator_creation_with_base_url(self):
        """Test generator creation with explicit base_url."""
        with patch("app.status_page.widgets._settings") as mock_settings:
            mock_settings.status_page_url = ""
            mock_settings.backend_url = "http://localhost:8000"
            generator = WidgetGenerator(base_url="https://custom.example.com")

            assert generator.base_url == "https://custom.example.com"

    def test_generator_creation_with_settings(self):
        """Test generator creation using settings."""
        with patch("app.status_page.widgets._settings") as mock_settings:
            mock_settings.status_page_url = "https://status.example.com"
            mock_settings.backend_url = "http://localhost:8000"
            generator = WidgetGenerator()

            assert generator.base_url == "https://status.example.com"

    def test_create_widget(self, generator):
        """Test creating a new widget."""
        config = generator.create_widget(
            customer_id="customer_001",
            components=["api", "database"],
            theme=WidgetTheme.DARK,
            size=WidgetSize.COMPACT,
            show_uptime=True,
            show_incidents=False,
        )

        assert config.widget_id.startswith("widget_")
        assert config.customer_id == "customer_001"
        assert config.components == ["api", "database"]
        assert config.theme == WidgetTheme.DARK
        assert config.size == WidgetSize.COMPACT
        assert config.show_uptime is True
        assert config.show_incidents is False
        assert len(config.embed_token) > 0

    def test_create_widget_increments_counter(self, generator):
        """Test that widget IDs are unique."""
        config1 = generator.create_widget("customer_001", ["api"])
        config2 = generator.create_widget("customer_001", ["api"])

        assert config1.widget_id != config2.widget_id

    def test_get_widget(self, generator):
        """Test retrieving a widget."""
        config = generator.create_widget("customer_001", ["api"])

        retrieved = generator.get_widget(config.widget_id)

        assert retrieved is not None
        assert retrieved.widget_id == config.widget_id

    def test_get_widget_not_found(self, generator):
        """Test retrieving non-existent widget."""
        result = generator.get_widget("nonexistent")
        assert result is None

    def test_validate_token_valid(self, generator):
        """Test validating correct token."""
        config = generator.create_widget("customer_001", ["api"])

        result = generator.validate_token(config.widget_id, config.embed_token)

        assert result is True

    def test_validate_token_invalid(self, generator):
        """Test validating incorrect token."""
        config = generator.create_widget("customer_001", ["api"])

        result = generator.validate_token(config.widget_id, "wrong_token")

        assert result is False

    def test_validate_token_widget_not_found(self, generator):
        """Test validating token for non-existent widget."""
        result = generator.validate_token("nonexistent", "any_token")
        assert result is False

    def test_get_embed_code_script(self, generator):
        """Test getting script embed code."""
        config = generator.create_widget("customer_001", ["api", "database"])

        code = generator.get_embed_code(config.widget_id, format="script")

        assert "<script" in code
        assert config.widget_id in code
        assert config.embed_token in code
        assert "api,database" in code
        assert generator.base_url in code

    def test_get_embed_code_iframe(self, generator):
        """Test getting iframe embed code."""
        config = generator.create_widget("customer_001", ["api"])

        code = generator.get_embed_code(config.widget_id, format="iframe")

        assert "<iframe" in code
        assert config.widget_id in code
        assert config.embed_token in code
        assert generator.base_url in code

    def test_get_embed_code_react(self, generator):
        """Test getting React embed code."""
        config = generator.create_widget("customer_001", ["api", "database"])

        code = generator.get_embed_code(config.widget_id, format="react")

        assert "import" in code
        assert "StatusWidget" in code
        assert config.widget_id in code
        assert config.embed_token in code
        assert "api" in code
        assert "database" in code

    def test_get_embed_code_invalid_format(self, generator):
        """Test getting embed code with invalid format."""
        config = generator.create_widget("customer_001", ["api"])

        with pytest.raises(ValueError, match="Unknown format"):
            generator.get_embed_code(config.widget_id, format="invalid")

    def test_get_embed_code_widget_not_found(self, generator):
        """Test getting embed code for non-existent widget."""
        with pytest.raises(ValueError, match="Widget not found"):
            generator.get_embed_code("nonexistent")

    def test_generate_widget_data_operational(self, generator):
        """Test generating widget data when all operational."""
        config = generator.create_widget("customer_001", ["api", "database"])

        component_statuses = {
            "api": "operational",
            "database": "operational",
        }
        uptimes = {"api": 99.99, "database": 99.95}

        data = generator.generate_widget_data(
            config.widget_id,
            component_statuses,
            uptimes=uptimes,
        )

        assert data.overall_status == "operational"
        assert data.has_incident is False
        assert len(data.components) == 2
        assert data.uptime_30d is not None

    def test_generate_widget_data_degraded(self, generator):
        """Test generating widget data with degraded status."""
        config = generator.create_widget("customer_001", ["api", "database"])

        component_statuses = {
            "api": "operational",
            "database": "degraded",
        }

        data = generator.generate_widget_data(config.widget_id, component_statuses)

        assert data.overall_status == "degraded"

    def test_generate_widget_data_worst_status(self, generator):
        """Test that worst status is used for overall."""
        config = generator.create_widget("customer_001", ["api", "database", "cache"])

        component_statuses = {
            "api": "operational",
            "database": "degraded",
            "cache": "major_outage",
        }

        data = generator.generate_widget_data(config.widget_id, component_statuses)

        assert data.overall_status == "major_outage"

    def test_generate_widget_data_with_incident(self, generator):
        """Test generating widget data with active incident."""
        config = generator.create_widget("customer_001", ["api"])

        incident = {
            "title": "API degradation",
            "severity": "minor",
        }

        data = generator.generate_widget_data(
            config.widget_id,
            {"api": "degraded"},
            active_incident=incident,
        )

        assert data.has_incident is True
        assert data.incident_title == "API degradation"

    def test_generate_widget_data_average_uptime(self, generator):
        """Test calculating average uptime."""
        config = generator.create_widget("customer_001", ["api", "database"])

        uptimes = {"api": 100.0, "database": 98.0}

        data = generator.generate_widget_data(
            config.widget_id,
            {"api": "operational", "database": "operational"},
            uptimes=uptimes,
        )

        assert data.uptime_30d == 99.0

    def test_generate_widget_data_no_uptime_when_disabled(self, generator):
        """Test that uptime is not calculated when disabled."""
        config = generator.create_widget(
            "customer_001",
            ["api"],
            show_uptime=False,
        )

        uptimes = {"api": 99.99}

        data = generator.generate_widget_data(
            config.widget_id,
            {"api": "operational"},
            uptimes=uptimes,
        )

        assert data.uptime_30d is None

    def test_generate_widget_data_widget_not_found(self, generator):
        """Test generating data for non-existent widget."""
        with pytest.raises(ValueError, match="Widget not found"):
            generator.generate_widget_data("nonexistent", {})

    def test_generate_widget_data_missing_component_status(self, generator):
        """Test generating data with missing component status defaults to operational."""
        config = generator.create_widget("customer_001", ["api", "database"])

        component_statuses = {"api": "operational"}

        data = generator.generate_widget_data(config.widget_id, component_statuses)

        component_statuses_map = {c["id"]: c["status"] for c in data.components}
        assert component_statuses_map["database"] == "operational"

    def test_render_widget_html_light_theme(self, generator):
        """Test rendering widget HTML with light theme."""
        config = generator.create_widget(
            "customer_001",
            ["api"],
            theme=WidgetTheme.LIGHT,
        )

        data = WidgetData(
            components=[{"id": "api", "status": "operational"}],
            overall_status="operational",
            has_incident=False,
            incident_title=None,
            uptime_30d=99.99,
            updated_at=datetime.now(timezone.utc),
        )

        html = generator.render_widget_html(config.widget_id, data)

        assert "<!DOCTYPE html>" in html
        assert "#ffffff" in html
        assert "#111827" in html
        assert "Operational" in html

    def test_render_widget_html_dark_theme(self, generator):
        """Test rendering widget HTML with dark theme."""
        config = generator.create_widget(
            "customer_001",
            ["api"],
            theme=WidgetTheme.DARK,
        )

        data = WidgetData(
            components=[{"id": "api", "status": "operational"}],
            overall_status="operational",
            has_incident=False,
            incident_title=None,
            uptime_30d=None,
            updated_at=datetime.now(timezone.utc),
        )

        html = generator.render_widget_html(config.widget_id, data)

        assert "#1f2937" in html
        assert "#f9fafb" in html

    def test_render_widget_html_with_uptime(self, generator):
        """Test rendering widget HTML with uptime display."""
        config = generator.create_widget("customer_001", ["api"])

        data = WidgetData(
            components=[{"id": "api", "status": "operational"}],
            overall_status="operational",
            has_incident=False,
            incident_title=None,
            uptime_30d=99.95,
            updated_at=datetime.now(timezone.utc),
        )

        html = generator.render_widget_html(config.widget_id, data)

        assert "30-day uptime" in html
        assert "99.95%" in html

    def test_render_widget_html_with_incident(self, generator):
        """Test rendering widget HTML with active incident."""
        config = generator.create_widget("customer_001", ["api"])

        data = WidgetData(
            components=[{"id": "api", "status": "degraded"}],
            overall_status="degraded",
            has_incident=True,
            incident_title="API performance issues",
            uptime_30d=None,
            updated_at=datetime.now(timezone.utc),
        )

        html = generator.render_widget_html(config.widget_id, data)

        assert "API performance issues" in html

    def test_render_widget_html_status_colors(self, generator):
        """Test that correct status colors are used."""
        config = generator.create_widget("customer_001", ["api"])

        statuses_and_colors = [
            ("operational", "#22c55e"),
            ("degraded", "#eab308"),
            ("partial_outage", "#f97316"),
            ("major_outage", "#ef4444"),
            ("maintenance", "#3b82f6"),
        ]

        for status, expected_color in statuses_and_colors:
            data = WidgetData(
                components=[{"id": "api", "status": status}],
                overall_status=status,
                has_incident=False,
                incident_title=None,
                uptime_30d=None,
                updated_at=datetime.now(timezone.utc),
            )

            html = generator.render_widget_html(config.widget_id, data)
            assert expected_color in html

    def test_render_widget_html_widget_not_found(self, generator):
        """Test rendering HTML for non-existent widget."""
        data = WidgetData(
            components=[],
            overall_status="operational",
            has_incident=False,
            incident_title=None,
            uptime_30d=None,
            updated_at=datetime.now(timezone.utc),
        )

        with pytest.raises(ValueError, match="Widget not found"):
            generator.render_widget_html("nonexistent", data)

    def test_get_widget_js(self, generator):
        """Test getting widget JavaScript code."""
        js = generator.get_widget_js()

        assert "function()" in js
        assert "fetchStatus" in js
        assert "renderWidget" in js
        assert generator.base_url in js
        assert "setInterval" in js
        assert "60000" in js

    def test_list_customer_widgets(self, generator):
        """Test listing widgets for a customer."""
        generator.create_widget("customer_001", ["api"])
        generator.create_widget("customer_001", ["database"])
        generator.create_widget("customer_002", ["cache"])

        customer1_widgets = generator.list_customer_widgets("customer_001")
        customer2_widgets = generator.list_customer_widgets("customer_002")

        assert len(customer1_widgets) == 2
        assert len(customer2_widgets) == 1
        assert all(w.customer_id == "customer_001" for w in customer1_widgets)

    def test_list_customer_widgets_empty(self, generator):
        """Test listing widgets for customer with no widgets."""
        widgets = generator.list_customer_widgets("nonexistent")
        assert widgets == []

    def test_delete_widget(self, generator):
        """Test deleting a widget."""
        config = generator.create_widget("customer_001", ["api"])
        widget_id = config.widget_id

        result = generator.delete_widget(widget_id)

        assert result is True
        assert generator.get_widget(widget_id) is None

    def test_delete_widget_not_found(self, generator):
        """Test deleting non-existent widget."""
        result = generator.delete_widget("nonexistent")
        assert result is False


class TestGetWidgetGenerator:
    """Tests for get_widget_generator function."""

    def test_get_widget_generator_singleton(self):
        """Test that get_widget_generator returns singleton."""
        with patch("app.status_page.widgets._generator", None):
            with patch("app.status_page.widgets._settings") as mock_settings:
                mock_settings.status_page_url = "https://status.example.com"
                mock_settings.backend_url = "http://localhost:8000"

                import app.status_page.widgets as widgets_module

                widgets_module._generator = None

                gen1 = get_widget_generator()
                gen2 = get_widget_generator()

                assert gen1 is gen2


class TestWidgetDataIntegration:
    """Integration tests for widget data flow."""

    @pytest.fixture
    def generator(self):
        """Create a generator with mocked settings."""
        with patch("app.status_page.widgets._settings") as mock_settings:
            mock_settings.status_page_url = "https://status.example.com"
            mock_settings.backend_url = "http://localhost:8000"
            return WidgetGenerator(base_url="https://status.example.com")

    def test_full_widget_lifecycle(self, generator):
        """Test complete widget lifecycle."""
        config = generator.create_widget(
            customer_id="customer_001",
            components=["api", "database", "cache"],
            theme=WidgetTheme.DARK,
            size=WidgetSize.STANDARD,
        )
        assert config.widget_id is not None

        retrieved = generator.get_widget(config.widget_id)
        assert retrieved is not None
        assert retrieved.customer_id == "customer_001"

        assert generator.validate_token(config.widget_id, config.embed_token) is True

        code = generator.get_embed_code(config.widget_id, format="script")
        assert config.widget_id in code

        data = generator.generate_widget_data(
            config.widget_id,
            {"api": "operational", "database": "degraded", "cache": "operational"},
            active_incident={"title": "DB issues"},
            uptimes={"api": 99.99, "database": 99.50, "cache": 99.95},
        )
        assert data.overall_status == "degraded"
        assert data.has_incident is True

        html = generator.render_widget_html(config.widget_id, data)
        assert "DB issues" in html

        assert generator.delete_widget(config.widget_id) is True
        assert generator.get_widget(config.widget_id) is None

    def test_multiple_customers_isolation(self, generator):
        """Test that customer widgets are isolated."""
        c1_widget1 = generator.create_widget("customer_001", ["api"])
        c1_widget2 = generator.create_widget("customer_001", ["database"])
        c2_widget1 = generator.create_widget("customer_002", ["api", "database"])

        c1_widgets = generator.list_customer_widgets("customer_001")
        c2_widgets = generator.list_customer_widgets("customer_002")

        assert len(c1_widgets) == 2
        assert len(c2_widgets) == 1
        assert c1_widget1 in c1_widgets
        assert c1_widget2 in c1_widgets
        assert c2_widget1 in c2_widgets
        assert c2_widget1 not in c1_widgets
