"""Tests for evidence_collector.py - evidence collection from multiple sources."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.verification.evidence_collector import (
    Evidence,
    EvidenceCollector,
    EvidenceSource,
)


class TestEvidenceSource:
    """Tests for EvidenceSource enum."""

    def test_all_sources(self):
        """Should have all expected sources."""
        sources = [e.value for e in EvidenceSource]
        assert "metrics" in sources
        assert "logs" in sources
        assert "git" in sources
        assert "kubernetes" in sources
        assert "knowledge_graph" in sources
        assert "traces" in sources
        assert "external" in sources


class TestEvidence:
    """Tests for Evidence dataclass."""

    def test_defaults(self):
        """Should initialize with safe defaults."""
        ev = Evidence(
            source=EvidenceSource.METRICS,
            data={"value": 0.5},
            timestamp=datetime.now(timezone.utc),
            confidence=0.9,
        )
        assert ev.metric_name is None
        assert ev.metric_value is None
        assert ev.log_lines == []
        assert ev.commit_sha is None
        assert ev.resource_type is None

    def test_to_dict(self):
        """Should serialize to dictionary."""
        ev = Evidence(
            source=EvidenceSource.METRICS,
            data={"result": [{"value": 0.5}]},
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            confidence=0.9,
            metric_name="error_rate",
            metric_value=0.05,
        )
        d = ev.to_dict()
        assert d["source"] == "metrics"
        assert d["timestamp"] == "2024-01-01T12:00:00+00:00"
        assert d["confidence"] == 0.9
        assert d["metric_name"] == "error_rate"
        assert d["metric_value"] == 0.05
        assert d["log_lines_count"] == 0

    def test_to_dict_with_logs(self):
        """Should include log line count."""
        ev = Evidence(
            source=EvidenceSource.LOGS,
            data={"keyword": "error"},
            timestamp=datetime.now(timezone.utc),
            confidence=0.8,
            log_lines=["error 1", "error 2", "error 3"],
        )
        d = ev.to_dict()
        assert d["log_lines_count"] == 3


class TestEvidenceCollector:
    """Tests for EvidenceCollector class."""

    def test_init_no_dependencies(self):
        """Should initialize without dependencies."""
        collector = EvidenceCollector()
        assert collector.prometheus_url is None
        assert collector.loki_url is None
        assert collector.k8s is None
        assert collector.github_token is None
        assert collector.graph is None

    def test_init_with_dependencies(self):
        """Should accept all dependencies."""
        mock_k8s = MagicMock()
        mock_graph = MagicMock()
        collector = EvidenceCollector(
            prometheus_url="http://prometheus:9090",
            loki_url="http://loki:3100",
            elasticsearch_url="http://es:9200",
            k8s_client=mock_k8s,
            github_token="ghp_token",
            knowledge_graph=mock_graph,
        )
        assert collector.prometheus_url == "http://prometheus:9090"
        assert collector.loki_url == "http://loki:3100"
        assert collector.elasticsearch_url == "http://es:9200"
        assert collector.k8s is mock_k8s
        assert collector.github_token == "ghp_token"
        assert collector.graph is mock_graph

    @pytest.mark.asyncio
    async def test_get_http_client_creates_client(self):
        """Should create HTTP client on first call."""
        collector = EvidenceCollector()
        client = await collector._get_http_client()
        assert client is not None
        await collector.close()

    @pytest.mark.asyncio
    async def test_get_http_client_reuses_client(self):
        """Should reuse existing HTTP client."""
        collector = EvidenceCollector()
        client1 = await collector._get_http_client()
        client2 = await collector._get_http_client()
        assert client1 is client2
        await collector.close()

    @pytest.mark.asyncio
    async def test_github_token_is_not_a_default_header_on_the_shared_client(self):
        """The GitHub token must be attached per-request, to GitHub only.

        The one httpx client is also used for Prometheus, Loki and
        Elasticsearch. A default Authorization header would have leaked the
        GitHub token to every one of those hosts.
        """
        collector = EvidenceCollector(github_token="ghp_test123")
        client = await collector._get_http_client()
        assert "Authorization" not in client.headers
        assert collector._github_headers()["Authorization"] == "token ghp_test123"
        await collector.close()

    @pytest.mark.asyncio
    async def test_github_headers_omit_authorization_without_a_token(self):
        collector = EvidenceCollector()
        assert "Authorization" not in collector._github_headers()

    @pytest.mark.asyncio
    async def test_close(self):
        """Should close HTTP client."""
        collector = EvidenceCollector()
        await collector._get_http_client()
        await collector.close()
        assert collector._http_client is None

    @pytest.mark.asyncio
    async def test_collect_evidence_no_sources(self):
        """Should return empty list when no sources configured."""
        collector = EvidenceCollector()
        evidence = await collector.collect_evidence(
            claim_text="Error rate increased",
            service_id="my-service",
        )
        # Without any configured sources, should return empty
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_evidence_specific_sources(self):
        """Should only query specified sources."""
        collector = EvidenceCollector(prometheus_url="http://prometheus:9090")

        with patch.object(collector, "_collect_metric_evidence", new_callable=AsyncMock) as mock_metrics:
            mock_metrics.return_value = []
            with patch.object(collector, "_collect_log_evidence", new_callable=AsyncMock) as mock_logs:
                mock_logs.return_value = []
                await collector.collect_evidence(
                    claim_text="Test",
                    service_id="svc",
                    sources=[EvidenceSource.METRICS],
                )
                mock_metrics.assert_called_once()
                mock_logs.assert_not_called()

    @pytest.mark.asyncio
    async def test_collect_evidence_handles_exceptions(self):
        """Should handle individual source failures gracefully."""
        collector = EvidenceCollector(prometheus_url="http://prometheus:9090")

        with patch.object(collector, "_collect_metric_evidence", new_callable=AsyncMock) as mock_metrics:
            mock_metrics.side_effect = Exception("Prometheus unavailable")
            evidence = await collector.collect_evidence(
                claim_text="Test",
                service_id="svc",
                sources=[EvidenceSource.METRICS],
            )
            # Should not raise, should return empty
            assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_metric_evidence_no_prometheus(self):
        """Should return empty without Prometheus URL."""
        collector = EvidenceCollector()
        evidence = await collector._collect_metric_evidence(
            "Error rate",
            "my-service",
            60,
        )
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_metric_evidence_no_service(self):
        """Should return empty without service_id."""
        collector = EvidenceCollector(prometheus_url="http://prometheus:9090")
        evidence = await collector._collect_metric_evidence(
            "Error rate",
            None,
            60,
        )
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_metric_evidence_success(self):
        """Should collect metric evidence from Prometheus."""
        collector = EvidenceCollector(prometheus_url="http://prometheus:9090")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "result": [{"value": [1234567890, "0.05"]}],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch.object(collector, "_get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_client
            evidence = await collector._collect_metric_evidence(
                "Error rate increased",
                "my-service",
                60,
            )

        assert len(evidence) > 0
        assert evidence[0].source == EvidenceSource.METRICS
        assert evidence[0].metric_value == 0.05

    @pytest.mark.asyncio
    async def test_collect_metric_evidence_query_failure(self):
        """Should handle Prometheus query failures."""
        collector = EvidenceCollector(prometheus_url="http://prometheus:9090")

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection refused")

        with patch.object(collector, "_get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_client
            evidence = await collector._collect_metric_evidence(
                "Error rate",
                "my-service",
                60,
            )

        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_log_evidence_no_service(self):
        """Should return empty without service_id."""
        collector = EvidenceCollector(loki_url="http://loki:3100")
        evidence = await collector._collect_log_evidence("Error", None, 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_log_evidence_loki(self):
        """Should collect log evidence from Loki."""
        collector = EvidenceCollector(loki_url="http://loki:3100")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {"values": [
                        [1234567890, "Error: connection timeout"],
                        [1234567891, "Error: service unavailable"],
                    ]}
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch.object(collector, "_get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_client
            evidence = await collector._collect_log_evidence(
                "Error timeout",
                "my-service",
                60,
            )

        assert len(evidence) > 0
        assert evidence[0].source == EvidenceSource.LOGS
        assert len(evidence[0].log_lines) == 2

    @pytest.mark.asyncio
    async def test_collect_log_evidence_elasticsearch(self):
        """Should collect log evidence from Elasticsearch."""
        collector = EvidenceCollector(elasticsearch_url="http://es:9200")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "hits": {
                "hits": [
                    {"_source": {"message": "Error: timeout"}},
                    {"_source": {"message": "Error: failure"}},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch.object(collector, "_get_http_client", new_callable=AsyncMock) as mock_get_client:
            mock_get_client.return_value = mock_client
            evidence = await collector._collect_log_evidence(
                "Error timeout",
                "my-service",
                60,
            )

        assert len(evidence) > 0
        assert evidence[0].source == EvidenceSource.LOGS

    @pytest.mark.asyncio
    async def test_collect_k8s_evidence_no_client(self):
        """Should return empty without k8s client."""
        collector = EvidenceCollector()
        evidence = await collector._collect_k8s_evidence("Test", "svc", 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_k8s_evidence_no_service(self):
        """Should return empty without service_id."""
        mock_k8s = MagicMock()
        collector = EvidenceCollector(k8s_client=mock_k8s)
        evidence = await collector._collect_k8s_evidence("Test", None, 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_k8s_evidence_deployment(self):
        """Should collect deployment evidence from k8s."""
        mock_k8s = AsyncMock()
        mock_deployment = MagicMock()
        mock_deployment.spec.replicas = 3
        mock_deployment.status.ready_replicas = 3
        mock_deployment.status.available_replicas = 3
        mock_deployment.spec.template.spec.containers = [MagicMock(image="app:v1.2.0")]
        mock_k8s.read_namespaced_deployment.return_value = mock_deployment

        # No warning events
        mock_events = MagicMock()
        mock_events.items = []
        mock_k8s.list_namespaced_event.return_value = mock_events

        collector = EvidenceCollector(k8s_client=mock_k8s)
        evidence = await collector._collect_k8s_evidence("Test", "my-service", 60)

        assert len(evidence) == 1
        assert evidence[0].source == EvidenceSource.KUBERNETES
        assert evidence[0].resource_type == "Deployment"
        assert evidence[0].data["replicas"] == 3

    @pytest.mark.asyncio
    async def test_collect_k8s_evidence_warning_events(self):
        """Should collect warning events from k8s."""
        mock_k8s = AsyncMock()

        # Deployment lookup fails
        mock_k8s.read_namespaced_deployment.side_effect = Exception("Not found")

        # Warning events
        mock_event = MagicMock()
        mock_event.type = "Warning"
        mock_event.reason = "FailedMount"
        mock_event.message = "Unable to mount volume"
        mock_event.count = 5
        mock_events = MagicMock()
        mock_events.items = [mock_event]
        mock_k8s.list_namespaced_event.return_value = mock_events

        collector = EvidenceCollector(k8s_client=mock_k8s)
        evidence = await collector._collect_k8s_evidence("Test", "my-service", 60)

        assert len(evidence) == 1
        assert evidence[0].source == EvidenceSource.KUBERNETES
        assert evidence[0].resource_type == "Event"
        assert evidence[0].data["event_count"] == 1

    @pytest.mark.asyncio
    async def test_collect_git_evidence_no_token(self):
        """Should return empty without GitHub token."""
        collector = EvidenceCollector()
        evidence = await collector._collect_git_evidence("Test", "svc", 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_git_evidence_no_service(self):
        """Should return empty without service_id."""
        collector = EvidenceCollector(github_token="ghp_token")
        evidence = await collector._collect_git_evidence("Test", None, 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_git_evidence_without_a_repo(self):
        """A token with no repo has nothing to read commits from."""
        collector = EvidenceCollector(github_token="ghp_token")
        evidence = await collector._collect_git_evidence("Test", "svc", 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_graph_evidence_no_graph(self):
        """Should return empty without knowledge graph."""
        collector = EvidenceCollector()
        evidence = await collector._collect_graph_evidence("Test", "svc", None, 60)
        assert evidence == []

    @pytest.mark.asyncio
    async def test_collect_graph_evidence_incident_changes(self):
        """Should collect correlated changes for incident."""
        mock_graph = AsyncMock()
        mock_change = MagicMock()
        mock_change.to_cypher_properties.return_value = {"type": "deployment", "version": "v1.2"}
        mock_graph.find_changes_before_incident.return_value = [mock_change]
        mock_graph.get_recent_deployments.return_value = []

        collector = EvidenceCollector(knowledge_graph=mock_graph)
        evidence = await collector._collect_graph_evidence(
            "Test",
            "my-service",
            "incident-123",
            60,
        )

        assert len(evidence) >= 1
        assert any(e.data.get("type") == "correlated_changes" for e in evidence)

    @pytest.mark.asyncio
    async def test_collect_graph_evidence_deployments(self):
        """Should collect recent deployments."""
        mock_graph = AsyncMock()
        mock_graph.find_changes_before_incident.return_value = []
        mock_deploy = MagicMock()
        mock_deploy.to_cypher_properties.return_value = {"version": "v1.3", "timestamp": "2024-01-01"}
        mock_graph.get_recent_deployments.return_value = [mock_deploy]

        collector = EvidenceCollector(knowledge_graph=mock_graph)
        evidence = await collector._collect_graph_evidence(
            "Test",
            "my-service",
            None,
            60,
        )

        assert len(evidence) == 1
        assert evidence[0].data["type"] == "recent_deployments"

    @pytest.mark.asyncio
    async def test_collect_graph_evidence_error(self):
        """Should handle graph errors gracefully."""
        mock_graph = AsyncMock()
        mock_graph.find_changes_before_incident.side_effect = Exception("Graph error")
        mock_graph.get_recent_deployments.side_effect = Exception("Graph error")

        collector = EvidenceCollector(knowledge_graph=mock_graph)
        evidence = await collector._collect_graph_evidence(
            "Test",
            "my-service",
            "incident-123",
            60,
        )

        assert evidence == []

    def test_infer_relevant_metrics_error(self):
        """Should infer error metrics."""
        collector = EvidenceCollector()
        metrics = collector._infer_relevant_metrics("Error rate increased", "svc")
        assert "error_rate" in metrics
        assert "svc" in metrics["error_rate"]

    def test_infer_relevant_metrics_latency(self):
        """Should infer latency metrics."""
        collector = EvidenceCollector()
        metrics = collector._infer_relevant_metrics("Slow response times", "svc")
        assert "latency_p99" in metrics

    def test_infer_relevant_metrics_cpu(self):
        """Should infer CPU metrics."""
        collector = EvidenceCollector()
        metrics = collector._infer_relevant_metrics("High CPU usage", "svc")
        assert "cpu_usage" in metrics

    def test_infer_relevant_metrics_memory(self):
        """Should infer memory metrics."""
        collector = EvidenceCollector()
        metrics = collector._infer_relevant_metrics("OOM killed", "svc")
        assert "memory_usage" in metrics

    def test_infer_relevant_metrics_traffic(self):
        """Should infer traffic metrics."""
        collector = EvidenceCollector()
        metrics = collector._infer_relevant_metrics("Traffic spike in requests", "svc")
        assert "request_rate" in metrics

    def test_infer_relevant_metrics_default(self):
        """Should use default metrics when no keywords match."""
        collector = EvidenceCollector()
        metrics = collector._infer_relevant_metrics("Something happened", "svc")
        assert "error_rate" in metrics
        assert "latency_p99" in metrics

    def test_extract_keywords(self):
        """Should extract meaningful keywords."""
        collector = EvidenceCollector()
        keywords = collector._extract_keywords("The error rate is increasing on the service")
        assert "error" in keywords
        assert "rate" in keywords
        assert "service" in keywords
        assert "the" not in keywords
        assert "is" not in keywords
        assert "on" not in keywords

    def test_extract_keywords_dedupes(self):
        """Should dedupe keywords."""
        collector = EvidenceCollector()
        keywords = collector._extract_keywords("error error error timeout timeout")
        assert keywords.count("error") == 1
        assert keywords.count("timeout") == 1

    def test_extract_keywords_limits(self):
        """Should limit to 5 keywords."""
        collector = EvidenceCollector()
        keywords = collector._extract_keywords(
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
        )
        assert len(keywords) <= 5

    def test_extract_keywords_short_words_excluded(self):
        """Should exclude words shorter than 3 chars."""
        collector = EvidenceCollector()
        keywords = collector._extract_keywords("An IO error on DB caused timeout")
        assert "io" not in keywords
        assert "db" not in keywords
        assert "error" in keywords
        assert "timeout" in keywords


class TestEvidenceCollectorIntegration:
    """Integration-style tests for EvidenceCollector."""

    @pytest.mark.asyncio
    async def test_collect_evidence_all_sources(self):
        """Should collect from all configured sources."""
        mock_k8s = AsyncMock()
        mock_deployment = MagicMock()
        mock_deployment.spec.replicas = 3
        mock_deployment.status.ready_replicas = 3
        mock_deployment.status.available_replicas = 3
        mock_deployment.spec.template.spec.containers = [MagicMock(image="app:v1")]
        mock_k8s.read_namespaced_deployment.return_value = mock_deployment
        mock_k8s.list_namespaced_event.return_value = MagicMock(items=[])

        mock_graph = AsyncMock()
        mock_deploy = MagicMock()
        mock_deploy.to_cypher_properties.return_value = {"version": "v1"}
        mock_graph.get_recent_deployments.return_value = [mock_deploy]
        mock_graph.find_changes_before_incident.return_value = []

        collector = EvidenceCollector(
            k8s_client=mock_k8s,
            knowledge_graph=mock_graph,
        )

        evidence = await collector.collect_evidence(
            claim_text="Error rate increased after deployment",
            service_id="my-service",
            incident_id="inc-123",
            time_window_minutes=60,
            sources=[EvidenceSource.KUBERNETES, EvidenceSource.KNOWLEDGE_GRAPH],
        )

        # Should have evidence from both sources
        sources = {e.source for e in evidence}
        assert EvidenceSource.KUBERNETES in sources
        assert EvidenceSource.KNOWLEDGE_GRAPH in sources

    @pytest.mark.asyncio
    async def test_evidence_confidence_levels(self):
        """Should assign appropriate confidence levels."""
        mock_k8s = AsyncMock()
        mock_deployment = MagicMock()
        mock_deployment.spec.replicas = 3
        mock_deployment.status.ready_replicas = 3
        mock_deployment.status.available_replicas = 3
        mock_deployment.spec.template.spec.containers = [MagicMock(image="app:v1")]
        mock_k8s.read_namespaced_deployment.return_value = mock_deployment
        mock_k8s.list_namespaced_event.return_value = MagicMock(items=[])

        collector = EvidenceCollector(k8s_client=mock_k8s)
        evidence = await collector.collect_evidence(
            claim_text="Test",
            service_id="svc",
            sources=[EvidenceSource.KUBERNETES],
        )

        # K8s deployment evidence should have high confidence
        for ev in evidence:
            if ev.resource_type == "Deployment":
                assert ev.confidence >= 0.9


class _GitHubHandler(BaseHTTPRequestHandler):
    """A real HTTP server that answers GitHub's list-commits endpoint."""

    commits: list[dict] = []
    requests: list[tuple[str, dict]] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        type(self).requests.append((parsed.path, dict(self.headers)))

        if not parsed.path.endswith("/commits"):
            self.send_response(404)
            self.end_headers()
            return

        payload = json.dumps(type(self).commits).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # noqa: A003 - silence test noise
        return


@pytest.fixture
def github_api():
    """A GitHub-shaped HTTP API on localhost. Returns its base URL."""
    _GitHubHandler.commits = []
    _GitHubHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _commit(sha: str, message: str, author: str = "Ada") -> dict:
    return {
        "sha": sha,
        "commit": {
            "message": message,
            "author": {"name": author, "date": "2026-01-01T00:00:00Z"},
        },
    }


class TestGitEvidence:
    """`_collect_git_evidence` used to be `if not token: return []` followed
    immediately by `return []` — the GIT source could never produce anything."""

    @pytest.mark.asyncio
    async def test_commits_touching_the_service_become_evidence(self, github_api):
        _GitHubHandler.commits = [
            _commit("aaa111", "checkout: raise pool size\n\ndetails"),
            _commit("bbb222", "docs: unrelated tweak"),
        ]
        collector = EvidenceCollector(
            github_token="ghp_token",
            github_repo="acme/platform",
            github_api_url=github_api,
        )

        evidence = await collector._collect_git_evidence("Latency is up", "checkout", 60)
        await collector.close()

        assert len(evidence) == 1
        ev = evidence[0]
        assert ev.source == EvidenceSource.GIT
        assert ev.commit_sha == "aaa111"
        assert ev.author == "Ada"
        assert ev.confidence == 0.8
        assert ev.data["matched_service"] is True
        assert ev.data["count"] == 1
        assert ev.data["commits"][0]["message"] == "checkout: raise pool size"
        # The token went to GitHub, and only in this request.
        path, headers = _GitHubHandler.requests[0]
        assert path == "/repos/acme/platform/commits"
        assert headers["Authorization"] == "token ghp_token"

    @pytest.mark.asyncio
    async def test_unrelated_commits_are_weaker_evidence(self, github_api):
        _GitHubHandler.commits = [_commit("ccc333", "docs: unrelated tweak")]
        collector = EvidenceCollector(
            github_token="ghp_token",
            github_repo="acme/platform",
            github_api_url=github_api,
        )

        evidence = await collector._collect_git_evidence("Latency is up", "checkout", 60)
        await collector.close()

        assert len(evidence) == 1
        assert evidence[0].confidence == 0.4
        assert evidence[0].data["matched_service"] is False

    @pytest.mark.asyncio
    async def test_no_commits_in_the_window_is_no_evidence(self, github_api):
        _GitHubHandler.commits = []
        collector = EvidenceCollector(
            github_token="ghp_token",
            github_repo="acme/platform",
            github_api_url=github_api,
        )

        evidence = await collector._collect_git_evidence("Latency is up", "checkout", 60)
        await collector.close()

        assert evidence == []

    @pytest.mark.asyncio
    async def test_an_unreachable_github_is_not_an_exception(self):
        collector = EvidenceCollector(
            github_token="ghp_token",
            github_repo="acme/platform",
            github_api_url="http://127.0.0.1:1",
        )

        evidence = await collector._collect_git_evidence("Latency is up", "checkout", 60)
        await collector.close()

        assert evidence == []


class TestConfiguredSources:
    """What the deployment can actually reach — the basis for the 503."""

    def test_nothing_configured_means_no_sources(self):
        assert EvidenceCollector().configured_sources() == []

    def test_each_backend_enables_its_own_source(self):
        assert EvidenceCollector(prometheus_url="http://p").configured_sources() == [
            EvidenceSource.METRICS
        ]
        assert EvidenceCollector(loki_url="http://l").configured_sources() == [
            EvidenceSource.LOGS
        ]
        assert EvidenceCollector(elasticsearch_url="http://e").configured_sources() == [
            EvidenceSource.LOGS
        ]
        assert EvidenceCollector(k8s_client=object()).configured_sources() == [
            EvidenceSource.KUBERNETES
        ]

    def test_github_needs_both_a_token_and_a_repo(self):
        assert EvidenceCollector(github_token="ghp").configured_sources() == []
        assert EvidenceCollector(github_repo="a/b").configured_sources() == []
        assert EvidenceCollector(
            github_token="ghp", github_repo="a/b"
        ).configured_sources() == [EvidenceSource.GIT]

    def test_missing_config_names_the_settings_an_operator_must_set(self):
        missing = EvidenceCollector.missing_source_config()
        assert "PROMETHEUS_URL" in missing
        assert "LOKI_URL or ELASTICSEARCH_URL" in missing
        assert "GITHUB_TOKEN + GITHUB_REPO" in missing
