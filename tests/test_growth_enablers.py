"""Tests for growth enabler features."""

from datetime import datetime, timedelta, timezone

from app.growth.usage import (
    UsageLimitType,
    UsageTracker,
)
from app.growth.tiers import (
    FeatureAccess,
    TierManager,
)
from app.growth.upgrade import (
    UpgradeContext,
    UpgradeTimingEngine,
)
from app.growth.achievements import (
    AchievementStatus,
    AchievementTracker,
)
from app.onboarding.health import (
    OnboardingHealth,
)
from app.onboarding.demo import (
    DemoMode,
)
from app.onboarding.rescue import (
    RescueEngine,
)
from app.roi.calculator import (
    IncidentMetrics,
    ROICalculator,
)
from app.roi.benchmarks import (
    BenchmarkEngine,
)
from app.roi.reports import (
    ReportGenerator,
    ReportType,
)
from app.github_app.risk_analyzer import (
    PRFile,
    RiskAnalyzer,
)
from app.github_app.pattern_matcher import (
    PatternMatcher,
)
from app.github_app.reviewer_suggester import (
    ReviewerProfile,
    ReviewerSuggester,
)
from app.mobile.notifications import (
    NotificationService,
)
from app.mobile.routing import (
    NotificationRouter,
    QuietHoursConfig,
    ResponderProfile,
)
from app.mobile.widgets import (
    SystemStatus,
    WidgetService,
    WidgetSize,
)


class TestUsageTracker:
    def test_record_usage(self):
        tracker = UsageTracker()
        tracker.set_tier("org1", "free")

        success, msg = tracker.record_usage("org1", UsageLimitType.INVESTIGATIONS)
        assert success is True

        summary = tracker.get_summary("org1")
        assert summary.tier == "free"

    def test_value_tracking(self):
        tracker = UsageTracker()
        tracker.set_tier("org1", "free")

        tracker.record_usage("org1", UsageLimitType.INVESTIGATIONS, 5)

        summary = tracker.get_summary("org1")
        assert summary.value.incidents_resolved == 5
        assert summary.value.dollars_saved > 0

    def test_bonus_investigations(self):
        tracker = UsageTracker()
        tracker.set_tier("org1", "free")

        total = tracker.add_bonus("org1", 5, "referral")
        assert total == 5


class TestTierManager:
    def test_get_tiers(self):
        manager = TierManager()

        tiers = manager.get_all_tiers()
        assert len(tiers) >= 3

        free = manager.get_tier("free")
        assert free is not None
        assert free.price_monthly == 0

    def test_check_feature_access(self):
        manager = TierManager()

        access, info = manager.check_access("free", "ai_fix_generation")
        assert access == FeatureAccess.PREVIEW

        access, info = manager.check_access("pro", "ai_fix_generation")
        assert access == FeatureAccess.FULL

    def test_tier_comparison(self):
        manager = TierManager()

        comparison = manager.compare_tiers("free", "pro", {"dollars_saved": 1000})
        assert comparison.recommended_tier == "pro"
        assert len(comparison.features_unlocked) > 0


class TestUpgradeTimingEngine:
    def test_should_not_prompt_during_incident(self):
        engine = UpgradeTimingEngine()
        engine.update_user_state("user1", "org1", "free")

        rec = engine.should_show_upgrade("user1", UpgradeContext.DURING_INCIDENT)
        assert rec.should_prompt is False

    def test_should_prompt_at_limit(self):
        engine = UpgradeTimingEngine()
        engine.update_user_state("user1", "org1", "free", value_delivered=500)

        rec = engine.should_show_upgrade("user1", UpgradeContext.AT_LIMIT)
        assert rec.should_prompt is True
        assert rec.urgency == "high"

    def test_conversion_likelihood(self):
        engine = UpgradeTimingEngine()
        engine.update_user_state(
            "user1", "org1", "free",
            value_delivered=2000,
            investigations_run=10,
            team_size=4,
        )

        status = engine.get_user_status("user1")
        assert status["found"] is True
        assert status["conversion_likelihood"] > 0.5


class TestAchievementTracker:
    def test_record_achievement(self):
        tracker = AchievementTracker()

        earned = tracker.check_investigation("user1", duration_minutes=10)
        assert len(earned) >= 1

        user_achs = tracker.get_user_achievements("user1")
        first_inv = user_achs.achievements.get("first_investigation")
        assert first_inv is not None
        assert first_inv.status == AchievementStatus.EARNED

    def test_speed_demon_achievement(self):
        tracker = AchievementTracker()

        earned = tracker.check_investigation("user1", duration_minutes=10)
        speed_demon = next((a for a in earned if a.achievement_id == "speed_demon"), None)
        assert speed_demon is not None

    def test_weekly_streak(self):
        tracker = AchievementTracker()

        streak = tracker.record_activity("user1")
        assert streak.current_streak >= 1


class TestOnboardingHealth:
    def test_create_checklist(self):
        health = OnboardingHealth()

        checklist = health.create_checklist("user1")
        assert len(checklist.checks) > 0
        assert checklist.total_points > 0

    def test_complete_check(self):
        health = OnboardingHealth()
        health.create_checklist("user1")

        checklist = health.complete_check("user1", "account_created")
        assert checklist.earned_points > 0

    def test_health_score(self):
        health = OnboardingHealth()
        health.create_checklist("user1")
        health.complete_check("user1", "account_created")
        health.complete_check("user1", "first_integration")

        score = health.get_health_score("user1")
        assert score.score > 0
        assert score.conversion_likelihood > 0


class TestDemoMode:
    def test_get_scenarios(self):
        demo = DemoMode()

        scenarios = demo.get_scenarios()
        assert len(scenarios) > 0

    def test_start_demo(self):
        demo = DemoMode()

        result = demo.start_demo("user1", "checkout_error")
        assert "scenario" in result
        assert result["scenario"]["name"] == "Checkout API Errors"

    def test_process_query(self):
        demo = DemoMode()
        demo.start_demo("user1", "checkout_error")

        result = demo.process_query("user1", "What is the root cause?")
        assert result["type"] == "root_cause"
        assert "content" in result


class TestRescueEngine:
    def test_check_triggers(self):
        engine = RescueEngine()
        engine.update_user_state(
            "user1",
            signup_at=datetime.now(timezone.utc) - timedelta(hours=48),
            investigations=0,
        )

        triggered = engine.check_triggers("user1")
        assert len(triggered) > 0

    def test_execute_campaign(self):
        engine = RescueEngine()

        records = engine.execute_campaign("user1", "no_investigation_24h")
        assert len(records) > 0


class TestROICalculator:
    def test_calculate_incident_value(self):
        calculator = ROICalculator()

        metrics = IncidentMetrics(
            incident_id="inc1",
            duration_minutes=30,
            time_to_detect_minutes=5,
            time_to_resolve_minutes=25,
            responders=3,
            severity="SEV2",
        )

        value = calculator.calculate_incident_value("org1", metrics)
        assert value["total_value"] > 0

    def test_calculate_period_roi(self):
        calculator = ROICalculator()

        metrics = IncidentMetrics(
            incident_id="inc1",
            duration_minutes=30,
            time_to_detect_minutes=5,
            time_to_resolve_minutes=25,
            responders=3,
            severity="SEV2",
        )
        calculator.record_incident("org1", metrics)

        now = datetime.now(timezone.utc)
        roi = calculator.calculate_period_roi(
            "org1",
            now - timedelta(days=30),
            now + timedelta(days=1),
        )

        assert roi.roi_multiple > 0

    def test_predictive_roi(self):
        calculator = ROICalculator()

        prediction = calculator.calculate_predictive_roi("org1", "free", "pro")
        assert "projected_value" in prediction
        assert "roi_multiple" in prediction


class TestBenchmarkEngine:
    def test_compare_benchmarks(self):
        engine = BenchmarkEngine()

        comparison = engine.compare(
            "org1",
            {
                "mttr_minutes": 45,
                "root_cause_accuracy_percent": 90,
            },
            team_size=50,
        )

        assert len(comparison.benchmarks) > 0
        assert comparison.overall_percentile > 0

    def test_improvement_suggestions(self):
        engine = BenchmarkEngine()

        comparison = engine.compare(
            "org1",
            {"mttr_minutes": 200},
            team_size=50,
        )

        suggestions = engine.get_improvement_suggestions(comparison)
        assert len(suggestions) > 0


class TestReportGenerator:
    def test_generate_executive_summary(self):
        generator = ReportGenerator()

        report = generator.generate_executive_summary(
            "org1",
            {
                "total_value": 50000,
                "hours_saved": 100,
                "roi_multiple": 25,
                "incidents_count": 50,
                "breakdown": {
                    "engineering_time_saved": 25000,
                    "revenue_protected": 20000,
                },
            },
        )

        assert len(report.sections) > 0
        assert report.report_type == ReportType.EXECUTIVE_SUMMARY

    def test_schedule_report(self):
        generator = ReportGenerator()

        schedule = generator.schedule_report(
            "org1",
            ReportType.EXECUTIVE_SUMMARY,
            "monthly",
            ["finance@example.com"],
        )

        assert schedule.frequency == "monthly"
        assert schedule.next_send is not None


class TestRiskAnalyzer:
    def test_analyze_pr(self):
        analyzer = RiskAnalyzer()

        files = [
            PRFile("billing/checkout.py", additions=50, deletions=10),
            PRFile("billing/payment.py", additions=20, deletions=5),
        ]

        analysis = analyzer.analyze_pr(
            pr_number=123,
            repo="acme/backend",
            title="Refactor payment validation",
            author="carol",
            files=files,
            diff_content="-if payment_method is None:\n+payment_method.charge(amount)",
        )

        assert analysis.overall_score > 0
        assert len(analysis.factors) > 0

    def test_high_risk_detection(self):
        analyzer = RiskAnalyzer()

        files = [PRFile("billing/checkout.py", additions=100, deletions=50)]

        analysis = analyzer.analyze_pr(
            pr_number=123,
            repo="acme/backend",
            title="Remove null checks",
            author="carol",
            files=files,
            diff_content="-if payment_method is None:\n-    raise Error()\n+payment_method.charge(amount)",
        )

        pattern_factor = next((f for f in analysis.factors if f.factor_id == "pattern_risk"), None)
        assert pattern_factor is not None
        assert pattern_factor.details.get("null_checks_removed", 0) >= 1

    def test_github_comment_generation(self):
        analyzer = RiskAnalyzer()

        files = [PRFile("api/handler.py", additions=10)]

        analysis = analyzer.analyze_pr(
            pr_number=1,
            repo="test/repo",
            title="Test PR",
            author="test",
            files=files,
        )

        comment = analysis.to_github_comment()
        assert "Risk Score" in comment
        assert "DevOps Copilot" in comment


class TestPatternMatcher:
    def test_match_null_check_pattern(self):
        matcher = PatternMatcher()

        pattern = matcher.get_pattern("null_check_removal")
        assert pattern is not None
        assert "null" in pattern.name.lower()

        all_patterns = matcher.get_all_patterns()
        assert len(all_patterns) > 0

    def test_pattern_stats(self):
        matcher = PatternMatcher()

        stats = matcher.get_pattern_stats()
        assert stats["total_patterns"] > 0


class TestReviewerSuggester:
    def test_suggest_reviewers(self):
        suggester = ReviewerSuggester()

        suggester.register_ownership("billing/", ["dave", "alice"])
        suggester.register_reviewer(ReviewerProfile(
            username="dave",
            expertise_areas=["billing", "payments"],
        ))

        suggestions = suggester.suggest_reviewers(
            files=["billing/checkout.py"],
            pr_author="carol",
        )

        assert len(suggestions) > 0
        assert suggestions[0].reviewer.username == "dave"


class TestNotificationService:
    def test_create_notification(self):
        service = NotificationService()

        notification = service.create_notification(
            incident_id="inc-123",
            title="Checkout API 500 errors",
            severity="SEV2",
            recipient="eve",
            root_cause="Deploy #47 removed null check",
            confidence=94.0,
            deploy_info={"deploy_id": 47, "author": "carol"},
        )

        assert notification.ai_enrichment is not None
        assert "deploy" in notification.ai_enrichment.summary.lower()

    def test_voice_summary(self):
        service = NotificationService()

        notification = service.create_notification(
            incident_id="inc-123",
            title="Database slow",
            severity="SEV1",
            recipient="eve",
            root_cause="Missing index on users table",
            confidence=85.0,
        )

        voice = notification.to_voice_summary()
        assert "severity 1" in voice.lower()


class TestNotificationRouter:
    def test_route_by_expertise(self):
        router = NotificationRouter()

        router.register_responder(ResponderProfile(
            user_id="dave",
            expertise_areas=["database"],
            is_available=True,
        ))
        router.register_responder(ResponderProfile(
            user_id="eve",
            is_scheduled_oncall=True,
            is_available=True,
        ))
        router.register_expertise("user-db", ["dave"])

        decision = router.route("user-db", "SEV2", "eve")
        assert decision.primary_recipient == "dave"

    def test_quiet_hours(self):
        router = NotificationRouter()

        router.set_quiet_hours("eve", QuietHoursConfig(
            user_id="eve",
            enabled=True,
            sev2_vibrate_only=True,
        ))

        router.register_responder(ResponderProfile(
            user_id="eve",
            is_scheduled_oncall=True,
            is_available=True,
        ))

        decision = router.route("checkout-api", "SEV2", "eve")
        assert decision.primary_recipient == "eve"


class TestWidgetService:
    def test_register_widget(self):
        service = WidgetService()

        widget = service.register_widget("user1", WidgetSize.MEDIUM, ["checkout-api"])
        assert widget.size == WidgetSize.MEDIUM

    def test_widget_data(self):
        service = WidgetService()

        widget = service.register_widget("user1", WidgetSize.LARGE)
        service.update_service_status("checkout-api", "Checkout API", SystemStatus.HEALTHY)
        service.set_oncall(
            "user1",
            "Eve",
            datetime.now(timezone.utc) + timedelta(hours=8),
        )

        data = service.get_widget_data(widget.widget_id)
        assert data is not None
        assert data.overall_status == SystemStatus.HEALTHY

    def test_ios_widget_format(self):
        service = WidgetService()

        widget = service.register_widget("user1", WidgetSize.MEDIUM)
        service.update_service_status("api", "API", SystemStatus.HEALTHY)

        data = service.get_widget_data(widget.widget_id)
        ios_format = data.to_ios_widget()

        assert "entries" in ios_format
        assert ios_format["family"] == "medium"
