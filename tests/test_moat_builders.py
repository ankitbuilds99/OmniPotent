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
