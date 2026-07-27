"""Tests for launch-critical features."""

import pytest
from datetime import datetime, timezone, timedelta


class TestOAuthModule:
    """Tests for OAuth2 authentication."""

    def test_oauth_config_loading(self):
        """Test OAuth config loads from environment."""
        from app.auth.oauth import get_available_providers

        providers = get_available_providers()
        assert isinstance(providers, list)

    def test_session_token_creation_and_verification(self):
        """Test JWT session token creation and verification."""
        from app.auth.oauth import create_session_token, verify_session_token

        token = create_session_token(
            user_id="user_123",
            org_id="org_456",
            email="test@example.com",
            role="admin",
        )

        assert token
        assert isinstance(token, str)

        payload = verify_session_token(token)
        assert payload is not None
        assert payload["sub"] == "user_123"
        assert payload["org_id"] == "org_456"
        assert payload["email"] == "test@example.com"
        assert payload["role"] == "admin"

    def test_oauth_state_generation_and_verification(self):
        """Test OAuth state parameter handling."""
        from app.auth.oauth import generate_oauth_state, verify_oauth_state

        state = generate_oauth_state("google")
        assert state

        verified = verify_oauth_state(state)
        assert verified == "google"

        verified_again = verify_oauth_state(state)
        assert verified_again is None

    def test_account_linking(self):
        """Test OAuth account linking."""
        from app.auth.oauth import (
            OAuthUser,
            link_oauth_account,
            find_account_by_provider,
            get_user_accounts,
        )

        user = OAuthUser(
            provider="github",
            provider_id="12345",
            email="dev@example.com",
            name="Test Developer",
        )

        account = link_oauth_account("user_test_123", user)
        assert account.user_id == "user_test_123"
        assert account.provider == "github"

        found = find_account_by_provider("github", "12345")
        assert found is not None
        assert found.email == "dev@example.com"

        accounts = get_user_accounts("user_test_123")
        assert len(accounts) >= 1


class TestEmailNotifications:
    """Tests for email notification system."""

    def test_email_message_creation(self):
        """Test email message dataclass."""
        from app.notifications.email import EmailMessage

        msg = EmailMessage(
            to=["test@example.com"],
            subject="Test Subject",
            body_text="Plain text body",
            body_html="<p>HTML body</p>",
        )

        assert msg.to == ["test@example.com"]
        assert msg.subject == "Test Subject"
        assert msg.from_name == "DevOps Copilot"

    def test_smtp_provider_configuration(self):
        """Test SMTP provider config check."""
        from app.notifications.email import SMTPProvider

        provider = SMTPProvider()
        assert not provider.is_configured()

        provider = SMTPProvider(
            host="smtp.example.com",
            username="user",
            password="pass",
        )
        assert provider.is_configured()

    def test_sendgrid_provider_configuration(self):
        """Test SendGrid provider config check."""
        from app.notifications.email import SendGridProvider

        provider = SendGridProvider()
        assert not provider.is_configured()

        provider = SendGridProvider(
            api_key="SG.test",
            from_email="test@example.com",
        )
        assert provider.is_configured()


class TestPushNotifications:
    """Tests for push notification system."""

    def test_push_message_creation(self):
        """Test push message dataclass."""
        from app.notifications.push import PushMessage

        msg = PushMessage(
            title="Alert",
            body="Something happened",
            data={"key": "value"},
        )

        assert msg.title == "Alert"
        assert msg.priority == "high"

    def test_device_registration(self):
        """Test device token registration."""
        from app.notifications.push import (
            register_device,
            unregister_device,
            get_user_devices,
        )

        device = register_device(
            user_id="user_push_test",
            token="fcm_token_123",
            platform="android",
            device_name="Test Phone",
        )

        assert device.user_id == "user_push_test"
        assert device.platform == "android"

        devices = get_user_devices("user_push_test")
        assert len(devices) >= 1

        result = unregister_device("fcm_token_123")
        assert result is True


class TestNotificationDispatcher:
    """Tests for notification dispatcher."""

    def test_notification_creation(self):
        """Test notification dataclass."""
        from app.notifications.dispatcher import (
            Notification,
            NotificationPriority,
        )

        notif = Notification(
            id="notif_1",
            type="alert",
            title="Test Alert",
            body="Alert body",
            priority=NotificationPriority.HIGH,
        )

        assert notif.title == "Test Alert"
        assert notif.priority == NotificationPriority.HIGH

    def test_user_preferences(self):
        """Test user notification preferences."""
        from app.notifications.dispatcher import (
            NotificationPreferences,
            NotificationChannel,
            set_user_preferences,
            get_user_preferences,
        )

        prefs = NotificationPreferences(
            user_id="user_prefs_test",
            email="test@example.com",
            enabled_channels={NotificationChannel.EMAIL},
        )

        set_user_preferences(prefs)
        loaded = get_user_preferences("user_prefs_test")

        assert loaded.email == "test@example.com"
        assert NotificationChannel.EMAIL in loaded.enabled_channels


class TestReportGenerator:
    """Tests for report generation."""

    def test_report_config(self):
        """Test report configuration."""
        from app.reports.generator import ReportConfig, ReportGenerator

        config = ReportConfig(
            title="Test Report",
            subtitle="Test Subtitle",
            author="Test Author",
        )

        gen = ReportGenerator(config)
        assert gen.config.title == "Test Report"

    def test_report_sections(self):
        """Test adding report sections."""
        from app.reports.generator import ReportConfig, ReportGenerator

        config = ReportConfig(title="Test Report")
        gen = ReportGenerator(config)

        gen.add_text("Summary", "This is the summary text")
        gen.add_table("Data", [{"col1": "val1", "col2": "val2"}])
        gen.add_key_value("Details", {"key1": "value1", "key2": "value2"})

        assert len(gen.sections) == 3

    def test_markdown_generation(self):
        """Test markdown report generation."""
        from app.reports.generator import ReportConfig, ReportGenerator, ReportFormat

        config = ReportConfig(title="Test Report")
        gen = ReportGenerator(config)
        gen.add_text("Summary", "Test summary content")

        report = gen.generate(ReportFormat.MARKDOWN)
        assert report.format == ReportFormat.MARKDOWN
        assert b"# Test Report" in report.content
        assert b"Test summary content" in report.content

    def test_json_generation(self):
        """Test JSON report generation."""
        import json
        from app.reports.generator import ReportConfig, ReportGenerator, ReportFormat

        config = ReportConfig(title="Test Report")
        gen = ReportGenerator(config)
        gen.add_text("Summary", "Test content")

        report = gen.generate(ReportFormat.JSON)
        data = json.loads(report.content)

        assert data["title"] == "Test Report"
        assert len(data["sections"]) == 1


class TestTeamsIntegration:
    """Tests for MS Teams integration."""

    def test_teams_config(self):
        """Test Teams configuration."""
        from app.integrations.teams import TeamsConfig

        config = TeamsConfig()
        assert not config.is_configured()

        config = TeamsConfig(webhook_url="https://outlook.webhook.office.com/test")
        assert config.is_configured()

    def test_adaptive_card_builder(self):
        """Test adaptive card building."""
        from app.integrations.teams import AdaptiveCard

        card = AdaptiveCard()
        card.add_heading("Test Heading")
        card.add_text("Test text content")
        card.add_facts([("Key1", "Value1"), ("Key2", "Value2")])
        card.add_action_button("Click Me", "https://example.com")

        result = card.to_dict()

        assert result["type"] == "AdaptiveCard"
        assert len(result["body"]) == 3
        assert len(result["actions"]) == 1

    def test_card_to_message(self):
        """Test converting card to Teams message format."""
        from app.integrations.teams import AdaptiveCard

        card = AdaptiveCard()
        card.add_text("Test")

        message = card.to_message("Test Summary")

        assert message["type"] == "message"
        assert len(message["attachments"]) == 1
        assert message["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


class TestPostmortemGenerator:
    """Tests for postmortem generation."""

    def test_postmortem_templates(self):
        """Test postmortem templates."""
        from app.postmortem.generator import get_postmortem_templates

        templates = get_postmortem_templates()

        assert "google" in templates
        assert "pagerduty" in templates
        assert "minimal" in templates

    def test_action_item_creation(self):
        """Test action item dataclass."""
        from app.postmortem.generator import ActionItem, ActionItemPriority, ActionItemStatus

        item = ActionItem(
            id="ai_1",
            title="Fix the bug",
            description="Detailed description",
            priority=ActionItemPriority.P1,
        )

        assert item.status == ActionItemStatus.OPEN
        assert item.priority == ActionItemPriority.P1

    @pytest.mark.asyncio
    async def test_postmortem_generation(self):
        """Test generating a postmortem document."""
        from app.postmortem.generator import generate_postmortem

        incident_data = {
            "title": "Database Outage",
            "summary": "Database became unavailable",
            "severity": "high",
            "impact": "Users could not access data",
            "root_cause": "Connection pool exhaustion due to configuration error",
            "started_at": "2024-01-15T10:00:00Z",
            "detected_at": "2024-01-15T10:05:00Z",
            "resolved_at": "2024-01-15T11:30:00Z",
        }

        doc = await generate_postmortem("INC-123", incident_data)

        assert doc.incident_id == "INC-123"
        assert "Database Outage" in doc.title
        assert doc.root_cause
        assert len(doc.action_items) > 0

    def test_postmortem_to_markdown(self):
        """Test converting postmortem to markdown."""
        from app.postmortem.generator import PostmortemDocument, PostmortemGenerator

        doc = PostmortemDocument(
            id="pm_1",
            incident_id="INC-456",
            title="Test Postmortem",
            summary="Test summary",
            root_cause="Test root cause",
        )

        gen = PostmortemGenerator()
        md = gen.to_markdown(doc)

        assert "# Test Postmortem" in md
        assert "INC-456" in md


class TestTimelineCollector:
    """Tests for incident timeline collection."""

    def test_timeline_event_creation(self):
        """Test timeline event dataclass."""
        from app.timeline.collector import (
            TimelineEvent,
            EventType,
            EventSeverity,
            EventSource,
        )

        event = TimelineEvent(
            id="evt_1",
            timestamp=datetime.now(timezone.utc),
            title="Alert fired",
            event_type=EventType.ALERT,
            severity=EventSeverity.WARNING,
            source=EventSource.PROMETHEUS,
        )

        assert event.event_type == EventType.ALERT
        assert event.source == EventSource.PROMETHEUS

    def test_timeline_filter(self):
        """Test timeline filtering."""
        from app.timeline.collector import (
            TimelineEvent,
            TimelineFilter,
            EventType,
            EventSeverity,
        )

        now = datetime.now(timezone.utc)
        event = TimelineEvent(
            id="evt_1",
            timestamp=now,
            title="Test event",
            event_type=EventType.ALERT,
            severity=EventSeverity.WARNING,
            service="api",
        )

        filter_match = TimelineFilter(
            event_types=[EventType.ALERT],
            services=["api"],
        )
        assert filter_match.matches(event)

        filter_no_match = TimelineFilter(
            event_types=[EventType.DEPLOY],
        )
        assert not filter_no_match.matches(event)

    def test_event_aggregation(self):
        """Test event aggregation."""
        from app.timeline.collector import TimelineEvent, aggregate_events

        now = datetime.now(timezone.utc)
        events = [
            TimelineEvent(id=f"evt_{i}", timestamp=now + timedelta(minutes=i), title=f"Event {i}")
            for i in range(10)
        ]

        windows = aggregate_events(events, window_seconds=300)
        assert len(windows) >= 1

    def test_event_correlation(self):
        """Test event correlation."""
        from app.timeline.collector import TimelineEvent, correlate_events

        now = datetime.now(timezone.utc)
        events = [
            TimelineEvent(id="evt_1", timestamp=now, title="Alert 1", service="api"),
            TimelineEvent(id="evt_2", timestamp=now + timedelta(seconds=30), title="Alert 2", service="api"),
            TimelineEvent(id="evt_3", timestamp=now + timedelta(minutes=10), title="Unrelated", service="web"),
        ]

        groups = correlate_events(events, correlation_window_seconds=120)
        assert len(groups) >= 1


class TestRunbookBuilder:
    """Tests for runbook builder."""

    def test_runbook_creation(self):
        """Test building a runbook."""
        from app.runbook_builder.builder import RunbookBuilder

        builder = RunbookBuilder("Test Runbook", "Test description")
        builder.add_variable("service", "Service name", required=True)
        builder.add_script_step("Check status", "echo 'checking'")
        builder.add_wait_step("Wait", 5)
        builder.add_notify_step("Notify", "slack", "Done!")

        runbook = builder.build()

        assert runbook.name == "Test Runbook"
        assert len(runbook.variables) == 1
        assert len(runbook.steps) == 3

    def test_runbook_validation(self):
        """Test runbook validation."""
        from app.runbook_builder.builder import RunbookBuilder

        builder = RunbookBuilder("Valid Runbook")
        builder.add_script_step("Step 1", "echo hello")

        runbook = builder.build()
        errors = runbook.validate()

        assert len(errors) == 0

    def test_step_conditions(self):
        """Test step condition evaluation."""
        from app.runbook_builder.builder import StepCondition

        cond = StepCondition(variable="status", operator="equals", value="success")
        assert cond.evaluate({"status": "success"})
        assert not cond.evaluate({"status": "failed"})

        cond_gt = StepCondition(variable="count", operator="greater_than", value=10)
        assert cond_gt.evaluate({"count": 15})
        assert not cond_gt.evaluate({"count": 5})

    def test_runbook_storage(self):
        """Test runbook storage operations."""
        from app.runbook_builder.builder import (
            RunbookBuilder,
            create_runbook,
            get_runbook,
            list_runbooks,
        )

        builder = RunbookBuilder("Stored Runbook")
        builder.add_script_step("Step", "echo test")
        runbook = builder.build()

        create_runbook(runbook)

        loaded = get_runbook(runbook.id)
        assert loaded is not None
        assert loaded.name == "Stored Runbook"

        all_runbooks = list_runbooks()
        assert any(r.id == runbook.id for r in all_runbooks)


class TestOnboardingWizard:
    """Tests for onboarding wizard."""

    def test_onboarding_progress_creation(self):
        """Test creating onboarding progress."""
        from app.onboarding.wizard import OnboardingWizard

        wizard = OnboardingWizard()
        progress = wizard.create_progress("user_onboard_test", "org_test")

        assert progress.user_id == "user_onboard_test"
        assert len(progress.steps) > 0
        assert progress.current_step_id == "welcome"

    def test_step_completion(self):
        """Test completing onboarding steps."""
        from app.onboarding.wizard import OnboardingWizard

        wizard = OnboardingWizard()
        wizard.create_progress("user_step_test", "org_test")

        updated = wizard.complete_step("user_step_test", "welcome")
        assert updated is not None
        assert updated.current_step_id == "profile"

    def test_progress_percentage(self):
        """Test progress percentage calculation."""
        from app.onboarding.wizard import OnboardingWizard

        wizard = OnboardingWizard()
        progress = wizard.create_progress("user_pct_test", "org_test")

        pct = progress.get_progress_percentage()
        assert pct == 0

        wizard.complete_step("user_pct_test", "welcome")
        progress = wizard.get_progress("user_pct_test")
        pct = progress.get_progress_percentage()
        assert pct > 0

    def test_step_skipping(self):
        """Test skipping optional steps."""
        from app.onboarding.wizard import OnboardingWizard

        wizard = OnboardingWizard()
        wizard.create_progress("user_skip_test", "org_test")

        wizard.complete_step("user_skip_test", "welcome")
        wizard.complete_step("user_skip_test", "profile")
        wizard.complete_step("user_skip_test", "organization")

        result = wizard.skip_step("user_skip_test", "integrations")
        assert result is not None


class TestAnalyticsTracker:
    """Tests for analytics tracking."""

    def test_event_tracking(self):
        """Test tracking analytics events."""
        from app.analytics.tracker import (
            AnalyticsTracker,
            AnalyticsEvent,
            EventType,
        )

        tracker = AnalyticsTracker()

        event = AnalyticsEvent(
            id="evt_1",
            event_type=EventType.INVESTIGATION_START,
            user_id="user_analytics_test",
            properties={"severity": "high"},
        )

        tracker.track(event)

        events = tracker.get_events(event_type=EventType.INVESTIGATION_START)
        assert len(events) >= 1

    def test_counter_metrics(self):
        """Test counter metrics."""
        from app.analytics.tracker import AnalyticsTracker

        tracker = AnalyticsTracker()

        tracker.increment("test.counter")
        tracker.increment("test.counter")
        tracker.increment("test.counter")

        value = tracker.get_counter("test.counter")
        assert value == 3

    def test_histogram_stats(self):
        """Test histogram statistics."""
        from app.analytics.tracker import AnalyticsTracker

        tracker = AnalyticsTracker()

        for i in range(100):
            tracker.histogram("test.histogram", float(i))

        stats = tracker.get_histogram_stats("test.histogram")

        assert stats["count"] == 100
        assert stats["min"] == 0
        assert stats["max"] == 99
        assert 49 <= stats["avg"] <= 50

    def test_usage_summary(self):
        """Test usage summary generation."""
        from app.analytics.tracker import (
            track_investigation,
            get_usage_summary,
        )

        track_investigation(
            investigation_id="inv_sum_test",
            status="started",
            user_id="user_sum_test",
            severity="high",
        )

        summary = get_usage_summary()
        assert summary.total_investigations >= 0

    def test_top_users(self):
        """Test top users report."""
        from app.analytics.tracker import track_user_action, get_top_users

        for i in range(5):
            track_user_action(
                action="view_dashboard",
                user_id="user_top_test",
            )

        users = get_top_users()
        assert isinstance(users, list)
