"""Tests for Jenkins Connector."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.base import ConnectorConfig, ConnectorError
from app.connectors.jenkins import JenkinsConnector


@pytest.fixture
def jenkins_config():
    """Create a Jenkins connector config with credentials."""
    return ConnectorConfig(
        url="https://jenkins.example.com",
        username="admin",
        api_key="api-token-12345",
        timeout_seconds=30.0,
    )


@pytest.fixture
def jenkins_config_no_auth():
    """Create a Jenkins connector config without credentials."""
    return ConnectorConfig(
        url="https://jenkins.example.com",
        timeout_seconds=30.0,
    )


@pytest.fixture
def jenkins_connector(jenkins_config):
    """Create a Jenkins connector instance."""
    return JenkinsConnector(jenkins_config)


@pytest.fixture
def jenkins_connector_no_auth(jenkins_config_no_auth):
    """Create a Jenkins connector without auth."""
    return JenkinsConnector(jenkins_config_no_auth)


class TestJenkinsConnectorInit:
    """Tests for JenkinsConnector initialization."""

    def test_connector_type(self, jenkins_connector):
        assert jenkins_connector.connector_type == "jenkins"

    def test_default_headers_with_auth(self, jenkins_connector):
        headers = jenkins_connector._default_headers()
        assert headers["Accept"] == "application/json"
        # Check Basic auth header
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")
        # Decode and verify credentials
        encoded = headers["Authorization"].replace("Basic ", "")
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "admin:api-token-12345"

    def test_default_headers_without_auth(self, jenkins_connector_no_auth):
        headers = jenkins_connector_no_auth._default_headers()
        assert headers["Accept"] == "application/json"
        assert "Authorization" not in headers

    def test_config_stored(self, jenkins_connector, jenkins_config):
        assert jenkins_connector.config == jenkins_config
        assert jenkins_connector.config.url == "https://jenkins.example.com"
        assert jenkins_connector.config.username == "admin"


class TestJenkinsHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.health_check()

        assert result is True
        mock_get.assert_called_once_with("/api/json")

    @pytest.mark.asyncio
    async def test_health_check_failure(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("Connection refused")
            result = await jenkins_connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_non_200(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.health_check()

        assert result is False


class TestJenkinsGetJobs:
    """Tests for get_jobs method."""

    @pytest.mark.asyncio
    async def test_get_jobs_success(self, jenkins_connector):
        jobs_data = {
            "jobs": [
                {"name": "build-api", "url": "http://jenkins/job/build-api/", "color": "blue"},
                {"name": "test-api", "url": "http://jenkins/job/test-api/", "color": "red"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = jobs_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_jobs()

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_jobs_with_folder(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"jobs": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_jobs(folder="my-folder")

        call_args = mock_get.call_args
        assert "/job/my-folder/api/json" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_jobs_without_folder(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"jobs": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_jobs()

        call_args = mock_get.call_args
        assert call_args[0][0] == "/api/json"

    @pytest.mark.asyncio
    async def test_get_jobs_tree_parameter(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"jobs": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_jobs()

        call_args = mock_get.call_args
        assert "tree" in call_args[1]["params"]
        assert "jobs[name,url,color,lastBuild" in call_args[1]["params"]["tree"]

    @pytest.mark.asyncio
    async def test_get_jobs_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await jenkins_connector.get_jobs()

        assert result.success is False
        assert result.data == []


class TestJenkinsGetJob:
    """Tests for get_job method."""

    @pytest.mark.asyncio
    async def test_get_job_success(self, jenkins_connector):
        job_data = {
            "name": "build-api",
            "url": "http://jenkins/job/build-api/",
            "buildable": True,
            "lastBuild": {"number": 42},
        }
        mock_response = MagicMock()
        mock_response.json.return_value = job_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_job("build-api")

        assert result.success is True
        assert result.data["name"] == "build-api"
        mock_get.assert_called_once_with("/job/build-api/api/json")

    @pytest.mark.asyncio
    async def test_get_job_with_folder_path(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"name": "test"}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_job("folder1/folder2/my-job")

        call_args = mock_get.call_args
        assert "/job/folder1/job/folder2/job/my-job/api/json" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("jenkins API error: 404", status_code=404)
            result = await jenkins_connector.get_job("nonexistent-job")

        assert result.success is False
        assert result.data == {}


class TestJenkinsGetBuilds:
    """Tests for get_builds method."""

    @pytest.mark.asyncio
    async def test_get_builds_success(self, jenkins_connector):
        builds_data = {
            "builds": [
                {"number": 42, "result": "SUCCESS", "timestamp": 1704067200000, "duration": 60000, "building": False},
                {"number": 41, "result": "FAILURE", "timestamp": 1704063600000, "duration": 30000, "building": False},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = builds_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_builds("build-api")

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2
        # Check timestamp conversion
        assert "timestamp_iso" in result.data[0]

    @pytest.mark.asyncio
    async def test_get_builds_with_limit(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"builds": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_builds("build-api", limit=5)

        call_args = mock_get.call_args
        # Check the tree parameter includes the limit
        assert "{0,5}" in call_args[1]["params"]["tree"]

    @pytest.mark.asyncio
    async def test_get_builds_timestamp_conversion(self, jenkins_connector):
        builds_data = {
            "builds": [
                {"number": 42, "result": "SUCCESS", "timestamp": 1704067200000, "duration": 60000, "building": False},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = builds_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_builds("build-api")

        # Verify ISO timestamp was added
        assert result.data[0]["timestamp_iso"] == "2024-01-01T00:00:00+00:00"

    @pytest.mark.asyncio
    async def test_get_builds_no_timestamp(self, jenkins_connector):
        builds_data = {
            "builds": [
                {"number": 42, "result": "SUCCESS", "duration": 60000, "building": False},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = builds_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_builds("build-api")

        # Should handle missing timestamp gracefully
        assert result.success is True
        assert "timestamp_iso" not in result.data[0]

    @pytest.mark.asyncio
    async def test_get_builds_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await jenkins_connector.get_builds("build-api")

        assert result.success is False
        assert result.data == []


class TestJenkinsGetBuild:
    """Tests for get_build method."""

    @pytest.mark.asyncio
    async def test_get_build_success(self, jenkins_connector):
        build_data = {
            "number": 42,
            "result": "SUCCESS",
            "timestamp": 1704067200000,
            "duration": 60000,
            "building": False,
            "artifacts": [],
        }
        mock_response = MagicMock()
        mock_response.json.return_value = build_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_build("build-api", 42)

        assert result.success is True
        assert result.data["number"] == 42
        mock_get.assert_called_once_with("/job/build-api/42/api/json")

    @pytest.mark.asyncio
    async def test_get_build_with_folder_path(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"number": 42}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_build("folder/my-job", 42)

        call_args = mock_get.call_args
        assert "/job/folder/job/my-job/42/api/json" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_build_not_found(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("Build not found", status_code=404)
            result = await jenkins_connector.get_build("build-api", 9999)

        assert result.success is False
        assert result.data == {}


class TestJenkinsGetBuildLog:
    """Tests for get_build_log method."""

    @pytest.mark.asyncio
    async def test_get_build_log_success(self, jenkins_connector):
        log_content = "Started by user admin\nBuilding...\nFinished: SUCCESS"
        mock_response = MagicMock()
        mock_response.text = log_content

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_build_log("build-api", 42)

        assert result.success is True
        assert result.data["log"] == log_content
        assert result.metadata["size_bytes"] == len(log_content)

    @pytest.mark.asyncio
    async def test_get_build_log_with_start_line(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.text = "...continued output"

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_build_log("build-api", 42, start_line=1000)

        call_args = mock_get.call_args
        assert call_args[1]["params"]["start"] == 1000

    @pytest.mark.asyncio
    async def test_get_build_log_progressive(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.text = ""

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_build_log("build-api", 42)

        call_args = mock_get.call_args
        assert "progressiveText" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_build_log_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("Log not available")
            result = await jenkins_connector.get_build_log("build-api", 42)

        assert result.success is False
        assert result.data == {}


class TestJenkinsGetQueue:
    """Tests for get_queue method."""

    @pytest.mark.asyncio
    async def test_get_queue_success(self, jenkins_connector):
        queue_data = {
            "items": [
                {"id": 1, "task": {"name": "build-api"}, "why": "Waiting for executor"},
                {"id": 2, "task": {"name": "test-api"}, "why": "In queue"},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = queue_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_queue()

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2
        mock_get.assert_called_once_with("/queue/api/json")

    @pytest.mark.asyncio
    async def test_get_queue_empty(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"items": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_queue()

        assert result.success is True
        assert result.data == []
        assert result.metadata["count"] == 0

    @pytest.mark.asyncio
    async def test_get_queue_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await jenkins_connector.get_queue()

        assert result.success is False
        assert result.data == []


class TestJenkinsGetNodes:
    """Tests for get_nodes method."""

    @pytest.mark.asyncio
    async def test_get_nodes_success(self, jenkins_connector):
        nodes_data = {
            "computer": [
                {"displayName": "master", "offline": False, "temporarilyOffline": False, "idle": True, "numExecutors": 2},
                {"displayName": "agent-1", "offline": False, "temporarilyOffline": False, "idle": False, "numExecutors": 4},
            ]
        }
        mock_response = MagicMock()
        mock_response.json.return_value = nodes_data

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_nodes()

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_nodes_tree_parameter(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"computer": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_nodes()

        call_args = mock_get.call_args
        assert "tree" in call_args[1]["params"]
        assert "displayName" in call_args[1]["params"]["tree"]

    @pytest.mark.asyncio
    async def test_get_nodes_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await jenkins_connector.get_nodes()

        assert result.success is False
        assert result.data == []


class TestJenkinsTriggerBuild:
    """Tests for trigger_build method."""

    @pytest.mark.asyncio
    async def test_trigger_build_success(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await jenkins_connector.trigger_build("build-api")

        assert result.success is True
        assert result.data["queued"] is True
        mock_post.assert_called_once_with("/job/build-api/build", params={})

    @pytest.mark.asyncio
    async def test_trigger_build_with_parameters(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 201

        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await jenkins_connector.trigger_build("build-api", parameters={"BRANCH": "main", "ENV": "prod"})

        call_args = mock_post.call_args
        assert "buildWithParameters" in call_args[0][0]
        assert call_args[1]["params"]["BRANCH"] == "main"
        assert call_args[1]["params"]["ENV"] == "prod"

    @pytest.mark.asyncio
    async def test_trigger_build_200_response(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await jenkins_connector.trigger_build("build-api")

        assert result.success is True

    @pytest.mark.asyncio
    async def test_trigger_build_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectorError("Permission denied")
            result = await jenkins_connector.trigger_build("build-api")

        assert result.success is False
        assert result.data == {}


class TestJenkinsStopBuild:
    """Tests for stop_build method."""

    @pytest.mark.asyncio
    async def test_stop_build_success(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await jenkins_connector.stop_build("build-api", 42)

        assert result.success is True
        assert result.data["stopped"] is True
        mock_post.assert_called_once_with("/job/build-api/42/stop")

    @pytest.mark.asyncio
    async def test_stop_build_302_redirect(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 302

        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await jenkins_connector.stop_build("build-api", 42)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_stop_build_with_folder_path(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            await jenkins_connector.stop_build("folder/my-job", 42)

        call_args = mock_post.call_args
        assert "/job/folder/job/my-job/42/stop" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_stop_build_error(self, jenkins_connector):
        with patch.object(jenkins_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectorError("Build cannot be stopped")
            result = await jenkins_connector.stop_build("build-api", 42)

        assert result.success is False
        assert result.data == {}


class TestJenkinsClientManagement:
    """Tests for HTTP client lifecycle."""

    @pytest.mark.asyncio
    async def test_close_client(self, jenkins_connector):
        mock_client = AsyncMock()
        jenkins_connector._client = mock_client

        await jenkins_connector.close()

        mock_client.aclose.assert_called_once()
        assert jenkins_connector._client is None

    @pytest.mark.asyncio
    async def test_close_without_client(self, jenkins_connector):
        await jenkins_connector.close()
        assert jenkins_connector._client is None


class TestJenkinsEdgeCases:
    """Tests for edge cases and data parsing."""

    @pytest.mark.asyncio
    async def test_empty_jobs_list(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"jobs": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_jobs()

        assert result.success is True
        assert result.data == []
        assert result.metadata["count"] == 0

    @pytest.mark.asyncio
    async def test_missing_jobs_key(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_jobs()

        assert result.success is True
        assert result.data == []

    @pytest.mark.asyncio
    async def test_deeply_nested_folder_path(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"name": "test"}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await jenkins_connector.get_job("a/b/c/d/my-job")

        call_args = mock_get.call_args
        assert "/job/a/job/b/job/c/job/d/job/my-job/api/json" in call_args[0][0]

    def test_basic_auth_encoding(self, jenkins_config):
        connector = JenkinsConnector(jenkins_config)
        headers = connector._default_headers()

        # Verify the encoding is correct
        encoded = headers["Authorization"].replace("Basic ", "")
        decoded = base64.b64decode(encoded).decode()
        assert decoded == "admin:api-token-12345"

    @pytest.mark.asyncio
    async def test_duration_tracking(self, jenkins_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"jobs": []}

        with patch.object(jenkins_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await jenkins_connector.get_jobs()

        assert result.duration_ms >= 0
