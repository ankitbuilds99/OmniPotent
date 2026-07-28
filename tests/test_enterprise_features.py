"""Tests for enterprise features: SSO/SAML, SOC2, On-Call, Status Page."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal


# SSO/SAML Tests
from app.sso.saml import SAMLConfig, SAMLProvider, SAMLService
from app.sso.abac import (
    ABACCondition,
    ABACPermissionResolver,
    ConditionOperator,
    Permission,
)
from app.sso.session_intelligence import (
    DeviceInfo,
    LocationInfo,
    RiskLevel,
    SessionIntelligence,
)
from app.sso.emergency_bypass import (
    BypassReason,
    BypassStatus,
    EmergencyBypassManager,
)
from app.sso.multi_idp import (
    IdPConfig,
    IdPDiscoveryHint,
    IdPRouter,
    IdPRoutingStrategy,
)

# Compliance Tests
from app.compliance.controls import (
    ComplianceControl,
    ControlFramework,
    ControlStatus,
)
from app.compliance.evidence import EvidenceCollector, EvidenceType
from app.compliance.dashboard import ComplianceDashboard
from app.compliance.trust_portal import (
    QuestionnaireType,
    TrustPortal,
)

# On-Call Tests
from app.oncall.fatigue import FatigueDetector, FatigueLevel, PageEvent
from app.oncall.smart_routing import (
    AvailabilityStatus,
    IncidentRouting,
    SmartRouter,
)
from app.oncall.compensation import CompensationTracker
from app.oncall.swap_marketplace import SwapMarketplace, SwapStatus

# Status Page Tests
from app.status_page.components import ComponentStatus, StatusRegistry
from app.status_page.incidents import (
    IncidentManager,
    IncidentSeverity,
    IncidentStatus,
)
from app.status_page.sla import SLATracker
from app.status_page.updates import (
    AIUpdateGenerator,
    InternalIncidentData,
    UpdateTone,
)
from app.status_page.widgets import WidgetGenerator, WidgetTheme


class TestSAMLService:
    def test_register_idp(self):
        service = SAMLService(
            sp_entity_id="https://app.example.com",
            sp_acs_url="https://app.example.com/saml/acs",
        )

        config = SAMLConfig(
            provider=SAMLProvider.OKTA,
            entity_id="https://okta.example.com",
            sso_url="https://okta.example.com/sso",
        )

        service.register_idp("okta", config)

        assert "okta" in service.list_idps()
        assert service.get_idp("okta") == config

    def test_create_authn_request(self):
        service = SAMLService(
            sp_entity_id="https://app.example.com",
            sp_acs_url="https://app.example.com/saml/acs",
        )

        config = SAMLConfig(
            provider=SAMLProvider.OKTA,
            entity_id="https://okta.example.com",
            sso_url="https://okta.example.com/sso",
        )

        service.register_idp("okta", config)

        redirect_url, request_id = service.create_authn_request("okta")

        assert redirect_url.startswith("https://okta.example.com/sso")
        assert "SAMLRequest" in redirect_url
        assert request_id.startswith("_")

    def test_generate_metadata(self):
        service = SAMLService(
            sp_entity_id="https://app.example.com",
            sp_acs_url="https://app.example.com/saml/acs",
        )

        metadata = service.generate_metadata()

        assert "EntityDescriptor" in metadata
        assert "https://app.example.com" in metadata


class TestABAC:
    def test_condition_equals(self):
        cond = ABACCondition("department", ConditionOperator.EQUALS, "engineering")

        assert cond.evaluate({"department": "engineering"}) is True
        assert cond.evaluate({"department": "sales"}) is False

    def test_condition_in(self):
        cond = ABACCondition("role", ConditionOperator.IN, ["admin", "owner"])

        assert cond.evaluate({"role": "admin"}) is True
        assert cond.evaluate({"role": "viewer"}) is False

    def test_permission_resolver(self):
        resolver = ABACPermissionResolver()

        permissions = resolver.resolve_permissions({
            "department": "engineering",
            "team": "sre",
            "level": "senior",
        })

        assert Permission.VIEW_INCIDENTS in permissions
        assert Permission.APPROVE_RUNBOOKS in permissions
        assert Permission.PRODUCTION_ACCESS in permissions

    def test_explain_permissions(self):
        resolver = ABACPermissionResolver()

        explanation = resolver.explain_permissions({
            "department": "engineering",
        })

        assert "granted_permissions" in explanation
        assert "explanations" in explanation
        assert len(explanation["explanations"]) > 0


class TestSessionIntelligence:
    def test_assess_known_device(self):
        intel = SessionIntelligence()

        device = DeviceInfo(
            user_agent="Mozilla/5.0",
            platform="MacOS",
            browser="Chrome",
        )
        location = LocationInfo(
            ip_address="192.168.1.1",
            country="US",
            city="New York",
        )

        intel.trust_device("user1", device)
        intel.record_login("user1", "session1", device, location)

        score = intel.assess_risk("user1", device, location)

        assert score.device_trusted is True
        assert score.level in (RiskLevel.LOW, RiskLevel.MEDIUM)

    def test_assess_new_device(self):
        intel = SessionIntelligence()

        device1 = DeviceInfo(user_agent="Mozilla/5.0", platform="MacOS")
        device2 = DeviceInfo(user_agent="Chrome/100", platform="Windows")
        location = LocationInfo(ip_address="192.168.1.1", country="US")

        intel.record_login("user1", "session1", device1, location)
        score = intel.assess_risk("user1", device2, location)

        assert score.device_trusted is False
        assert len(score.anomalies) > 0

    def test_record_failed_attempt(self):
        intel = SessionIntelligence()

        for _ in range(5):
            intel.record_failed_attempt("user1")

        device = DeviceInfo(user_agent="Mozilla/5.0")
        location = LocationInfo(ip_address="192.168.1.1")

        score = intel.assess_risk("user1", device, location)

        assert score.factors.get("failed_attempts", 0) > 0


class TestEmergencyBypass:
    def test_generate_code(self):
        bypass = EmergencyBypassManager(secret_key="test-secret")

        code = bypass.generate_code("alice@company.com")

        assert len(code) > 10

    def test_verify_valid_code(self):
        bypass = EmergencyBypassManager(secret_key="test-secret")

        code = bypass.generate_code("alice@company.com")
        status, session = bypass.verify_code(
            "alice@company.com",
            code,
            BypassReason.IDP_UNREACHABLE,
            "192.168.1.1",
            "Mozilla/5.0",
        )

        assert status == BypassStatus.SUCCESS
        assert session is not None
        assert session.user_email == "alice@company.com"

    def test_verify_invalid_code(self):
        bypass = EmergencyBypassManager(secret_key="test-secret")

        status, session = bypass.verify_code(
            "alice@company.com",
            "invalid-code",
            BypassReason.IDP_UNREACHABLE,
            "192.168.1.1",
            "Mozilla/5.0",
        )

        assert status == BypassStatus.INVALID_CODE
        assert session is None


class TestMultiIdP:
    def test_register_idp(self):
        router = IdPRouter()

        config = IdPConfig(
            idp_id="okta",
            name="Okta",
            saml_config=SAMLConfig(
                provider=SAMLProvider.OKTA,
                entity_id="https://okta.example.com",
                sso_url="https://okta.example.com/sso",
            ),
            email_domains=["company.com"],
        )

        router.register_idp(config)

        assert router.get_idp("okta") is not None

    def test_route_by_email_domain(self):
        router = IdPRouter()

        config = IdPConfig(
            idp_id="okta",
            name="Okta",
            saml_config=SAMLConfig(
                provider=SAMLProvider.OKTA,
                entity_id="https://okta.example.com",
                sso_url="https://okta.example.com/sso",
            ),
            email_domains=["company.com"],
        )

        router.register_idp(config)

        hint = IdPDiscoveryHint(email="alice@company.com")
        result = router.route(hint)

        assert result is not None
        assert result.idp_id == "okta"
        assert result.strategy_used == IdPRoutingStrategy.EMAIL_DOMAIN


class TestComplianceControls:
    def test_list_controls(self):
        registry = ComplianceControl()

        controls = registry.list_controls(framework=ControlFramework.SOC2)

        assert len(controls) > 0
        assert all(ControlFramework.SOC2 in c.frameworks for c in controls)

    def test_check_control(self):
        registry = ComplianceControl()

        result = registry.check_control("CC5.1")

        assert result.control_id == "CC5.1"
        assert result.status in [ControlStatus.PASS, ControlStatus.WARNING, ControlStatus.FAIL]
        assert len(result.evidence) > 0


class TestEvidenceCollector:
    def test_collect_evidence(self):
        collector = EvidenceCollector()

        evidence = collector.collect("encryption_status")

        assert evidence.evidence_type == EvidenceType.ENCRYPTION_STATUS
        assert evidence.data is not None
        assert evidence.hash != ""

    def test_create_snapshot(self):
        collector = EvidenceCollector()
        collector.collect_all()

        snapshot = collector.create_snapshot(framework="soc2")

        assert len(snapshot.evidence_items) > 0
        assert snapshot.summary["total_evidence"] > 0


class TestComplianceDashboard:
    def test_get_score(self):
        dashboard = ComplianceDashboard()

        score = dashboard.get_score(ControlFramework.SOC2)

        assert score.framework == ControlFramework.SOC2
        assert 0 <= score.score <= 100

    def test_check_for_violations(self):
        dashboard = ComplianceDashboard()

        dashboard._registry.check_all_controls()
        alerts = dashboard.check_for_violations()

        assert isinstance(alerts, list)


class TestTrustPortal:
    def test_list_documents(self):
        portal = TrustPortal()

        docs = portal.list_documents()

        assert len(docs) > 0

    def test_get_questionnaire(self):
        portal = TrustPortal()

        answers = portal.get_questionnaire(QuestionnaireType.SIG_LITE)

        assert len(answers) > 0

    def test_request_access_nda_required(self):
        portal = TrustPortal()

        access = portal.request_access(
            "soc2_2026",
            "customer@example.com",
            "Example Corp",
            "192.168.1.1",
            nda_accepted=False,
        )

        assert access is None

        access = portal.request_access(
            "soc2_2026",
            "customer@example.com",
            "Example Corp",
            "192.168.1.1",
            nda_accepted=True,
        )

        assert access is not None
        assert access.nda_signed is True


class TestFatigueDetector:
    def test_assess_fatigue(self):
        detector = FatigueDetector()

        score = detector.assess_fatigue("alice")

        assert score.engineer_id == "alice"
        assert score.level in list(FatigueLevel)

    def test_record_page(self):
        detector = FatigueDetector()

        for i in range(10):
            event = PageEvent(
                event_id=f"page_{i}",
                engineer_id="alice",
                timestamp=datetime.now(timezone.utc),
                incident_id=f"inc_{i}",
                severity="high",
            )
            detector.record_page(event)

        score = detector.assess_fatigue("alice")

        assert score.metrics["pages_this_week"] == 10


class TestSmartRouter:
    def test_route_incident(self):
        router = SmartRouter()

        incident = IncidentRouting(
            incident_id="inc_1",
            title="Database connection pool exhausted",
            service="billing-db",
            severity="high",
            affected_technologies=["PostgreSQL"],
        )

        decision = router.route(incident)

        assert decision.primary_responder is not None
        assert decision.confidence > 0

    def test_update_availability(self):
        router = SmartRouter()

        result = router.update_availability("alice", AvailabilityStatus.AWAY)

        assert result is True


class TestCompensationTracker:
    def test_record_shift(self):
        tracker = CompensationTracker()

        now = datetime.now(timezone.utc)
        shift = tracker.record_shift(
            engineer_id="alice",
            engineer_name="Alice Chen",
            start_time=now - timedelta(hours=8),
            end_time=now,
            incidents_handled=3,
        )

        assert shift.duration_hours == 8
        assert shift.incidents_handled == 3

    def test_calculate_compensation(self):
        tracker = CompensationTracker()

        now = datetime.now(timezone.utc)
        tracker.record_shift(
            "alice",
            "Alice Chen",
            now - timedelta(hours=8),
            now,
            incidents_handled=2,
        )

        summary = tracker.calculate_compensation(
            "alice",
            now - timedelta(days=7),
            now + timedelta(days=1),
        )

        assert summary.total_hours == 8
        assert summary.total_pay > Decimal("0")


class TestSwapMarketplace:
    def test_create_request(self):
        marketplace = SwapMarketplace()

        now = datetime.now(timezone.utc)
        request = marketplace.create_request(
            requester_id="alice",
            requester_name="Alice Chen",
            shift_start=now + timedelta(days=1),
            shift_end=now + timedelta(days=1, hours=12),
            reason="Concert tickets",
        )

        assert request.status == SwapStatus.PENDING

    def test_make_offer(self):
        marketplace = SwapMarketplace()

        now = datetime.now(timezone.utc)
        request = marketplace.create_request(
            "alice",
            "Alice Chen",
            now + timedelta(days=1),
            now + timedelta(days=1, hours=12),
            "PTO",
        )

        offer = marketplace.make_offer(
            request.request_id,
            "bob",
            "Bob Johnson",
            "Happy to help!",
        )

        assert offer is not None
        assert offer.offerer_id == "bob"
        assert request.status == SwapStatus.OFFERED


class TestStatusRegistry:
    def test_list_components(self):
        registry = StatusRegistry()

        components = registry.list_components(showcase_only=True)

        assert len(components) > 0

    def test_update_status(self):
        registry = StatusRegistry()

        result = registry.update_status(
            "api",
            ComponentStatus.DEGRADED,
            reason="High latency",
        )

        assert result is True
        assert registry.get_component("api").status == ComponentStatus.DEGRADED

    def test_get_overall_status(self):
        registry = StatusRegistry()

        status = registry.get_overall_status()

        assert status in list(ComponentStatus)


class TestIncidentManager:
    def test_create_incident(self):
        manager = IncidentManager()

        incident = manager.create_incident(
            title="API returning 500 errors",
            severity=IncidentSeverity.MAJOR,
            affected_components=["api"],
            affected_features=["Authentication"],
            customer_impact="Users cannot log in",
            initial_message="Investigating elevated error rates",
        )

        assert incident.status == IncidentStatus.INVESTIGATING
        assert len(incident.updates) == 1

    def test_add_update(self):
        manager = IncidentManager()

        incident = manager.create_incident(
            "Test incident",
            IncidentSeverity.MINOR,
            ["api"],
            [],
            "Minor impact",
        )

        update = manager.add_update(
            incident.incident_id,
            IncidentStatus.IDENTIFIED,
            "Root cause identified",
        )

        assert update is not None
        assert incident.status == IncidentStatus.IDENTIFIED


class TestSLATracker:
    def test_get_sla_status(self):
        tracker = SLATracker()

        status = tracker.get_sla_status("api_monthly")

        assert status is not None
        assert status.sla.sla_id == "api_monthly"
        assert status.current_uptime <= Decimal("100")

    def test_record_downtime(self):
        tracker = SLATracker()

        event = tracker.record_downtime_start("api", is_maintenance=False)
        tracker.record_downtime_end(event.event_id)

        status = tracker.get_sla_status("api_monthly")

        assert status.downtime_minutes >= Decimal("0")


class TestAIUpdateGenerator:
    def test_generate_update(self):
        generator = AIUpdateGenerator()

        data = InternalIncidentData(
            error_type="NullPointerException",
            error_details="in billing/checkout.py:142",
            affected_service="checkout-api",
            error_count=847,
            affected_users_estimate=200,
            root_cause="Deploy #47 removed null check",
            fix_status="Fix deployed, canary at 10%",
        )

        update = generator.generate(data, "identified", UpdateTone.PROFESSIONAL)

        assert "checkout" in update.customer_message.lower()
        assert update.internal_summary != ""

    def test_suggest_updates(self):
        generator = AIUpdateGenerator()

        data = InternalIncidentData(
            error_type="500 errors",
            error_details="High error rate",
            affected_service="api-gateway",
            error_count=100,
            affected_users_estimate=50,
        )

        suggestions = generator.suggest_updates(data, "investigating")

        assert len(suggestions) == 3


class TestWidgetGenerator:
    def test_create_widget(self):
        generator = WidgetGenerator()

        widget = generator.create_widget(
            customer_id="cust_1",
            components=["api", "checkout"],
            theme=WidgetTheme.DARK,
        )

        assert widget.customer_id == "cust_1"
        assert len(widget.embed_token) > 0

    def test_get_embed_code(self):
        generator = WidgetGenerator()

        widget = generator.create_widget("cust_1", ["api"])

        script = generator.get_embed_code(widget.widget_id, "script")
        iframe = generator.get_embed_code(widget.widget_id, "iframe")

        assert "<script" in script
        assert "<iframe" in iframe

    def test_validate_token(self):
        generator = WidgetGenerator()

        widget = generator.create_widget("cust_1", ["api"])

        assert generator.validate_token(widget.widget_id, widget.embed_token) is True
        assert generator.validate_token(widget.widget_id, "invalid") is False
