"""Comprehensive tests for background task system.

Tests cover:
- Broker configuration (Redis, Stub, fallback)
- Webhook processing (GitHub, GitLab, PagerDuty, Slack)
- Notification tasks (Slack, PagerDuty)
- Alert evaluation
- Usage sync to Stripe
- Session cleanup
- Runbook execution
- Worker configuration
- Enqueue functionality
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("dramatiq", reason="dramatiq not installed")


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def reset_broker():
    """Reset broker state before and after each test."""
    from app.tasks import broker
    broker._broker = None
    yield
    broker._broker = None


@pytest.fixture
def event_loop_for_sync():
    """Create and set an event loop for tests calling sync functions with async internals."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


@pytest.fixture
def stub_broker():
    """Set up stub broker for testing."""
    with patch.dict(os.environ, {"DRAMATIQ_TEST_BROKER": "1"}):
        from app.tasks import broker
        broker._broker = None
        result = broker.configure_broker()
        yield result
        broker._broker = None


@pytest.fixture
def mock_settings():
    """Mock settings with configurable tokens."""
    settings = MagicMock()
    settings.slack_bot_token = ""
    settings.slack_channel = ""
    settings.pagerduty_api_token = ""
    settings.stripe_api_key = ""
    settings.stripe_meter_event = "usage_event"
    settings.http_timeout_default = 30.0
    return settings


# ============================================================================
# Broker Configuration Tests
# ============================================================================


class TestBrokerConfiguration:
    """Tests for broker configuration."""

    def test_configure_broker_stub_for_testing(self):
        """Should use stub broker when DRAMATIQ_TEST_BROKER is set."""
        with patch.dict(os.environ, {"DRAMATIQ_TEST_BROKER": "1"}, clear=False):
            from app.tasks import broker
            from dramatiq.brokers.stub import StubBroker

            broker._broker = None
            result = broker.configure_broker()

            assert isinstance(result, StubBroker)

    def test_configure_broker_redis(self):
        """Should use Redis broker when URL is provided."""
        with patch.dict(
            os.environ,
            {"COPILOT_REDIS_URL": "redis://localhost:6379"},
            clear=False
        ):
            # Remove test broker flag if present
            os.environ.pop("DRAMATIQ_TEST_BROKER", None)

            with patch("app.tasks.broker.RedisBroker") as mock_redis:
                mock_broker = MagicMock()
                mock_redis.return_value = mock_broker

                from app.tasks import broker
                broker._broker = None
                result = broker.configure_broker()

                mock_redis.assert_called_once()
                assert result == mock_broker

    def test_configure_broker_fallback_to_stub(self):
        """Should fallback to stub when no Redis URL and not in test mode."""
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ["COPILOT_REDIS_URL", "DRAMATIQ_TEST_BROKER"]}

        with patch.dict(os.environ, clean_env, clear=True):
            from app.tasks import broker
            from dramatiq.brokers.stub import StubBroker

            broker._broker = None
            result = broker.configure_broker()

            assert isinstance(result, StubBroker)

    def test_configure_broker_idempotent(self):
        """Should return same broker on subsequent calls."""
        with patch.dict(os.environ, {"DRAMATIQ_TEST_BROKER": "1"}):
            from app.tasks import broker

            broker._broker = None
            result1 = broker.configure_broker()
            result2 = broker.configure_broker()

            assert result1 is result2

    def test_configure_broker_with_explicit_url(self):
        """Should use explicitly provided URL over env var."""
        with patch("app.tasks.broker.RedisBroker") as mock_redis:
            mock_broker = MagicMock()
            mock_redis.return_value = mock_broker

            from app.tasks import broker
            broker._broker = None
            broker.configure_broker(redis_url="redis://custom:6379")

            # Verify RedisBroker was called with the custom URL
            call_kwargs = mock_redis.call_args
            assert "redis://custom:6379" in str(call_kwargs)

    def test_get_broker_creates_if_needed(self):
        """Should create broker if not configured."""
        with patch.dict(os.environ, {"DRAMATIQ_TEST_BROKER": "1"}):
            from app.tasks import broker
            from dramatiq.brokers.stub import StubBroker

            broker._broker = None
            result = broker.get_broker()

            assert isinstance(result, StubBroker)

    def test_get_broker_returns_existing(self):
        """Should return existing broker without reconfiguring."""
        from app.tasks import broker

        mock_broker = MagicMock()
        broker._broker = mock_broker

        result = broker.get_broker()
        assert result is mock_broker


class TestMiddlewareConfiguration:
    """Tests for middleware stack configuration."""

    def test_create_middleware_returns_list(self):
        """Should return list of middleware instances."""
        from app.tasks.broker import _create_middleware
        from dramatiq.middleware import (
            AgeLimit, TimeLimit, ShutdownNotifications,
            Callbacks, Pipelines, Retries
        )

        middleware = _create_middleware()

        assert len(middleware) == 6
        assert any(isinstance(m, AgeLimit) for m in middleware)
        assert any(isinstance(m, TimeLimit) for m in middleware)
        assert any(isinstance(m, ShutdownNotifications) for m in middleware)
        assert any(isinstance(m, Callbacks) for m in middleware)
        assert any(isinstance(m, Pipelines) for m in middleware)
        assert any(isinstance(m, Retries) for m in middleware)


class TestEnqueueFunction:
    """Tests for task enqueue functionality."""

    def test_enqueue_basic(self, stub_broker):
        """Should enqueue task to broker."""
        from app.tasks.broker import enqueue

        # Create a mock actor
        mock_actor = MagicMock()
        mock_message = MagicMock()
        mock_actor.send_with_options.return_value = mock_message

        with patch.object(stub_broker, 'get_actor', return_value=mock_actor):
            result = enqueue("process_webhook", "github", "push", {}, {})

            mock_actor.send_with_options.assert_called_once()
            assert result == mock_message

    def test_enqueue_with_delay(self, stub_broker):
        """Should pass delay option when enqueuing."""
        from app.tasks.broker import enqueue

        mock_actor = MagicMock()
        mock_message = MagicMock()
        mock_actor.send_with_options.return_value = mock_message

        with patch.object(stub_broker, 'get_actor', return_value=mock_actor):
            enqueue("process_webhook", "github", "push", {}, {}, delay=5000)

            call_kwargs = mock_actor.send_with_options.call_args
            assert call_kwargs.kwargs.get("delay") == 5000


# ============================================================================
# Webhook Processing Tests
# ============================================================================


class TestWebhookProcessing:
    """Tests for webhook processing task."""

    def test_process_github_push(self):
        """Should process GitHub push events with commit count."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("push", {"commits": [1, 2, 3]})

        assert result["status"] == "processed"
        assert result["commits"] == 3

    def test_process_github_push_empty_commits(self):
        """Should handle push with no commits."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("push", {})

        assert result["status"] == "processed"
        assert result["commits"] == 0

    def test_process_github_pull_request(self):
        """Should process GitHub PR events with action."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("pull_request", {"action": "opened"})

        assert result["status"] == "processed"
        assert result["action"] == "opened"

    def test_process_github_pull_request_no_action(self):
        """Should handle PR without action field."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("pull_request", {})

        assert result["status"] == "processed"
        assert result["action"] == ""

    def test_process_github_issues(self):
        """Should process GitHub issues events."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("issues", {"action": "closed"})

        assert result["status"] == "processed"
        assert result["action"] == "closed"

    def test_process_github_unknown_event(self):
        """Should ignore unknown GitHub events."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("unknown_event", {})

        assert result["status"] == "ignored"
        assert result["event"] == "unknown_event"

    def test_process_gitlab_push(self):
        """Should process GitLab push events."""
        from app.tasks.tasks import _process_gitlab_webhook

        result = _process_gitlab_webhook("push", {})

        assert result["status"] == "processed"
        assert result["event"] == "push"

    def test_process_gitlab_merge_request(self):
        """Should process GitLab merge request events."""
        from app.tasks.tasks import _process_gitlab_webhook

        result = _process_gitlab_webhook("merge_request", {})

        assert result["status"] == "processed"

    def test_process_gitlab_pipeline(self):
        """Should process GitLab pipeline events."""
        from app.tasks.tasks import _process_gitlab_webhook

        result = _process_gitlab_webhook("pipeline", {})

        assert result["status"] == "processed"

    def test_process_gitlab_unknown(self):
        """Should ignore unknown GitLab events."""
        from app.tasks.tasks import _process_gitlab_webhook

        result = _process_gitlab_webhook("unknown", {})

        assert result["status"] == "ignored"

    def test_process_pagerduty_events(self):
        """Should process PagerDuty events with message count."""
        from app.tasks.tasks import _process_pagerduty_webhook

        result = _process_pagerduty_webhook("incident.trigger", {"messages": [1, 2]})

        assert result["status"] == "processed"
        assert result["messages"] == 2

    def test_process_pagerduty_no_messages(self):
        """Should handle PagerDuty event without messages."""
        from app.tasks.tasks import _process_pagerduty_webhook

        result = _process_pagerduty_webhook("incident.trigger", {})

        assert result["status"] == "processed"
        assert result["messages"] == 0

    def test_process_slack_events(self):
        """Should process Slack events."""
        from app.tasks.tasks import _process_slack_webhook

        result = _process_slack_webhook("app_mention", {})

        assert result["status"] == "processed"
        assert result["event"] == "app_mention"


class TestProcessWebhookTask:
    """Tests for the main process_webhook actor."""

    def test_process_webhook_github(self, stub_broker):
        """Should route GitHub webhooks correctly."""
        from app.tasks.tasks import process_webhook

        result = process_webhook.fn(
            source="github",
            event_type="push",
            payload={"commits": [1]},
            headers={}
        )

        assert result["status"] == "processed"
        assert result["commits"] == 1

    def test_process_webhook_gitlab(self, stub_broker):
        """Should route GitLab webhooks correctly."""
        from app.tasks.tasks import process_webhook

        result = process_webhook.fn(
            source="gitlab",
            event_type="push",
            payload={},
            headers={}
        )

        assert result["status"] == "processed"

    def test_process_webhook_pagerduty(self, stub_broker):
        """Should route PagerDuty webhooks correctly."""
        from app.tasks.tasks import process_webhook

        result = process_webhook.fn(
            source="pagerduty",
            event_type="incident",
            payload={"messages": []},
            headers={}
        )

        assert result["status"] == "processed"

    def test_process_webhook_slack(self, stub_broker):
        """Should route Slack webhooks correctly."""
        from app.tasks.tasks import process_webhook

        result = process_webhook.fn(
            source="slack",
            event_type="event_callback",
            payload={},
            headers={}
        )

        assert result["status"] == "processed"

    def test_process_webhook_unknown_source(self, stub_broker):
        """Should handle unknown webhook sources."""
        from app.tasks.tasks import process_webhook

        result = process_webhook.fn(
            source="unknown",
            event_type="test",
            payload={},
            headers={}
        )

        assert result["status"] == "ignored"
        assert "unknown source" in result["reason"]

    def test_process_webhook_exception_handling(self, stub_broker):
        """Should re-raise exceptions for retry."""
        from app.tasks.tasks import process_webhook

        with patch("app.tasks.tasks._process_github_webhook") as mock:
            mock.side_effect = ValueError("Processing failed")

            with pytest.raises(ValueError, match="Processing failed"):
                process_webhook.fn(
                    source="github",
                    event_type="push",
                    payload={},
                    headers={}
                )


# ============================================================================
# Notification Tests
# ============================================================================


class TestSlackNotification:
    """Tests for Slack notification functionality."""

    @pytest.mark.asyncio
    async def test_send_slack_notification_no_token(self, mock_settings):
        """Should skip Slack when no token configured."""
        from app.tasks.tasks import _send_slack_notification

        with patch("app.tasks.tasks.get_settings", return_value=mock_settings):
            result = await _send_slack_notification("Test message", "#general", {})

            assert result["status"] == "skipped"
            assert result["reason"] == "no slack token"

    @pytest.mark.asyncio
    async def test_send_slack_notification_no_channel(self, mock_settings):
        """Should skip Slack when no channel configured."""
        from app.tasks.tasks import _send_slack_notification

        mock_settings.slack_bot_token = "xoxb-test-token"
        mock_settings.slack_channel = ""

        with patch("app.config.get_settings", return_value=mock_settings):
            result = await _send_slack_notification("Test message", None, {})

            assert result["status"] == "skipped"
            assert result["reason"] == "no channel"

    @pytest.mark.asyncio
    async def test_send_slack_notification_success(self, mock_settings):
        """Should send Slack notification successfully."""
        from app.tasks.tasks import _send_slack_notification

        mock_settings.slack_bot_token = "xoxb-test-token"
        mock_settings.slack_channel = "#default"

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "ts": "1234567890.123456"}

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await _send_slack_notification(
                    "Test message",
                    "#test-channel",
                    {"thread_ts": "1234"}
                )

                assert result["status"] == "sent"
                assert result["ts"] == "1234567890.123456"
                mock_instance.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_slack_notification_failure(self, mock_settings):
        """Should handle Slack API failure."""
        from app.tasks.tasks import _send_slack_notification

        mock_settings.slack_bot_token = "xoxb-test-token"
        mock_settings.slack_channel = "#default"

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await _send_slack_notification("Test", "#invalid", {})

                assert result["status"] == "failed"


class TestPagerDutyNotification:
    """Tests for PagerDuty notification functionality."""

    @pytest.mark.asyncio
    async def test_send_pagerduty_notification_no_token(self, mock_settings):
        """Should skip PagerDuty when no token configured."""
        from app.tasks.tasks import _send_pagerduty_notification

        with patch("app.config.get_settings", return_value=mock_settings):
            result = await _send_pagerduty_notification("Test alert", {})

            assert result["status"] == "skipped"
            assert result["reason"] == "no pagerduty token"

    @pytest.mark.asyncio
    async def test_send_pagerduty_notification_success(self, mock_settings):
        """Should send PagerDuty event successfully."""
        from app.tasks.tasks import _send_pagerduty_notification

        mock_settings.pagerduty_api_token = "pd-routing-key"

        mock_response = MagicMock()
        mock_response.status_code = 202

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await _send_pagerduty_notification(
                    "High CPU alert",
                    {"severity": "critical", "action": "trigger"}
                )

                assert result["status"] == "sent"
                mock_instance.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_pagerduty_notification_failure(self, mock_settings):
        """Should handle PagerDuty API failure."""
        from app.tasks.tasks import _send_pagerduty_notification

        mock_settings.pagerduty_api_token = "pd-routing-key"

        mock_response = MagicMock()
        mock_response.status_code = 400

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await _send_pagerduty_notification("Test", {})

                assert result["status"] == "failed"


class TestSendNotificationTask:
    """Tests for the main send_notification actor."""

    def test_send_notification_slack(
        self, stub_broker, mock_settings, event_loop_for_sync
    ):
        """Should send Slack notification via actor."""
        from app.tasks.tasks import send_notification

        mock_settings.slack_bot_token = "xoxb-test"
        mock_settings.slack_channel = "#default"

        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "ts": "123"}

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = send_notification.fn(
                    channel="slack",
                    message="Test",
                    recipient="#test",
                    metadata={}
                )

                assert result["status"] == "sent"

    def test_send_notification_unsupported_channel(
        self, stub_broker, mock_settings, event_loop_for_sync
    ):
        """Should handle unsupported notification channels."""
        from app.tasks.tasks import send_notification

        # Need to mock get_settings even for unsupported channel
        # because send_notification still imports it
        with patch("app.config.get_settings", return_value=mock_settings):
            result = send_notification.fn(
                channel="email",
                message="Test",
                recipient="user@example.com",
                metadata={}
            )

            assert result["status"] == "unsupported"
            assert result["channel"] == "email"


# ============================================================================
# Alert Evaluation Tests
# ============================================================================


class TestAlertEvaluation:
    """Tests for alert evaluation task."""

    def test_process_alert_evaluation(self, stub_broker):
        """Should evaluate alerts for services."""
        from app.tasks.tasks import process_alert_evaluation

        mock_engine = MagicMock()
        mock_engine.evaluate_all.return_value = []

        with patch("app.alerting.get_engine", return_value=mock_engine):
            result = process_alert_evaluation.fn(["service1", "service2"])

            assert result["status"] == "completed"
            assert result["services_evaluated"] == 2
            assert result["alerts_fired"] == 0
            mock_engine.evaluate_all.assert_called_once_with(["service1", "service2"])

    def test_process_alert_evaluation_with_alerts(self, stub_broker):
        """Should report fired alerts count."""
        from app.tasks.tasks import process_alert_evaluation

        mock_engine = MagicMock()
        mock_engine.evaluate_all.return_value = [
            MagicMock(), MagicMock(), MagicMock()  # 3 alerts fired
        ]

        with patch("app.alerting.get_engine", return_value=mock_engine):
            result = process_alert_evaluation.fn(["service1"])

            assert result["alerts_fired"] == 3

    def test_process_alert_evaluation_with_timestamp(self, stub_broker):
        """Should use provided timestamp."""
        from app.tasks.tasks import process_alert_evaluation

        mock_engine = MagicMock()
        mock_engine.evaluate_all.return_value = []

        with patch("app.alerting.get_engine", return_value=mock_engine):
            result = process_alert_evaluation.fn(
                ["service1"],
                timestamp="2024-01-15T10:00:00"
            )

            assert result["timestamp"] == "2024-01-15T10:00:00"

    def test_process_alert_evaluation_empty_services(self, stub_broker):
        """Should handle empty services list."""
        from app.tasks.tasks import process_alert_evaluation

        mock_engine = MagicMock()
        mock_engine.evaluate_all.return_value = []

        with patch("app.alerting.get_engine", return_value=mock_engine):
            result = process_alert_evaluation.fn([])

            assert result["services_evaluated"] == 0


# ============================================================================
# Usage Sync Tests
# ============================================================================


class TestUsageSync:
    """Tests for usage sync task."""

    def test_sync_usage_no_stripe_key(
        self, stub_broker, mock_settings, event_loop_for_sync
    ):
        """Should skip sync when no Stripe key."""
        from app.tasks.tasks import sync_usage_to_stripe

        with patch("app.config.get_settings", return_value=mock_settings):
            result = sync_usage_to_stripe.fn("org1", "2024-03", 10, 1000)

            assert result["status"] == "skipped"
            assert result["reason"] == "no stripe key"

    def test_sync_usage_success(
        self, stub_broker, mock_settings, event_loop_for_sync
    ):
        """Should sync usage to Stripe successfully."""
        from app.tasks.tasks import sync_usage_to_stripe

        mock_settings.stripe_api_key = "sk_test_123"

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = sync_usage_to_stripe.fn("org1", "2024-03", 10, 1000)

                assert result["status"] == "synced"
                assert result["code"] == 200

    def test_sync_usage_failure(
        self, stub_broker, mock_settings, event_loop_for_sync
    ):
        """Should handle Stripe API failure."""
        from app.tasks.tasks import sync_usage_to_stripe

        mock_settings.stripe_api_key = "sk_test_123"

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("app.config.get_settings", return_value=mock_settings):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post.return_value = mock_response
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = sync_usage_to_stripe.fn("org1", "2024-03", 10, 1000)

                assert result["status"] == "failed"
                assert result["code"] == 500


# ============================================================================
# Session Cleanup Tests
# ============================================================================


class TestSessionCleanup:
    """Tests for session cleanup task."""

    def test_cleanup_expired_sessions_default(self, stub_broker):
        """Should cleanup sessions with default age."""
        from app.tasks.tasks import cleanup_expired_sessions

        result = cleanup_expired_sessions.fn()

        assert result["status"] == "completed"
        assert result["cleaned"] == 0

    def test_cleanup_expired_sessions_custom_age(self, stub_broker):
        """Should cleanup sessions with custom age."""
        from app.tasks.tasks import cleanup_expired_sessions

        result = cleanup_expired_sessions.fn(max_age_hours=48)

        assert result["status"] == "completed"




# ============================================================================
# Worker Tests
# ============================================================================


class TestWorkerConfiguration:
    """Tests for worker configuration and runner."""

    def test_start_worker_configures_broker(self):
        """Should configure broker before starting."""
        from app.tasks import worker

        with patch("app.tasks.broker.configure_broker") as mock_configure:
            with patch("dramatiq.cli.main") as mock_main:
                mock_main.side_effect = KeyboardInterrupt()

                worker.start_worker(processes=1, threads=4)

                mock_configure.assert_called_once()

    def test_start_worker_with_queues(self):
        """Should pass queue arguments to dramatiq."""
        from app.tasks import worker

        with patch("app.tasks.broker.configure_broker"):
            with patch("dramatiq.cli.main") as mock_main:
                mock_main.side_effect = KeyboardInterrupt()

                worker.start_worker(
                    queues=["webhooks", "notifications"],
                    processes=2,
                    threads=8
                )

                # Check sys.argv was set correctly
                assert "--queues" in sys.argv
                assert "webhooks" in sys.argv
                assert "notifications" in sys.argv

    def test_start_worker_default_args(self):
        """Should use default process/thread counts."""
        from app.tasks import worker

        with patch("app.tasks.broker.configure_broker"):
            with patch("dramatiq.cli.main") as mock_main:
                mock_main.side_effect = KeyboardInterrupt()

                worker.start_worker()

                assert "--processes" in sys.argv
                assert "2" in sys.argv
                assert "--threads" in sys.argv
                assert "8" in sys.argv


class TestSchedulerConfiguration:
    """Tests for scheduler configuration."""

    def test_run_scheduler_adds_jobs(self):
        """Should add periodic jobs to scheduler."""
        from app.tasks import worker

        mock_scheduler = MagicMock()

        with patch("apscheduler.schedulers.blocking.BlockingScheduler",
                   return_value=mock_scheduler):
            with patch("signal.signal"):
                # Make start() exit immediately
                mock_scheduler.start.side_effect = KeyboardInterrupt()

                try:
                    worker.run_scheduler()
                except KeyboardInterrupt:
                    pass

                # Verify jobs were added
                assert mock_scheduler.add_job.call_count == 2

                # Check job IDs
                job_ids = [
                    call.kwargs.get("id")
                    for call in mock_scheduler.add_job.call_args_list
                ]
                assert "alert_evaluation" in job_ids
                assert "session_cleanup" in job_ids

    def test_scheduler_signal_handling(self):
        """Should register signal handlers."""
        from app.tasks import worker

        mock_scheduler = MagicMock()

        with patch("apscheduler.schedulers.blocking.BlockingScheduler",
                   return_value=mock_scheduler):
            with patch("signal.signal") as mock_signal:
                mock_scheduler.start.side_effect = KeyboardInterrupt()

                try:
                    worker.run_scheduler()
                except KeyboardInterrupt:
                    pass

                # Verify SIGINT and SIGTERM handlers registered
                signal_calls = [call[0][0] for call in mock_signal.call_args_list]
                assert signal.SIGINT in signal_calls
                assert signal.SIGTERM in signal_calls


# ============================================================================
# Actor Decorator Tests
# ============================================================================


class TestActorDecorators:
    """Tests for actor configuration via decorators."""

    def test_process_webhook_actor_config(self, stub_broker):
        """Should have correct queue and retry config."""
        from app.tasks.tasks import process_webhook

        assert process_webhook.queue_name == "webhooks"
        assert process_webhook.options.get("max_retries") == 3

    def test_run_investigation_actor_config(self, stub_broker):
        """Should have time limit for long-running investigations."""
        from app.tasks.tasks import run_investigation

        assert run_investigation.queue_name == "investigations"
        assert run_investigation.options.get("time_limit") == 300000

    def test_send_notification_actor_config(self, stub_broker):
        """Should have high retry count for notifications."""
        from app.tasks.tasks import send_notification

        assert send_notification.queue_name == "notifications"
        assert send_notification.options.get("max_retries") == 5

    def test_cleanup_expired_sessions_actor_config(self, stub_broker):
        """Should have low retry count for maintenance."""
        from app.tasks.tasks import cleanup_expired_sessions

        assert cleanup_expired_sessions.queue_name == "maintenance"
        assert cleanup_expired_sessions.options.get("max_retries") == 1

    def test_execute_runbook_actor_config(self, stub_broker):
        """Should have long time limit for runbooks."""
        from app.tasks.tasks import execute_runbook_async

        assert execute_runbook_async.queue_name == "runbooks"
        assert execute_runbook_async.options.get("time_limit") == 600000


# ============================================================================
# Module Exports Tests
# ============================================================================


class TestModuleExports:
    """Tests for module-level exports."""

    def test_init_exports_broker_functions(self):
        """Should export broker configuration functions."""
        from app.tasks import get_broker, configure_broker

        assert callable(get_broker)
        assert callable(configure_broker)

    def test_init_exports_worker_function(self):
        """Should export worker start function."""
        from app.tasks import start_worker

        assert callable(start_worker)

    def test_init_exports_all_tasks(self):
        """Should export all task actors."""
        from app.tasks import (
            process_webhook,
            run_investigation,
            send_notification,
            sync_usage_to_stripe,
            cleanup_expired_sessions,
            process_alert_evaluation,
            execute_runbook_async,
        )

        # Verify all are dramatiq actors
        assert hasattr(process_webhook, 'send')
        assert hasattr(run_investigation, 'send')
        assert hasattr(send_notification, 'send')
        assert hasattr(sync_usage_to_stripe, 'send')
        assert hasattr(cleanup_expired_sessions, 'send')
        assert hasattr(process_alert_evaluation, 'send')
        assert hasattr(execute_runbook_async, 'send')
