"""Tests for Grafana Connector."""

from datetime import datetime, timezone
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.base import ConnectorConfig, ConnectorError
from app.connectors.grafana import GrafanaConnector


@pytest.fixture
def grafana_config():
    """Create a Grafana connector config."""
    return ConnectorConfig(
        url="https://grafana.example.com",
        api_key="glsa_test_token_12345",
        timeout_seconds=30.0,
    )


@pytest.fixture
def grafana_config_no_key():
    """Create a Grafana connector config without API key."""
    return ConnectorConfig(
        url="https://grafana.example.com",
        timeout_seconds=30.0,
    )


@pytest.fixture
def grafana_config_basic_auth():
    """Create a Grafana config using the supported admin Basic Auth path."""
    return ConnectorConfig(
        url="https://grafana.example.com",
        username="admin",
        password="secret",
        timeout_seconds=30.0,
    )


@pytest.fixture
def grafana_connector(grafana_config):
    """Create a Grafana connector instance."""
    return GrafanaConnector(grafana_config)


@pytest.fixture
def grafana_connector_no_key(grafana_config_no_key):
    """Create a Grafana connector without API key."""
    return GrafanaConnector(grafana_config_no_key)


class TestGrafanaConnectorInit:
    """Tests for GrafanaConnector initialization."""

    def test_connector_type(self, grafana_connector):
        assert grafana_connector.connector_type == "grafana"

    def test_default_headers_with_api_key(self, grafana_connector):
        headers = grafana_connector._default_headers()
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer glsa_test_token_12345"

    def test_default_headers_without_api_key(self, grafana_connector_no_key):
        headers = grafana_connector_no_key._default_headers()
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers

    def test_default_headers_with_basic_auth(self, grafana_config_basic_auth):
        connector = GrafanaConnector(grafana_config_basic_auth)
        headers = connector._default_headers()
        expected = base64.b64encode(b"admin:secret").decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_config_stored(self, grafana_connector, grafana_config):
        assert grafana_connector.config == grafana_config
        assert grafana_connector.config.url == "https://grafana.example.com"


class TestGrafanaHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.health_check()

        assert result is True
        mock_get.assert_called_once_with("/api/health")

    @pytest.mark.asyncio
    async def test_health_check_failure(self, grafana_connector):
        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("Connection refused")
            result = await grafana_connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_non_200(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.health_check()

        assert result is False


class TestGrafanaSearchDashboards:
    """Tests for search_dashboards method."""

    @pytest.mark.asyncio
    async def test_search_dashboards_success(self, grafana_connector):
        dashboards_data = [
            {"uid": "abc123", "title": "API Metrics", "url": "/d/abc123/api-metrics"},
            {"uid": "def456", "title": "Database Stats", "url": "/d/def456/database-stats"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = dashboards_data

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.search_dashboards()

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_search_dashboards_with_query(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await grafana_connector.search_dashboards(query="api")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["query"] == "api"
        assert call_args[1]["params"]["type"] == "dash-db"

    @pytest.mark.asyncio
    async def test_search_dashboards_with_tag(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await grafana_connector.search_dashboards(tag="production")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["tag"] == "production"

    @pytest.mark.asyncio
    async def test_search_dashboards_error(self, grafana_connector):
        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await grafana_connector.search_dashboards()

        assert result.success is False
        assert result.data == []


class TestGrafanaGetDashboard:
    """Tests for get_dashboard method."""

    @pytest.mark.asyncio
    async def test_get_dashboard_success(self, grafana_connector):
        dashboard_data = {
            "dashboard": {
                "id": 1,
                "uid": "abc123",
                "title": "API Metrics",
                "panels": [
                    {"id": 1, "title": "Request Rate"},
                    {"id": 2, "title": "Error Rate"},
                ],
            },
            "meta": {"isStarred": True},
        }
        mock_response = MagicMock()
        mock_response.json.return_value = dashboard_data

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.get_dashboard("abc123")

        assert result.success is True
        assert result.data["dashboard"]["uid"] == "abc123"
        mock_get.assert_called_once_with("/api/dashboards/uid/abc123")

    @pytest.mark.asyncio
    async def test_get_dashboard_not_found(self, grafana_connector):
        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("grafana API error: 404", status_code=404)
            result = await grafana_connector.get_dashboard("nonexistent")

        assert result.success is False
        assert result.data == {}
        assert "404" in result.error


class TestGrafanaGetAnnotations:
    """Tests for get_annotations method."""

    @pytest.mark.asyncio
    async def test_get_annotations_success(self, grafana_connector):
        annotations_data = [
            {"id": 1, "text": "Deployment", "time": 1704067200000},
            {"id": 2, "text": "Incident", "time": 1704070800000},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = annotations_data

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.get_annotations()

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_annotations_with_dashboard(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await grafana_connector.get_annotations(dashboard_uid="abc123")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["dashboardUID"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_annotations_with_time_range(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        from_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await grafana_connector.get_annotations(from_time=from_time, to_time=to_time)

        call_args = mock_get.call_args
        # Timestamps should be in milliseconds
        assert call_args[1]["params"]["from"] == int(from_time.timestamp() * 1000)
        assert call_args[1]["params"]["to"] == int(to_time.timestamp() * 1000)

    @pytest.mark.asyncio
    async def test_get_annotations_with_tags(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await grafana_connector.get_annotations(tags=["deploy", "incident"])

        call_args = mock_get.call_args
        assert call_args[1]["params"]["tags"] == "deploy,incident"

    @pytest.mark.asyncio
    async def test_get_annotations_with_limit(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await grafana_connector.get_annotations(limit=50)

        call_args = mock_get.call_args
        assert call_args[1]["params"]["limit"] == 50

    @pytest.mark.asyncio
    async def test_get_annotations_error(self, grafana_connector):
        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await grafana_connector.get_annotations()

        assert result.success is False
        assert result.data == []


class TestGrafanaCreateAnnotation:
    """Tests for create_annotation method."""

    @pytest.mark.asyncio
    async def test_create_annotation_success(self, grafana_connector):
        annotation_response = {
            "id": 123,
            "message": "Annotation added",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = annotation_response

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await grafana_connector.create_annotation("Deployment started")

        assert result.success is True
        assert result.data["id"] == 123

    @pytest.mark.asyncio
    async def test_create_annotation_with_tags(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await grafana_connector.create_annotation("Event", tags=["deploy", "prod"])

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["tags"] == ["deploy", "prod"]

    @pytest.mark.asyncio
    async def test_create_annotation_default_tags(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await grafana_connector.create_annotation("Event")

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["tags"] == ["omnipotent"]

    @pytest.mark.asyncio
    async def test_create_annotation_with_dashboard(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await grafana_connector.create_annotation(
                "Event",
                dashboard_uid="abc123",
                panel_id=5,
            )

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["dashboardUID"] == "abc123"
        assert payload["panelId"] == 5

    @pytest.mark.asyncio
    async def test_create_annotation_with_time_range(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}
        start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end_time = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await grafana_connector.create_annotation(
                "Maintenance window",
                time_start=start_time,
                time_end=end_time,
            )

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["time"] == int(start_time.timestamp() * 1000)
        assert payload["timeEnd"] == int(end_time.timestamp() * 1000)

    @pytest.mark.asyncio
    async def test_create_annotation_error(self, grafana_connector):
        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectorError("Permission denied")
            result = await grafana_connector.create_annotation("Event")

        assert result.success is False
        assert result.data == {}


class TestGrafanaGetAlerts:
    """Tests for get_alerts method."""

    @pytest.mark.asyncio
    async def test_get_alerts_success(self, grafana_connector):
        alerts_data = [
            {"uid": "alert1", "title": "High CPU", "condition": "A"},
            {"uid": "alert2", "title": "Low Memory", "condition": "B"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = alerts_data

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.get_alerts()

        assert result.success is True
        assert len(result.data) == 2
        mock_get.assert_called_once_with("/api/v1/provisioning/alert-rules")

    @pytest.mark.asyncio
    async def test_get_alerts_with_state(self, grafana_connector):
        # Note: current implementation doesn't use the state parameter
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.get_alerts(state="alerting")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_get_alerts_error(self, grafana_connector):
        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await grafana_connector.get_alerts()

        assert result.success is False
        assert result.data == []


class TestGrafanaGetDatasources:
    """Tests for get_datasources method."""

    @pytest.mark.asyncio
    async def test_get_datasources_success(self, grafana_connector):
        datasources_data = [
            {"id": 1, "uid": "prom1", "name": "Prometheus", "type": "prometheus"},
            {"id": 2, "uid": "loki1", "name": "Loki", "type": "loki"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = datasources_data

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.get_datasources()

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2
        mock_get.assert_called_once_with("/api/datasources")

    @pytest.mark.asyncio
    async def test_get_datasources_error(self, grafana_connector):
        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await grafana_connector.get_datasources()

        assert result.success is False
        assert result.data == []


class TestGrafanaQueryDatasource:
    """Tests for query_datasource method."""

    @pytest.mark.asyncio
    async def test_query_datasource_success(self, grafana_connector):
        query_results = {
            "results": {
                "A": {
                    "frames": [
                        {"data": {"values": [[1, 2, 3], [100, 200, 300]]}}
                    ]
                }
            }
        }
        mock_response = MagicMock()
        mock_response.json.return_value = query_results

        from_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        query = {"expr": "up{job='api'}"}

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await grafana_connector.query_datasource(
                "prom1",
                query,
                from_time,
                to_time,
            )

        assert result.success is True
        assert "A" in result.data

    @pytest.mark.asyncio
    async def test_query_datasource_payload_structure(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": {}}

        from_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        query = {"expr": "up", "refId": "A"}

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await grafana_connector.query_datasource("prom1", query, from_time, to_time)

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert "queries" in payload
        assert payload["queries"][0]["datasourceId"] == "prom1"
        assert payload["queries"][0]["expr"] == "up"
        assert payload["from"] == str(int(from_time.timestamp() * 1000))
        assert payload["to"] == str(int(to_time.timestamp() * 1000))

    @pytest.mark.asyncio
    async def test_query_datasource_error(self, grafana_connector):
        from_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectorError("Query failed")
            result = await grafana_connector.query_datasource(
                "prom1",
                {"expr": "up"},
                from_time,
                to_time,
            )

        assert result.success is False
        assert result.data == {}

    @pytest.mark.asyncio
    async def test_query_datasource_empty_results(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        from_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        to_time = datetime(2026, 1, 1, 1, 0, 0, tzinfo=timezone.utc)

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await grafana_connector.query_datasource(
                "prom1",
                {"expr": "up"},
                from_time,
                to_time,
            )

        assert result.success is True
        assert result.data == {}


class TestGrafanaClientManagement:
    """Tests for HTTP client lifecycle."""

    @pytest.mark.asyncio
    async def test_close_client(self, grafana_connector):
        mock_client = AsyncMock()
        grafana_connector._client = mock_client

        await grafana_connector.close()

        mock_client.aclose.assert_called_once()
        assert grafana_connector._client is None

    @pytest.mark.asyncio
    async def test_close_without_client(self, grafana_connector):
        await grafana_connector.close()
        assert grafana_connector._client is None


class TestGrafanaEdgeCases:
    """Tests for edge cases and data parsing."""

    @pytest.mark.asyncio
    async def test_empty_dashboards_list(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.search_dashboards()

        assert result.success is True
        assert result.data == []
        assert result.metadata["count"] == 0

    @pytest.mark.asyncio
    async def test_duration_tracking(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(grafana_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await grafana_connector.search_dashboards()

        # Duration should be tracked
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_annotation_default_time(self, grafana_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}

        with patch.object(grafana_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await grafana_connector.create_annotation("Event")

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        # Time should be set (close to now)
        assert "time" in payload
        assert payload["time"] > 0
