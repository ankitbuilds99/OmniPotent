"""Tests for Moat Builder features."""

from datetime import datetime, timedelta, timezone

from app.integrations_advanced.correlation import (
    CorrelatedEvent,
    CrossIntegrationCorrelator,
    EventType,
)
from app.integrations_advanced.oauth import (
    OAuthManager,
    OAuthProvider,
)
from app.integrations_advanced.health import (
    HealthStatus,
    IntegrationHealthMonitor,
    IssueType,
)
from app.integrations_advanced.bidirectional import (
    BidirectionalSync,
    SyncActionType,
)
from app.integrations_advanced.sdk import (
    IntegrationCapabilities,
    IntegrationCategory,
    IntegrationSDK,
)
from app.playbook_marketplace.catalog import (
    PlaybookCatalog,
)
from app.playbook_marketplace.ratings import (
    RatingsEngine,
)
from app.playbook_marketplace.publishing import (
    PublishingManager,
)
from app.playbook_marketplace.adaptation import (
    StackAdapter,
)
from app.playbook_marketplace.ai_generator import (
    AIPlaybookGenerator,
    GenerationRequest,
)
from app.vscode_extension.annotations import (
    AnnotationService,
    AnnotationSeverity,
)
from app.vscode_extension.code_linking import (
    CodeLinkingService,
)
from app.vscode_extension.pr_risk import (
    PRRiskPreview,
)
from app.vscode_extension.commands import (
    CommandPalette,
)
from app.chaos_engineering.recommendations import (
    ChaosRecommendationEngine,
)
from app.chaos_engineering.experiments import (
    ExperimentRunner,
)
from app.chaos_engineering.gates import (
    PreDeployChaosGate,
    GateStatus,
)
from app.chaos_engineering.blast_radius import (
    BlastRadiusMonitor,
)
from app.cost_tracking.calculator import (
    CostCalculator,
)
from app.cost_tracking.revenue import (
    RevenueImpactCalculator,
)
from app.cost_tracking.executive_reports import (
    ExecutiveReportGenerator,
    ReportPeriod,
)
from app.cost_tracking.team_attribution import (
    CostAttributionEngine,
)


class TestCrossIntegrationCorrelation:
    def test_ingest_event(self):
        correlator = CrossIntegrationCorrelator()

        event = CorrelatedEvent(
            event_id="evt1",
            integration="github",
            event_type=EventType.PR_MERGED,
            timestamp=datetime.now(timezone.utc),
            title="PR #847 merged",
        )

        correlator.ingest_event("org1", event)

        chains = correlator.correlate_events("org1", time_window_hours=1)
        assert isinstance(chains, list)

    def test_correlate_deploy_to_alert(self):
        correlator = CrossIntegrationCorrelator()
        now = datetime.now(timezone.utc)

        deploy = CorrelatedEvent(
            event_id="deploy1",
            integration="argocd",
            event_type=EventType.DEPLOY,
            timestamp=now,
            title="Deploy #47",
        )

        alert = CorrelatedEvent(
            event_id="alert1",
            integration="datadog",
            event_type=EventType.ALERT,
            timestamp=now + timedelta(minutes=5),
            title="Error rate spike",
        )

        correlator.ingest_event("org1", deploy)
        correlator.ingest_event("org1", alert)

        chains = correlator.correlate_events("org1", time_window_hours=1)
        assert len(chains) >= 1


class TestOAuthManager:
    def test_get_providers(self):
        manager = OAuthManager()

        providers = manager.get_providers()
        assert len(providers) > 0
        assert any(p["provider"] == "datadog" for p in providers)

    def test_start_flow(self):
        manager = OAuthManager()

        result = manager.start_flow(
            OAuthProvider.GITHUB,
            "org1",
            "user1",
            "https://app.example.com/callback",
        )

        assert "flow_id" in result
        assert "authorize_url" in result
        assert result["flow_id"].startswith("flow_")

    def test_complete_flow(self):
        manager = OAuthManager()

        flow = manager.start_flow(
            OAuthProvider.SLACK,
            "org1",
            "user1",
            "https://callback",
        )

        result = manager.complete_flow(flow["state"], "auth_code_123")
        assert result["success"] is True
        assert manager.is_connected("org1", OAuthProvider.SLACK)


class TestIntegrationHealth:
    def test_record_check(self):
        monitor = IntegrationHealthMonitor()

        check = monitor.record_check(
            "org1",
            "datadog",
            HealthStatus.HEALTHY,
            response_time_ms=150,
        )

        assert check.status == HealthStatus.HEALTHY

    def test_health_degraded(self):
        monitor = IntegrationHealthMonitor()

        monitor.record_check("org1", "jira", HealthStatus.DEGRADED, issue_type=IssueType.RATE_LIMIT)

        health = monitor.get_health("org1", "jira")
        assert health is not None
        assert health.status == HealthStatus.DEGRADED
        assert IssueType.RATE_LIMIT in health.current_issues

    def test_recovery_actions(self):
        monitor = IntegrationHealthMonitor()

        monitor.record_check("org1", "jira", HealthStatus.DEGRADED, issue_type=IssueType.RATE_LIMIT)

        actions = monitor.get_recovery_actions("org1", "jira")
        assert len(actions) > 0


class TestBidirectionalSync:
    def test_get_config(self):
        sync = BidirectionalSync()

        config = sync.get_config("jira")
        assert config is not None
        assert len(config.write_capabilities) > 0

    def test_queue_action(self):
        sync = BidirectionalSync()

        action = sync.queue_action(
            "org1",
            "jira",
            SyncActionType.CREATE_TICKET,
            {"title": "Bug from incident"},
            "auto",
        )

        assert action.action_type == SyncActionType.CREATE_TICKET
        pending = sync.get_pending_actions("org1")
        assert len(pending) > 0


class TestIntegrationSDK:
    def test_create_integration(self):
        sdk = IntegrationSDK()

        manifest = sdk.create_integration(
            name="Custom Tool",
            version="1.0.0",
            description="Custom monitoring integration",
            author="dev@company.com",
            category=IntegrationCategory.MONITORING,
            capabilities=IntegrationCapabilities(logs=True, metrics=True),
        )

        assert manifest.integration_id == "custom-tool"
        assert manifest.capabilities.logs is True

    def test_get_marketplace(self):
        sdk = IntegrationSDK()

        marketplace = sdk.get_marketplace()
        assert len(marketplace) >= 1


class TestPlaybookCatalog:
    def test_search_playbooks(self):
        catalog = PlaybookCatalog()

        results = catalog.search_playbooks("kubernetes")
        assert len(results) >= 1

    def test_get_featured(self):
        catalog = PlaybookCatalog()

        featured = catalog.get_featured(limit=3)
        assert len(featured) >= 1
        assert all(p.is_verified for p in featured)

    def test_install_playbook(self):
        catalog = PlaybookCatalog()

        result = catalog.install_playbook("org1", "k8s-oom-recovery")
        assert result["success"] is True

        installed = catalog.get_installed("org1")
        assert len(installed) >= 1


class TestRatingsEngine:
    def test_submit_rating(self):
        engine = RatingsEngine()

        rating = engine.submit_rating(
            "k8s-oom-recovery",
            "user1",
            "org1",
            stars=5,
            title="Great playbook!",
        )

        assert rating.stars == 5

        summary = engine.get_summary("k8s-oom-recovery")
        assert summary.total_reviews >= 1


class TestPublishingManager:
    def test_register_publisher(self):
        manager = PublishingManager()

        publisher = manager.register_publisher(
            "user1",
            "John Doe",
            "john@example.com",
        )

        assert publisher.publisher_id == "user1"

    def test_revenue_tracking(self):
        manager = PublishingManager()
        manager.register_publisher("user1", "John", "john@ex.com")
        manager.assign_playbook("playbook1", "user1")

        record = manager.record_revenue(
            "playbook1",
            "org1",
            29.0,
            datetime.now(timezone.utc),
            datetime.now(timezone.utc) + timedelta(days=30),
        )

        assert record is not None
        assert record.publisher_share == 29.0 * 0.7


class TestStackAdapter:
    def test_detect_stack(self):
        adapter = StackAdapter()

        services = adapter.detect_stack("org1", ["kubernetes", "datadog"])
        assert len(services) >= 1

    def test_adapt_playbook(self):
        adapter = StackAdapter()
        adapter.detect_stack("org1", ["kubernetes", "datadog", "slack"])

        result = adapter.adapt_playbook(
            "org1",
            "test-playbook",
            [{"name": "NAMESPACE", "type": "string"}],
            ["kubernetes"],
        )

        assert result.compatible is True
        assert len(result.variable_mappings) >= 1


class TestAIPlaybookGenerator:
    def test_generate_playbook(self):
        generator = AIPlaybookGenerator()

        request = GenerationRequest(
            request_id="req1",
            user_id="user1",
            org_id="org1",
            description="Create a runbook for webhook endpoint failures with retries",
        )

        result = generator.generate(request)
        assert result.success is True
        assert result.playbook is not None
        assert len(result.playbook.steps) >= 1


class TestAnnotationService:
    def test_add_annotation(self):
        service = AnnotationService()

        annotation = service.add_annotation(
            file_path="app/checkout.py",
            line_number=142,
            incident_id="INC-5521",
            incident_title="Checkout 500 errors",
            severity=AnnotationSeverity.ACTIVE,
            users_affected=412,
        )

        assert annotation.incident_id == "INC-5521"

    def test_get_annotations(self):
        service = AnnotationService()
        service.add_annotation(
            "app/checkout.py", 142, "INC-5521", "Error", AnnotationSeverity.ACTIVE
        )

        file_anns = service.get_annotations("app/checkout.py")
        assert len(file_anns.annotations) >= 1


class TestCodeLinking:
    def test_create_link(self):
        service = CodeLinkingService()

        link = service.create_link(
            file_path="app/payment.py",
            line_start=100,
            line_end=105,
            incident_id="INC-5521",
            incident_title="Payment failure",
        )

        assert link.incident_id == "INC-5521"

    def test_get_risk_profile(self):
        service = CodeLinkingService()
        service.create_link("app/checkout.py", 140, 145, "INC-1", "Error 1")
        service.create_link("app/checkout.py", 150, 155, "INC-2", "Error 2")

        profile = service.get_risk_profile("app/checkout.py")
        assert profile.total_incidents == 2


class TestPRRiskPreview:
    def test_analyze_changes(self):
        preview = PRRiskPreview()

        analysis = preview.analyze_changes([
            {
                "path": "app/checkout.py",
                "additions": ["def process_payment():"],
                "deletions": ["if payment_method is None:"],
            }
        ])

        assert analysis.total_score > 0
        assert len(analysis.factors) >= 1

    def test_quick_check(self):
        preview = PRRiskPreview()

        result = preview.quick_check(["app/payment/checkout.py"])
        assert "status" in result
        assert "score" in result


class TestCommandPalette:
    def test_get_commands(self):
        palette = CommandPalette()

        commands = palette.get_commands()
        assert len(commands) > 0

    def test_execute_command(self):
        palette = CommandPalette()

        result = palette.execute("investigateLastError")
        assert result.success is True


class TestChaosRecommendations:
    def test_record_incident(self):
        engine = ChaosRecommendationEngine()

        engine.record_incident(
            "org1",
            "INC-1",
            "Redis timeout",
            "Connection pool exhaustion",
            ["redis", "checkout"],
        )

        recommendations = engine.get_recommendations("org1")
        assert len(recommendations) >= 1

    def test_get_recommendations(self):
        engine = ChaosRecommendationEngine()
        engine.record_incident("org1", "INC-1", "Timeout", "latency spike", ["api"])

        recs = engine.get_recommendations("org1")
        assert len(recs) >= 1


class TestExperimentRunner:
    def test_create_experiment(self):
        runner = ExperimentRunner()

        experiment = runner.create_experiment(
            name="Latency Test",
            description="Inject latency",
            hypothesis="Circuit breaker activates",
            target_service="payment-service",
            config={"latency_ms": 5000},
        )

        assert experiment.name == "Latency Test"

    def test_start_experiment(self):
        runner = ExperimentRunner()
        exp = runner.create_experiment(
            "Test", "Desc", "Hyp", "service", {}
        )

        result = runner.start_experiment(exp.experiment_id)
        assert result.status.value == "running"


class TestChaosGate:
    def test_evaluate_gate(self):
        gate = PreDeployChaosGate()

        result = gate.evaluate_gate("staging", "deploy-123")
        assert result.status == GateStatus.RUNNING
        assert len(result.tests) >= 1

    def test_update_test_status(self):
        gate = PreDeployChaosGate()
        result = gate.evaluate_gate("staging", "deploy-456")

        test_id = result.tests[0].test_id
        gate.update_test_status("deploy-456", test_id, GateStatus.PASSED)

        status = gate.get_gate_status("deploy-456")
        assert status["tests"][0]["status"] == "passed"


class TestBlastRadius:
    def test_start_monitoring(self):
        monitor = BlastRadiusMonitor()

        monitor.start_monitoring("exp1", "checkout-api")

        status = monitor.check_blast_radius("exp1")
        assert status.experiment_id == "exp1"

    def test_check_containment(self):
        monitor = BlastRadiusMonitor()
        monitor.start_monitoring("exp2", "payment-service")

        status = monitor.check_blast_radius("exp2")
        assert isinstance(status.is_contained, bool)
        assert len(status.metrics) >= 1


class TestCostCalculator:
    def test_start_tracking(self):
        calc = CostCalculator()

        incident = calc.start_tracking("INC-1", "Checkout failure", "SEV2")
        assert incident.incident_id == "INC-1"
        assert incident.is_ongoing is True

    def test_add_engineer_time(self):
        calc = CostCalculator()
        calc.start_tracking("INC-2", "API timeout", "SEV2")

        calc.add_engineer_time("INC-2", "eng1", "Alice", "sre", 2.0)

        incident = calc.get_incident_cost("INC-2")
        assert incident is not None
        assert incident.breakdown.engineer_time > 0

    def test_live_cost(self):
        calc = CostCalculator()
        calc.start_tracking("INC-3", "DB failure", "SEV1")
        calc.add_engineer_time("INC-3", "eng1", "Bob", "senior_engineer", 1.5)

        live = calc.get_live_cost("INC-3")
        assert live["total_cost"] > 0


class TestRevenueImpact:
    def test_calculate_full_impact(self):
        calc = RevenueImpactCalculator()

        impact = calc.calculate_full_impact(
            incident_id="INC-1",
            lost_transactions=384,
            avg_transaction_value=127,
            affected_customers=5000,
            incident_severity="SEV2",
            social_mentions=4,
        )

        assert impact.total_impact > 0
        assert impact.immediate_loss > 0


class TestExecutiveReports:
    def test_generate_report(self):
        generator = ExecutiveReportGenerator()

        now = datetime.now(timezone.utc)
        report = generator.generate_report(
            period=ReportPeriod.MONTHLY,
            period_start=now - timedelta(days=30),
            period_end=now,
            incident_data=[
                {"cost": 50000, "duration_minutes": 60, "service": "checkout"},
                {"cost": 30000, "duration_minutes": 45, "service": "payment"},
            ],
            service_costs={"checkout": 50000, "payment": 30000},
        )

        assert report.incident_count == 2
        assert report.total_cost == 80000
        assert report.roi.roi_multiple > 0


class TestCostAttribution:
    def test_attribute_incident(self):
        engine = CostAttributionEngine()

        engine.attribute_incident(
            incident_id="INC-1",
            service="checkout-api",
            team="platform",
            cost=50000,
            engineer_cost=15000,
            revenue_impact=30000,
        )

        teams = engine.get_team_costs()
        assert len(teams) >= 1

        services = engine.get_service_costs()
        assert len(services) >= 1

    def test_summary_dashboard(self):
        engine = CostAttributionEngine()
        engine.attribute_incident("INC-1", "api", "backend", 40000)
        engine.attribute_incident("INC-2", "web", "frontend", 20000)

        summary = engine.get_summary_dashboard()
        assert summary["totals"]["cost"] == 60000
        assert summary["totals"]["incidents"] == 2
