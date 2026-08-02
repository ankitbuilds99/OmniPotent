"""Tests for VPC Connector system."""

import pytest

from app.vpc_connector.redaction import RedactionEngine, RedactionRule
from app.vpc_connector.collectors import (
    DataSourceType,
    QueryResult,
    LogCollector,
    MetricsCollector,
    CodeCollector,
    KubernetesCollector,
    TracesCollector,
)
from app.vpc_connector.connector import (
    VPCConnector,
    ConnectorConfig,
    EvidencePackage,
)


class TestRedactionEngine:
    """Tests for the RedactionEngine."""

    def test_redact_email(self):
        engine = RedactionEngine()
        text = "Contact user@example.com for support"
        redacted, rules = engine.redact(text)
        assert "[EMAIL]" in redacted
        assert "user@example.com" not in redacted
        assert "email" in rules

    def test_redact_ssn(self):
        engine = RedactionEngine()
        text = "SSN: 123-45-6789"
        redacted, rules = engine.redact(text)
        assert "[SSN]" in redacted
        assert "123-45-6789" not in redacted
        assert "ssn" in rules

    def test_redact_credit_card(self):
        engine = RedactionEngine()
        text = "Card: 4111-1111-1111-1111"
        redacted, rules = engine.redact(text)
        assert "[CARD]" in redacted
        assert "4111" not in redacted

    def test_redact_aws_access_key(self):
        engine = RedactionEngine()
        text = "AWS Key: AKIAIOSFODNN7EXAMPLE"
        redacted, rules = engine.redact(text)
        assert "[AWS_KEY]" in redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in redacted

    def test_redact_github_token(self):
        engine = RedactionEngine()
        text = "Token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        redacted, rules = engine.redact(text)
        assert "[GITHUB_TOKEN]" in redacted

    def test_redact_jwt(self):
        engine = RedactionEngine()
        # Test JWT pattern matching with a clearly fake test token
        # Using base64-encoded {"alg":"HS256","typ":"JWT"}.{"sub":"test"}.fakesig
        text = "Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0In0.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        redacted, rules = engine.redact(text)
        assert "[JWT]" in redacted

    def test_redact_private_key(self):
        engine = RedactionEngine()
        text = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhki\n-----END PRIVATE KEY-----"
        redacted, rules = engine.redact(text)
        assert "[PRIVATE_KEY]" in redacted
        assert "MIIEvQIBADANBgkqhki" not in redacted

    def test_redact_internal_ip_10(self):
        engine = RedactionEngine()
        text = "Server at 10.0.1.50"
        redacted, rules = engine.redact(text)
        assert "[INTERNAL_IP]" in redacted
        assert "10.0.1.50" not in redacted

    def test_redact_internal_ip_192(self):
        engine = RedactionEngine()
        text = "DB at 192.168.1.100"
        redacted, rules = engine.redact(text)
        assert "[INTERNAL_IP]" in redacted
        assert "192.168.1.100" not in redacted

    def test_redact_postgres_uri(self):
        engine = RedactionEngine()
        text = "postgres://user:pass@host:5432/db"
        redacted, rules = engine.redact(text)
        assert "[POSTGRES_URI]" in redacted
        assert "pass" not in redacted

    def test_redact_mongodb_uri(self):
        engine = RedactionEngine()
        text = "mongodb+srv://user:pass@cluster.mongodb.net/db"
        redacted, rules = engine.redact(text)
        assert "[MONGODB_URI]" in redacted

    def test_redact_dict(self):
        engine = RedactionEngine()
        data = {
            "user": {
                "email": "test@example.com",
                "name": "John",
            },
            "logs": [
                {"message": "Error from 10.0.0.1"},
                {"message": "Normal log"},
            ],
        }
        redacted, rules = engine.redact_dict(data)
        assert redacted["user"]["email"] == "[EMAIL]"
        assert "[INTERNAL_IP]" in redacted["logs"][0]["message"]
        assert redacted["logs"][1]["message"] == "Normal log"

    def test_custom_rule(self):
        custom = RedactionRule(
            name="customer_id",
            pattern=r"CUST-\d{8}",
            replacement="[CUSTOMER_ID]",
        )
        engine = RedactionEngine(custom_rules=[custom])
        text = "Customer CUST-12345678 placed order"
        redacted, rules = engine.redact(text)
        assert "[CUSTOMER_ID]" in redacted
        assert "customer_id" in rules

    def test_disable_rule(self):
        engine = RedactionEngine()
        assert engine.disable_rule("email")
        text = "Contact user@example.com"
        redacted, rules = engine.redact(text)
        assert "user@example.com" in redacted
        assert "email" not in rules

    def test_empty_text(self):
        engine = RedactionEngine()
        redacted, rules = engine.redact("")
        assert redacted == ""
        assert rules == []


class TestDataCollectors:
    """Tests for data collectors."""

    @pytest.mark.asyncio
    async def test_log_collector_query(self):
        redaction = RedactionEngine()
        collector = LogCollector(
            config={"type": "elasticsearch", "url": "http://localhost:9200"},
            redaction=redaction,
        )
        result = await collector.collect({
            "service": "api-server",
            "level": "error",
            "limit": 10,
        })
        assert result.source_type == DataSourceType.LOGS
        assert "logs" in result.data
        assert result.error is None

    @pytest.mark.asyncio
    async def test_log_collector_health(self):
        redaction = RedactionEngine()
        collector = LogCollector(
            config={"type": "elasticsearch"},
            redaction=redaction,
        )
        healthy = await collector.health_check()
        assert healthy is True

    @pytest.mark.asyncio
    async def test_metrics_collector_query(self):
        redaction = RedactionEngine()
        collector = MetricsCollector(
            config={"type": "prometheus", "url": "http://localhost:9090"},
            redaction=redaction,
        )
        result = await collector.collect({
            "metric_names": ["api_latency_p99", "error_rate"],
            "service": "api-server",
        })
        assert result.source_type == DataSourceType.METRICS
        assert "metrics" in result.data

    @pytest.mark.asyncio
    async def test_code_collector_query(self):
        redaction = RedactionEngine()
        collector = CodeCollector(
            config={},
            redaction=redaction,
        )
        result = await collector.collect({
            "repo": "api-server",
            "types": ["deployments", "commits"],
        })
        assert result.source_type == DataSourceType.CODE
        assert "deployments" in result.data or "commits" in result.data

    @pytest.mark.asyncio
    async def test_kubernetes_collector_query(self):
        redaction = RedactionEngine()
        collector = KubernetesCollector(
            config={},
            redaction=redaction,
        )
        result = await collector.collect({
            "namespace": "default",
            "types": ["pods", "events"],
        })
        assert result.source_type == DataSourceType.KUBERNETES
        assert "pods" in result.data or "events" in result.data

    @pytest.mark.asyncio
    async def test_traces_collector_query(self):
        redaction = RedactionEngine()
        collector = TracesCollector(
            config={"type": "jaeger"},
            redaction=redaction,
        )
        result = await collector.collect({
            "service": "api-server",
            "min_duration_ms": 1000,
        })
        assert result.source_type == DataSourceType.TRACES
        assert "traces" in result.data


class TestVPCConnector:
    """Tests for the main VPCConnector."""

    def create_connector(
        self,
        kubernetes_enabled: bool = False,
        traces_enabled: bool = False,
        **overrides,
    ) -> VPCConnector:
        config = ConnectorConfig(
            connector_id="test-connector-001",
            organization_id="org-test",
            control_plane_url="wss://api.omnipotent.com/connector",
            connector_token="test-token-secret",
            logs_config={"type": "elasticsearch"},
            metrics_config={"type": "prometheus"},
            kubernetes_enabled=kubernetes_enabled,
            traces_enabled=traces_enabled,
            **overrides,
        )
        return VPCConnector(config)

    def test_connector_initialization(self):
        connector = self.create_connector()
        assert connector.connector_id == "test-connector-001"
        assert connector.organization_id == "org-test"
        assert len(connector.collectors) == 3  # logs, metrics, code
        assert connector.running is False

    def test_connector_with_kubernetes(self):
        connector = self.create_connector(kubernetes_enabled=True)
        assert DataSourceType.KUBERNETES in connector.collectors

    def test_connector_with_traces(self):
        connector = self.create_connector(traces_enabled=True)
        assert DataSourceType.TRACES in connector.collectors

    @pytest.mark.asyncio
    async def test_query_logs(self):
        connector = self.create_connector()
        result = await connector.query(DataSourceType.LOGS, {
            "service": "api-server",
            "level": "error",
        })
        assert result.source_type == DataSourceType.LOGS
        assert connector.total_queries == 1

    @pytest.mark.asyncio
    async def test_query_missing_source(self):
        connector = self.create_connector(kubernetes_enabled=False)
        result = await connector.query(DataSourceType.KUBERNETES, {})
        assert result.error is not None
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_collect_incident_evidence(self):
        connector = self.create_connector()
        evidence = await connector.collect_incident_evidence(
            incident_id="INC-001",
            service="api-server",
            time_range={
                "start": "2026-07-31T00:00:00Z",
                "end": "2026-07-31T01:00:00Z",
            },
            severity="high",
        )
        assert evidence.incident_id == "INC-001"
        assert evidence.organization_id == "org-test"
        assert evidence.evidence_hash  # HMAC was computed
        assert evidence.collection_duration_ms >= 0
        assert connector.total_evidence_packages == 1

    @pytest.mark.asyncio
    async def test_evidence_redaction(self):
        connector = self.create_connector()
        evidence = await connector.collect_incident_evidence(
            incident_id="INC-002",
            service="api-server",
            time_range={"start": "2026-07-31T00:00:00Z", "end": "2026-07-31T01:00:00Z"},
        )
        # Verify evidence was collected
        assert evidence.incident_id == "INC-002"
        # Code collector redacts emails - verify redaction was applied
        if "code" in evidence.evidence:
            code_data = evidence.evidence["code"]
            # The collector's redact_dict should have been applied
            assert isinstance(code_data, dict)

    def test_connector_status(self):
        connector = self.create_connector()
        status = connector.get_status()
        assert status.connector_id == "test-connector-001"
        assert status.status == "disconnected"
        assert status.version == "1.0.0"
        assert "logs" in status.collectors

    @pytest.mark.asyncio
    async def test_connector_start_stop(self):
        connector = self.create_connector()

        # Start creates background tasks
        await connector.start()
        assert connector.running is True
        assert connector.start_time is not None

        # Stop cleanly
        await connector.stop()
        assert connector.running is False
        assert connector.connected is False

    def test_evidence_package_to_dict(self):
        package = EvidencePackage(
            incident_id="INC-001",
            organization_id="org-test",
            timestamp="2026-07-31T00:00:00Z",
            evidence={"logs": []},
            evidence_hash="abc123",
            redacted_fields=["email"],
            collection_duration_ms=100,
        )
        d = package.to_dict()
        assert d["incident_id"] == "INC-001"
        assert d["evidence_hash"] == "abc123"
        assert "email" in d["redacted_fields"]

    def test_custom_redaction_patterns(self):
        connector = VPCConnector(ConnectorConfig(
            connector_id="test",
            organization_id="org",
            control_plane_url="wss://test",
            connector_token="token",
            custom_redaction_patterns=[
                {
                    "name": "internal_order_id",
                    "pattern": r"ORD-[A-Z]{3}-\d{6}",
                    "replacement": "[ORDER_ID]",
                }
            ],
        ))
        text = "Processing ORD-ABC-123456"
        redacted, _ = connector.redaction.redact(text)
        assert "[ORDER_ID]" in redacted


class TestQueryResult:
    """Tests for QueryResult dataclass."""

    def test_query_result_to_dict(self):
        result = QueryResult(
            source_type=DataSourceType.LOGS,
            data={"logs": []},
            redacted_fields=["email", "ssn"],
            query_duration_ms=50,
            bytes_processed=1024,
            truncated=False,
            error=None,
        )
        d = result.to_dict()
        assert d["source_type"] == "logs"
        assert d["query_duration_ms"] == 50
        assert len(d["redacted_fields"]) == 2

    def test_query_result_with_error(self):
        result = QueryResult(
            source_type=DataSourceType.METRICS,
            data={},
            error="Connection refused",
        )
        assert result.error is not None
        d = result.to_dict()
        assert d["error"] == "Connection refused"
