"""Tests for competitive parity features.

Tests for Auto Postmortems, Runbook Automation, Multi-Incident Correlation,
Service Dependency Map, and Log Intelligence modules.
"""

from datetime import datetime, timedelta, timezone

import pytest


class TestPostmortemCounterfactual:
    """Tests for counterfactual analysis."""

    def test_analyze_null_check(self) -> None:
        from app.postmortem.counterfactual import get_counterfactual_analyzer

        analyzer = get_counterfactual_analyzer()

        incident_data = {
            "triggered_by_deploy": True,
            "time_to_detect_minutes": 5,
        }

        results = analyzer.analyze(
            incident_data=incident_data,
            root_cause="NullPointerException: payment_method.charge() called on None",
            duration_minutes=18,
            customers_affected=412,
            revenue_impact=4521.0,
        )

        assert len(results) > 0

        types = [r.counterfactual_type.value for r in results]
        assert "test_coverage" in types or "type_checking" in types

    def test_build_causal_graph(self) -> None:
        from app.postmortem.counterfactual import get_counterfactual_analyzer

        analyzer = get_counterfactual_analyzer()

        incident_data = {
            "trigger": "Deploy #47",
            "error_count": 847,
            "customers_affected": 412,
            "revenue_impact": 4521,
            "duration_minutes": 18,
            "impact": "Checkout unavailable",
        }

        graph = analyzer.build_causal_graph(
            incident_data=incident_data,
            root_cause="Null check removed in refactoring",
            contributing_factors=[
                "Code review missed the issue",
                "Staging environment lacks realistic data",
            ],
        )

        assert graph.trigger is not None
        assert graph.root_cause is not None
        assert len(graph.factors) >= 3


class TestPostmortemActionItems:
    """Tests for action item prioritization."""

    def test_generate_action_items(self) -> None:
        from app.postmortem.action_items import get_action_prioritizer

        prioritizer = get_action_prioritizer()

        incident_data = {
            "severity": "SEV2",
            "triggered_by_deploy": True,
            "time_to_detect_minutes": 5,
        }

        result = prioritizer.generate_action_items(
            incident_data=incident_data,
            root_cause="NoneType has no attribute 'charge'",
        )

        assert len(result.action_items) > 0
        assert result.total_effort_hours > 0

        priorities = [a.priority.value for a in result.action_items]
        assert "high" in priorities or "critical" in priorities

    def test_quick_wins_identified(self) -> None:
        from app.postmortem.action_items import get_action_prioritizer

        prioritizer = get_action_prioritizer()

        result = prioritizer.generate_action_items(
            incident_data={"severity": "SEV2"},
            root_cause="Null pointer in checkout flow",
        )

        quick_wins = [a for a in result.action_items if a.effort_hours <= 2]
        assert len(quick_wins) > 0


class TestPostmortemQuality:
    """Tests for postmortem quality checking."""

    def test_check_quality_complete(self) -> None:
        from app.postmortem.quality import get_quality_checker

        checker = get_quality_checker()

        postmortem = {
            "timeline": [
                {"timestamp": "2026-08-05T14:01:00Z", "title": "Deploy started", "event_type": "start"},
                {"timestamp": "2026-08-05T14:03:00Z", "title": "Incident detected", "event_type": "detection"},
                {"timestamp": "2026-08-05T14:18:00Z", "title": "Incident resolved", "event_type": "resolution"},
            ],
            "root_cause": "Null check was removed during refactoring because the code reviewer missed the edge case",
            "contributing_factors": ["Code review gap", "Staging data mismatch"],
            "impact": "412 customers affected with $4,521 in transactions delayed",
            "customers_affected": 412,
            "action_items": [
                {"title": "Add null check test", "owner": "carol", "due_date": "2026-08-10"},
                {"title": "Enable Mypy strict", "owner": "dave", "due_date": "2026-08-15"},
            ],
            "lessons_learned": ["Need better test coverage for edge cases"],
            "things_that_went_well": ["Fast detection"],
        }

        score = checker.check_quality(postmortem)

        assert score.total_score > 0
        assert score.max_score > 0
        assert score.grade in ["A", "B", "C", "D", "F"]

    def test_detect_blame_language(self) -> None:
        from app.postmortem.quality import get_quality_checker

        checker = get_quality_checker()

        postmortem = {
            "summary": "Carol's commit removed the null check",
            "root_cause": "The code review failed to catch the issue",
        }

        score = checker.check_quality(postmortem)

        assert len(score.blame_language_issues) > 0
        assert score.blame_language_issues[0].suggested_rewrite != ""


class TestPostmortemNarrative:
    """Tests for narrative generation."""

    def test_generate_narratives(self) -> None:
        from app.postmortem.narrative import get_narrative_generator

        generator = get_narrative_generator()

        incident_data = {
            "title": "Checkout API 500 Errors",
            "duration_minutes": 18,
            "customers_affected": 412,
            "revenue_impact": 4521,
            "affected_services": ["checkout-api"],
            "started_at": "2026-08-05T14:01:00Z",
            "resolved_at": "2026-08-05T14:18:00Z",
            "trigger": "Deploy #47",
        }

        narratives = generator.generate_narratives(
            incident_data=incident_data,
            root_cause="Null check removed",
            timeline=[],
            action_items=[],
        )

        assert narratives.executive_summary.headline != ""
        assert narratives.technical_narrative.what_happened != ""
        assert narratives.customer_communication.subject != ""
        assert narratives.slack_update.title != ""


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


class TestServiceGraph:
    """Tests for service dependency graph."""

    def test_add_services_and_connections(self) -> None:
        from app.service_map.graph import ServiceGraph
        from app.service_map.models import ConnectionType

        graph = ServiceGraph()

        graph.add_service("api-gateway", "API Gateway", tier="tier0")
        graph.add_service("checkout-api", "Checkout API", tier="tier1")
        graph.add_service("billing-db", "Billing Database", tier="tier1")

        graph.add_connection("api-gateway", "checkout-api", ConnectionType.SYNC, is_critical=True)
        graph.add_connection("checkout-api", "billing-db", ConnectionType.DATABASE, is_critical=True)

        viz = graph.get_visualization_data()

        assert viz["total_services"] == 3
        assert viz["total_connections"] == 2

    def test_calculate_blast_radius(self) -> None:
        from app.service_map.graph import ServiceGraph
        from app.service_map.models import ConnectionType

        graph = ServiceGraph()

        graph.add_service("billing-db", "Billing Database", tier="tier0")
        graph.add_service("payment-service", "Payment Service", tier="tier1")
        graph.add_service("checkout-api", "Checkout API", tier="tier1")

        graph.add_connection("payment-service", "billing-db", ConnectionType.DATABASE)
        graph.add_connection("checkout-api", "payment-service", ConnectionType.SYNC)

        blast = graph.calculate_blast_radius("billing-db")

        assert blast.source_service == "billing-db"
        assert blast.total_services_affected >= 1

    def test_analyze_change_impact(self) -> None:
        from app.service_map.graph import ServiceGraph
        from app.service_map.models import ConnectionType

        graph = ServiceGraph()

        graph.add_service("payment-service", "Payment Service", tier="tier1")
        graph.add_service("checkout-api", "Checkout API", tier="tier1")

        graph.add_connection("checkout-api", "payment-service", ConnectionType.SYNC)

        impact = graph.analyze_change_impact(
            service_id="payment-service",
            change_description="Upgrade Stripe SDK to v2.0",
            is_breaking_change=True,
        )

        assert impact.target_service == "payment-service"
        assert impact.risk_level is not None
        assert len(impact.recommendations) > 0


class TestFailureSimulator:
    """Tests for failure simulation."""

    def test_simulate_failure(self) -> None:
        from app.service_map.graph import ServiceGraph
        from app.service_map.models import ConnectionType
        from app.service_map.simulation import FailureSimulator

        graph = ServiceGraph()

        graph.add_service("database", "Main Database", tier="tier0")
        graph.add_service("api", "API Service", tier="tier1")
        graph.add_service("web", "Web Frontend", tier="tier2")

        graph.add_connection("api", "database", ConnectionType.DATABASE)
        graph.add_connection("web", "api", ConnectionType.SYNC)

        simulator = FailureSimulator(graph)

        scenario = simulator.simulate_failure("database", "complete_outage")

        assert scenario.failed_service == "database"
        assert scenario.failure_mode == "complete_outage"
        assert len(scenario.mitigation_recommendations) > 0

    def test_chaos_test_plan(self) -> None:
        from app.service_map.graph import ServiceGraph
        from app.service_map.simulation import FailureSimulator

        graph = ServiceGraph()
        graph.add_service("service-a", "Service A", tier="tier1")
        graph.add_service("service-b", "Service B", tier="tier1")

        simulator = FailureSimulator(graph)

        plan = simulator.chaos_test_plan(target_services=["service-a"])

        assert "plan_id" in plan
        assert len(plan["phases"]) > 0


class TestLogAnalyzer:
    """Tests for log analysis."""

    def test_ingest_and_summarize(self) -> None:
        from app.log_intelligence.analyzer import get_log_analyzer
        from app.log_intelligence.models import LogEntry, LogLevel

        analyzer = get_log_analyzer()
        now = datetime.now(timezone.utc)

        for i in range(10):
            analyzer.ingest_log(LogEntry(
                log_id=f"log_{i}",
                timestamp=now - timedelta(minutes=i),
                level=LogLevel.ERROR if i < 3 else LogLevel.INFO,
                service="checkout-api",
                message=f"NoneType has no attribute 'charge' - request {i}",
            ))

        summary = analyzer.summarize(time_range_minutes=60)

        assert summary.total_logs == 10
        assert summary.error_count == 3

    def test_query_logs(self) -> None:
        from app.log_intelligence.analyzer import get_log_analyzer
        from app.log_intelligence.models import LogEntry, LogLevel, LogQuery

        analyzer = get_log_analyzer()
        now = datetime.now(timezone.utc)

        analyzer.ingest_log(LogEntry(
            log_id="log_query_1",
            timestamp=now,
            level=LogLevel.ERROR,
            service="payment-service",
            message="Payment failed: card declined",
        ))

        query = LogQuery(
            query="payment failed",
            services=["payment-service"],
            levels=[LogLevel.ERROR],
        )

        results = analyzer.query(query)

        assert len(results) >= 1


class TestErrorClusterer:
    """Tests for error clustering."""

    def test_cluster_errors(self) -> None:
        from app.log_intelligence.clustering import get_error_clusterer
        from app.log_intelligence.models import LogEntry, LogLevel

        clusterer = get_error_clusterer()
        now = datetime.now(timezone.utc)

        logs = [
            LogEntry(
                log_id=f"err_{i}",
                timestamp=now - timedelta(minutes=i),
                level=LogLevel.ERROR,
                service="api-service",
                message=f"NullPointerException: user {i} has no attribute 'charge'",
            )
            for i in range(5)
        ]

        logs.extend([
            LogEntry(
                log_id=f"timeout_{i}",
                timestamp=now - timedelta(minutes=i),
                level=LogLevel.ERROR,
                service="api-service",
                message=f"Connection timeout after {30+i}s",
            )
            for i in range(3)
        ])

        clusters = clusterer.cluster(logs)

        assert len(clusters) >= 1
        assert sum(c.count for c in clusters) == len(logs)


class TestCodeLinker:
    """Tests for log-to-code linking."""

    def test_link_from_stack_trace(self) -> None:
        from app.log_intelligence.code_linking import get_code_linker
        from app.log_intelligence.models import LogEntry, LogLevel

        linker = get_code_linker()

        entry = LogEntry(
            log_id="log_stack",
            timestamp=datetime.now(timezone.utc),
            level=LogLevel.ERROR,
            service="checkout-api",
            message='''AttributeError: 'NoneType' object has no attribute 'charge'
File "/app/billing/checkout.py", line 142, in process_payment
    payment_method.charge(amount)''',
        )

        link = linker.link_log(entry)

        assert link is not None
        assert link.location.file_path == "/app/billing/checkout.py"
        assert link.location.line_number == 142
        assert link.link_type == "stack_trace"


class TestHealthOverlay:
    """Tests for health overlay."""

    def test_record_and_get_trend(self) -> None:
        from app.service_map.health import get_health_overlay
        from app.service_map.models import HealthStatus, ServiceHealth

        overlay = get_health_overlay()

        for i in range(10):
            health = ServiceHealth(
                status=HealthStatus.HEALTHY,
                error_rate=0.01 + (i * 0.005),
                latency_p99_ms=100 + (i * 10),
                throughput_rps=1000,
            )
            overlay.record_snapshot("test-service", health)

        trend = overlay.get_trend("test-service")

        assert trend is not None
        assert trend.service_id == "test-service"
        assert trend.error_rate_trend in ["stable", "increasing", "decreasing"]

    def test_health_alerts(self) -> None:
        from app.service_map.health import get_health_overlay
        from app.service_map.models import HealthStatus, ServiceHealth

        overlay = get_health_overlay()

        health = ServiceHealth(
            status=HealthStatus.FAILING,
            error_rate=0.10,
            latency_p99_ms=3000,
            throughput_rps=500,
        )

        overlay.record_snapshot("failing-service", health)

        alerts = overlay.get_recent_alerts(service_id="failing-service")

        assert len(alerts) > 0
