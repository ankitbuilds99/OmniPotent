"""Tests for enterprise features: SSO/SAML, SOC2, On-Call, Status Page."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal


# SSO/SAML Tests

# Compliance Tests

# On-Call Tests
from app.oncall.fatigue import FatigueDetector, FatigueLevel, PageEvent
from app.oncall.smart_routing import (
    AvailabilityStatus,
    EngineerProfile,
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


def _registered_router() -> SmartRouter:
    """A router with a real (test-registered) engineer, the production path.

    The router used to seed four fictional engineers unconditionally; these
    tests registered nothing and still routed. Registration is now explicit.
    """
    router = SmartRouter()
    router.register_engineer(EngineerProfile(
        engineer_id="alice",
        name="Alice Chen",
        email="alice@example.test",
        services=["billing-db"],
        technologies=["PostgreSQL"],
        incident_history=[
            {"service": "billing-db", "resolved": True, "resolution_time_minutes": 20},
        ],
        availability=AvailabilityStatus.ONLINE,
        is_on_call=True,
    ))
    return router


class TestSmartRouter:
    def test_route_incident(self):
        router = _registered_router()

        incident = IncidentRouting(
            incident_id="inc_1",
            title="Database connection pool exhausted",
            service="billing-db",
            severity="high",
            affected_technologies=["PostgreSQL"],
        )

        decision = router.route(incident)

        assert decision.primary_responder is not None
        assert decision.primary_responder.engineer_id == "alice"
        assert decision.confidence > 0

    def test_update_availability(self):
        router = _registered_router()

        result = router.update_availability("alice", AvailabilityStatus.AWAY)

        assert result is True

    def test_starts_empty_without_demo_mode(self, monkeypatch):
        """Fictional engineers (alice@company.com etc.) must not be seeded into
        a router that assigns real incidents unless the operator opted into
        COPILOT_DEMO_MODE."""
        from app import config as cfg

        monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
        cfg.get_settings.cache_clear()
        try:
            router = SmartRouter()
        finally:
            cfg.get_settings.cache_clear()

        assert router._engineers == {}

    def test_no_engineers_is_a_clear_no_responders_result(self):
        router = SmartRouter()

        decision = router.route(IncidentRouting(
            incident_id="inc_none",
            title="Anything",
            service="billing-db",
            severity="high",
        ))

        assert decision.primary_responder is None
        assert decision.confidence == 0.0
        assert "No engineers are registered" in decision.reason
        assert decision.to_dict()["primary_responder"] is None

    def test_demo_mode_seeds_labelled_demo_engineers(self, monkeypatch):
        from app import config as cfg

        monkeypatch.setenv("COPILOT_DEMO_MODE", "true")
        cfg.get_settings.cache_clear()
        try:
            router = SmartRouter()
        finally:
            cfg.get_settings.cache_clear()

        assert "alice" in router._engineers


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
