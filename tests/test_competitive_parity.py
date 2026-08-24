"""Tests for competitive parity features.

Tests for Auto Postmortems, Runbook Automation, Multi-Incident Correlation,
Service Dependency Map, and Log Intelligence modules.
"""

from datetime import datetime, timedelta, timezone



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
