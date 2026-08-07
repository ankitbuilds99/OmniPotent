"""Tests for GitLab Connector."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.base import ConnectorConfig, ConnectorError
from app.connectors.gitlab import GitLabConnector


@pytest.fixture
def gitlab_config():
    """Create a GitLab connector config."""
    return ConnectorConfig(
        url="https://gitlab.example.com",
        api_key="glpat-test-token-12345",
        timeout_seconds=30.0,
    )


@pytest.fixture
def gitlab_connector(gitlab_config):
    """Create a GitLab connector instance."""
    return GitLabConnector(gitlab_config)


class TestGitLabConnectorInit:
    """Tests for GitLabConnector initialization."""

    def test_connector_type(self, gitlab_connector):
        assert gitlab_connector.connector_type == "gitlab"

    def test_default_headers(self, gitlab_connector):
        headers = gitlab_connector._default_headers()
        assert headers["Accept"] == "application/json"
        assert headers["PRIVATE-TOKEN"] == "glpat-test-token-12345"

    def test_config_stored(self, gitlab_connector, gitlab_config):
        assert gitlab_connector.config == gitlab_config
        assert gitlab_connector.config.url == "https://gitlab.example.com"

    def test_client_not_created_on_init(self, gitlab_connector):
        assert gitlab_connector._client is None


class TestGitLabProjectEncoding:
    """Tests for project path URL encoding."""

    def test_encode_simple_project(self, gitlab_connector):
        encoded = gitlab_connector._encode_project("group/project")
        assert encoded == "group%2Fproject"

    def test_encode_nested_project(self, gitlab_connector):
        encoded = gitlab_connector._encode_project("org/team/project")
        assert encoded == "org%2Fteam%2Fproject"

    def test_encode_project_with_special_chars(self, gitlab_connector):
        encoded = gitlab_connector._encode_project("group/my-project.git")
        assert encoded == "group%2Fmy-project.git"


class TestGitLabHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.health_check()

        assert result is True
        mock_get.assert_called_once_with("/api/v4/version")

    @pytest.mark.asyncio
    async def test_health_check_failure(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("Connection refused")
            result = await gitlab_connector.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_non_200(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.status_code = 503

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.health_check()

        assert result is False


class TestGitLabGetProject:
    """Tests for get_project method."""

    @pytest.mark.asyncio
    async def test_get_project_success(self, gitlab_connector):
        project_data = {
            "id": 123,
            "name": "my-project",
            "path_with_namespace": "group/my-project",
            "default_branch": "main",
            "web_url": "https://gitlab.example.com/group/my-project",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = project_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_project("group/my-project")

        assert result.success is True
        assert result.data == project_data
        assert result.duration_ms >= 0
        mock_get.assert_called_once_with("/api/v4/projects/group%2Fmy-project")

    @pytest.mark.asyncio
    async def test_get_project_not_found(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("gitlab API error: 404", status_code=404)
            result = await gitlab_connector.get_project("nonexistent/project")

        assert result.success is False
        assert result.data == {}
        assert "404" in result.error

    @pytest.mark.asyncio
    async def test_get_project_connection_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("gitlab connection error: timeout")
            result = await gitlab_connector.get_project("group/project")

        assert result.success is False
        assert "timeout" in result.error


class TestGitLabGetPipelines:
    """Tests for get_pipelines method."""

    @pytest.mark.asyncio
    async def test_get_pipelines_success(self, gitlab_connector):
        pipelines_data = [
            {"id": 100, "status": "success", "ref": "main", "sha": "abc123"},
            {"id": 99, "status": "failed", "ref": "feature", "sha": "def456"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = pipelines_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_pipelines("group/project")

        assert result.success is True
        assert result.data == pipelines_data
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_pipelines_with_ref_filter(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_pipelines("group/project", ref="main")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["ref"] == "main"

    @pytest.mark.asyncio
    async def test_get_pipelines_with_status_filter(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_pipelines("group/project", status="failed")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_get_pipelines_with_limit(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_pipelines("group/project", limit=50)

        call_args = mock_get.call_args
        assert call_args[1]["params"]["per_page"] == 50

    @pytest.mark.asyncio
    async def test_get_pipelines_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await gitlab_connector.get_pipelines("group/project")

        assert result.success is False
        assert result.data == []


class TestGitLabGetPipeline:
    """Tests for get_pipeline method."""

    @pytest.mark.asyncio
    async def test_get_pipeline_success(self, gitlab_connector):
        pipeline_data = {
            "id": 100,
            "status": "success",
            "ref": "main",
            "sha": "abc123",
            "created_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:10:00Z",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = pipeline_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_pipeline("group/project", 100)

        assert result.success is True
        assert result.data["id"] == 100
        assert result.data["status"] == "success"
        mock_get.assert_called_once_with("/api/v4/projects/group%2Fproject/pipelines/100")

    @pytest.mark.asyncio
    async def test_get_pipeline_not_found(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("gitlab API error: 404", status_code=404)
            result = await gitlab_connector.get_pipeline("group/project", 99999)

        assert result.success is False
        assert result.data == {}


class TestGitLabGetPipelineJobs:
    """Tests for get_pipeline_jobs method."""

    @pytest.mark.asyncio
    async def test_get_pipeline_jobs_success(self, gitlab_connector):
        jobs_data = [
            {"id": 1, "name": "build", "status": "success", "stage": "build"},
            {"id": 2, "name": "test", "status": "success", "stage": "test"},
            {"id": 3, "name": "deploy", "status": "running", "stage": "deploy"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = jobs_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_pipeline_jobs("group/project", 100)

        assert result.success is True
        assert len(result.data) == 3
        assert result.metadata["count"] == 3

    @pytest.mark.asyncio
    async def test_get_pipeline_jobs_with_scope(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_pipeline_jobs("group/project", 100, scope="failed")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["scope"] == "failed"

    @pytest.mark.asyncio
    async def test_get_pipeline_jobs_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await gitlab_connector.get_pipeline_jobs("group/project", 100)

        assert result.success is False
        assert result.data == []


class TestGitLabGetJobLog:
    """Tests for get_job_log method."""

    @pytest.mark.asyncio
    async def test_get_job_log_success(self, gitlab_connector):
        log_content = "Running build...\nInstalling deps...\nBuild complete!"
        mock_response = MagicMock()
        mock_response.text = log_content

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_job_log("group/project", 123)

        assert result.success is True
        assert result.data["log"] == log_content
        assert result.metadata["size_bytes"] == len(log_content)
        mock_get.assert_called_once_with("/api/v4/projects/group%2Fproject/jobs/123/trace")

    @pytest.mark.asyncio
    async def test_get_job_log_empty(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.text = ""

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_job_log("group/project", 123)

        assert result.success is True
        assert result.data["log"] == ""
        assert result.metadata["size_bytes"] == 0

    @pytest.mark.asyncio
    async def test_get_job_log_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("Job not found")
            result = await gitlab_connector.get_job_log("group/project", 123)

        assert result.success is False
        assert result.data == {}


class TestGitLabGetMergeRequests:
    """Tests for get_merge_requests method."""

    @pytest.mark.asyncio
    async def test_get_merge_requests_success(self, gitlab_connector):
        mrs_data = [
            {"iid": 1, "title": "Feature A", "state": "opened", "author": {"username": "dev1"}},
            {"iid": 2, "title": "Bugfix B", "state": "opened", "author": {"username": "dev2"}},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = mrs_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_merge_requests("group/project")

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_merge_requests_with_state(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_merge_requests("group/project", state="merged")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["state"] == "merged"

    @pytest.mark.asyncio
    async def test_get_merge_requests_with_limit(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_merge_requests("group/project", limit=50)

        call_args = mock_get.call_args
        assert call_args[1]["params"]["per_page"] == 50

    @pytest.mark.asyncio
    async def test_get_merge_requests_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await gitlab_connector.get_merge_requests("group/project")

        assert result.success is False
        assert result.data == []


class TestGitLabGetDeployments:
    """Tests for get_deployments method."""

    @pytest.mark.asyncio
    async def test_get_deployments_success(self, gitlab_connector):
        deployments_data = [
            {"id": 1, "status": "success", "environment": "production"},
            {"id": 2, "status": "running", "environment": "staging"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = deployments_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_deployments("group/project")

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_deployments_with_environment(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_deployments("group/project", environment="production")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["environment"] == "production"

    @pytest.mark.asyncio
    async def test_get_deployments_with_status(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_deployments("group/project", status="success")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_deployments_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await gitlab_connector.get_deployments("group/project")

        assert result.success is False
        assert result.data == []


class TestGitLabGetCommits:
    """Tests for get_commits method."""

    @pytest.mark.asyncio
    async def test_get_commits_success(self, gitlab_connector):
        commits_data = [
            {"id": "abc123", "message": "Add feature", "author_name": "Dev"},
            {"id": "def456", "message": "Fix bug", "author_name": "Dev2"},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = commits_data

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_commits("group/project")

        assert result.success is True
        assert len(result.data) == 2
        assert result.metadata["count"] == 2

    @pytest.mark.asyncio
    async def test_get_commits_with_ref(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_commits("group/project", ref="develop")

        call_args = mock_get.call_args
        assert call_args[1]["params"]["ref_name"] == "develop"

    @pytest.mark.asyncio
    async def test_get_commits_with_since(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        since_date = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_commits("group/project", since=since_date)

        call_args = mock_get.call_args
        assert "since" in call_args[1]["params"]
        assert "2026-01-01" in call_args[1]["params"]["since"]

    @pytest.mark.asyncio
    async def test_get_commits_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = ConnectorError("API error")
            result = await gitlab_connector.get_commits("group/project")

        assert result.success is False
        assert result.data == []


class TestGitLabRetryPipeline:
    """Tests for retry_pipeline method."""

    @pytest.mark.asyncio
    async def test_retry_pipeline_success(self, gitlab_connector):
        retry_data = {
            "id": 101,
            "status": "pending",
            "ref": "main",
        }
        mock_response = MagicMock()
        mock_response.json.return_value = retry_data

        with patch.object(gitlab_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await gitlab_connector.retry_pipeline("group/project", 100)

        assert result.success is True
        assert result.data["id"] == 101
        mock_post.assert_called_once_with("/api/v4/projects/group%2Fproject/pipelines/100/retry")

    @pytest.mark.asyncio
    async def test_retry_pipeline_error(self, gitlab_connector):
        with patch.object(gitlab_connector, "_post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = ConnectorError("Cannot retry pipeline")
            result = await gitlab_connector.retry_pipeline("group/project", 100)

        assert result.success is False
        assert result.data == {}
        assert "Cannot retry" in result.error


class TestGitLabClientManagement:
    """Tests for HTTP client lifecycle."""

    @pytest.mark.asyncio
    async def test_close_client(self, gitlab_connector):
        # Create a mock client
        mock_client = AsyncMock()
        gitlab_connector._client = mock_client

        await gitlab_connector.close()

        mock_client.aclose.assert_called_once()
        assert gitlab_connector._client is None

    @pytest.mark.asyncio
    async def test_close_without_client(self, gitlab_connector):
        # Should not raise
        await gitlab_connector.close()
        assert gitlab_connector._client is None

    def test_client_property_creates_client(self, gitlab_connector):
        assert gitlab_connector._client is None
        client = gitlab_connector.client
        assert client is not None
        assert gitlab_connector._client is client


class TestGitLabEdgeCases:
    """Tests for edge cases and data parsing."""

    @pytest.mark.asyncio
    async def test_empty_pipelines_list(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = []

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await gitlab_connector.get_pipelines("group/project")

        assert result.success is True
        assert result.data == []
        assert result.metadata["count"] == 0

    @pytest.mark.asyncio
    async def test_special_chars_in_project_name(self, gitlab_connector):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1}

        with patch.object(gitlab_connector, "_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            await gitlab_connector.get_project("org/sub-group/my.project")

        call_args = mock_get.call_args
        # Verify URL encoding
        assert "org%2Fsub-group%2Fmy.project" in call_args[0][0]
