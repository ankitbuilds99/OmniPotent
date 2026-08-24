"""Tests for competitive parity features.

Tests for Auto Postmortems, Runbook Automation, Multi-Incident Correlation,
Service Dependency Map, and Log Intelligence modules.
"""

from datetime import datetime, timedelta, timezone

import pytest


class TestRunbookEngine:
    """Tests for runbook execution engine."""

    @pytest.mark.asyncio
    async def test_execute_runbook(self) -> None:
        from app.runbook.builder import get_runbook_builder
        from app.runbook.engine import get_runbook_engine
        from app.runbook.models import ExecutionContext

        builder = get_runbook_builder()
        engine = get_runbook_engine()

        runbook = builder.build_from_template("redis_memory")
        assert runbook is not None

        context = ExecutionContext(
            dry_run=True,
            variables={"REDIS_HOST": "localhost"},
        )

        result = await engine.execute(runbook, context, dry_run=True)

        assert result.execution.status in ["completed", "failed"]
        assert result.total_duration_seconds >= 0

    def test_build_from_description(self) -> None:
        from app.runbook.builder import get_runbook_builder

        builder = get_runbook_builder()

        runbook = builder.build_from_description(
            description="Handle Redis memory issues with pod restarts",
            trigger="redis_memory > 80%",
        )

        assert runbook is not None
        assert len(runbook.steps) > 0
        assert runbook.trigger == "redis_memory > 80%"


class TestRunbookRollback:
    """Tests for runbook rollback generation."""

    def test_generate_rollback_plan(self) -> None:
        from app.runbook.builder import get_runbook_builder
        from app.runbook.rollback import get_rollback_generator

        builder = get_runbook_builder()
        generator = get_rollback_generator()

        runbook = builder.build_from_template("database_failover")
        assert runbook is not None

        plan = generator.generate_rollback_plan(runbook)

        assert plan.plan_id.startswith("rollback_")
        assert len(plan.steps) > 0

    def test_estimate_rollback_risk(self) -> None:
        from app.runbook.builder import get_runbook_builder
        from app.runbook.rollback import get_rollback_generator

        builder = get_runbook_builder()
        generator = get_rollback_generator()

        runbook = builder.build_from_template("database_failover")
        plan = generator.generate_rollback_plan(runbook)

        risk = generator.estimate_rollback_risk(plan)

        assert risk["overall_risk"] in ["low", "medium", "high", "critical"]
        assert "risk_score" in risk


class TestCorrelationEngine:
    """Tests for multi-incident correlation."""

    def test_correlate_alerts(self) -> None:
        from app.correlation.engine import get_correlation_engine
        from app.correlation.models import Alert, AlertSeverity, AlertStatus

        engine = get_correlation_engine()

        engine.register_dependency("checkout-api", ["payment-service", "billing-db"])
        engine.register_dependency("payment-service", ["billing-db"])

        now = datetime.now(timezone.utc)

        alerts = [
            Alert(
                alert_id="alert_1",
                name="billing-db connection pool exhausted",
                service="billing-db",
                severity=AlertSeverity.CRITICAL,
                status=AlertStatus.FIRING,
                message="Connection pool at 500/500",
                started_at=now,
            ),
            Alert(
                alert_id="alert_2",
                name="payment-service timeouts",
                service="payment-service",
                severity=AlertSeverity.HIGH,
                status=AlertStatus.FIRING,
                message="Connection to billing-db timed out",
                started_at=now + timedelta(seconds=45),
            ),
            Alert(
                alert_id="alert_3",
                name="checkout-api 500 errors",
                service="checkout-api",
                severity=AlertSeverity.HIGH,
                status=AlertStatus.FIRING,
                message="5% error rate, payment failures",
                started_at=now + timedelta(minutes=2),
            ),
        ]

        result = engine.correlate_alerts(alerts)

        assert result.total_alerts == 3
        assert result.correlated_alerts >= 2
        assert result.noise_reduction_percent >= 0


class TestSemanticAnalyzer:
    """Tests for semantic alert analysis."""

    def test_extract_features(self) -> None:
        from app.correlation.models import Alert, AlertSeverity, AlertStatus
        from app.correlation.semantic import get_semantic_analyzer

        analyzer = get_semantic_analyzer()

        alert = Alert(
            alert_id="test_1",
            name="Database connection timeout",
            service="api-service",
            severity=AlertSeverity.HIGH,
            status=AlertStatus.FIRING,
            message="Connection to postgres-db timed out after 30s",
            started_at=datetime.now(timezone.utc),
        )

        features = analyzer.extract_features(alert)

        assert features.error_type != "unknown"
        assert features.affected_resource != "unknown"
        assert len(features.keywords) > 0

    def test_calculate_similarity(self) -> None:
        from app.correlation.models import Alert, AlertSeverity, AlertStatus
        from app.correlation.semantic import get_semantic_analyzer

        analyzer = get_semantic_analyzer()
        now = datetime.now(timezone.utc)

        alert1 = Alert(
            alert_id="test_1",
            name="Database connection timeout",
            service="api-service",
            severity=AlertSeverity.HIGH,
            status=AlertStatus.FIRING,
            message="Connection to postgres-db timed out",
            started_at=now,
        )

        alert2 = Alert(
            alert_id="test_2",
            name="DB connection pool exhausted",
            service="worker-service",
            severity=AlertSeverity.HIGH,
            status=AlertStatus.FIRING,
            message="Connection pool exhausted, timeout waiting",
            started_at=now,
        )

        similarity = analyzer.calculate_similarity(alert1, alert2)

        assert similarity.overall_score >= 0
        assert similarity.overall_score <= 1
