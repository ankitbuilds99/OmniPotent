"""Tests for launch-critical features."""


class TestOAuthModule:
    """Tests for OAuth2 authentication."""

    def test_oauth_config_loading(self):
        """Test OAuth config loads from environment."""
        from app.auth.oauth import get_available_providers

        providers = get_available_providers()
        assert isinstance(providers, list)

    def test_session_token_creation_and_verification(self, monkeypatch):
        """Test JWT session token creation and verification."""
        from app.auth.oauth import create_session_token, verify_session_token
        from app.config import get_settings

        # Session signing has no development fallback on purpose — a hardcoded
        # default would let anyone forge an owner session — so supply a key.
        monkeypatch.setenv("COPILOT_SECRET_KEY", "test-signing-key-32-bytes-minimum-length")
        get_settings.cache_clear()

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
        assert msg.from_name == "OmniPotent"

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
