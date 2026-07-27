"""Tests for background task system."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

pytest.importorskip("dramatiq", reason="dramatiq not installed")


class TestBrokerConfiguration:
    """Tests for broker configuration."""

    def test_configure_broker_stub_for_testing(self):
        """Should use stub broker in test mode."""
        with patch.dict("os.environ", {"DRAMATIQ_TEST_BROKER": "1"}):
            from app.tasks import broker

            broker._broker = None
            result = broker.configure_broker()

            from dramatiq.brokers.stub import StubBroker
            assert isinstance(result, StubBroker)

            broker._broker = None

    def test_configure_broker_redis(self):
        """Should use Redis broker with URL."""
        with patch.dict("os.environ", {"COPILOT_REDIS_URL": "redis://localhost:6379"}):
            with patch("app.tasks.broker.RedisBroker") as mock:
                mock.return_value = MagicMock()

                from app.tasks import broker
                broker._broker = None
                broker.configure_broker()

                mock.assert_called_once()
                broker._broker = None

    def test_configure_broker_fallback_to_stub(self):
        """Should fallback to stub when no Redis URL."""
        with patch.dict("os.environ", {}, clear=True):
            from app.tasks import broker

            broker._broker = None
            result = broker.configure_broker()

            from dramatiq.brokers.stub import StubBroker
            assert isinstance(result, StubBroker)

            broker._broker = None


class TestWebhookProcessing:
    """Tests for webhook processing task."""

    def test_process_github_push(self):
        """Should process GitHub push events."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("push", {"commits": [1, 2, 3]})

        assert result["status"] == "processed"
        assert result["commits"] == 3

    def test_process_github_pull_request(self):
        """Should process GitHub PR events."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("pull_request", {"action": "opened"})

        assert result["status"] == "processed"
        assert result["action"] == "opened"

    def test_process_github_unknown_event(self):
        """Should ignore unknown GitHub events."""
        from app.tasks.tasks import _process_github_webhook

        result = _process_github_webhook("unknown_event", {})

        assert result["status"] == "ignored"

    def test_process_gitlab_events(self):
        """Should process GitLab events."""
        from app.tasks.tasks import _process_gitlab_webhook

        for event in ["push", "merge_request", "pipeline"]:
            result = _process_gitlab_webhook(event, {})
            assert result["status"] == "processed"

    def test_process_pagerduty_events(self):
        """Should process PagerDuty events."""
        from app.tasks.tasks import _process_pagerduty_webhook

        result = _process_pagerduty_webhook("incident", {"messages": [1, 2]})

        assert result["status"] == "processed"
        assert result["messages"] == 2


class TestNotificationTasks:
    """Tests for notification tasks."""

    @pytest.mark.asyncio
    async def test_send_slack_notification_no_token(self):
        """Should skip Slack when no token."""
        from app.tasks.tasks import _send_slack_notification

        with patch("app.tasks.tasks.get_settings") as mock:
            mock.return_value.slack_bot_token = ""
            result = await _send_slack_notification("Test", "#general", {})

            assert result["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_send_pagerduty_notification_no_token(self):
        """Should skip PagerDuty when no token."""
        from app.tasks.tasks import _send_pagerduty_notification

        with patch("app.tasks.tasks.get_settings") as mock:
            mock.return_value.pagerduty_api_token = ""
            result = await _send_pagerduty_notification("Test", {})

            assert result["status"] == "skipped"


class TestAlertEvaluation:
    """Tests for alert evaluation task."""

    def test_process_alert_evaluation(self):
        """Should evaluate alerts for services."""
        from app.tasks.tasks import process_alert_evaluation

        with patch("app.tasks.tasks.get_engine") as mock:
            mock_engine = MagicMock()
            mock_engine.evaluate_all.return_value = []
            mock.return_value = mock_engine

            with patch("app.tasks.tasks.get_broker"):
                with patch("dramatiq.actor"):
                    result = process_alert_evaluation.fn(["service1", "service2"])

                    assert result["status"] == "completed"
                    assert result["services_evaluated"] == 2


class TestUsageSync:
    """Tests for usage sync task."""

    def test_sync_usage_no_stripe(self):
        """Should skip sync when no Stripe key."""
        from app.tasks.tasks import sync_usage_to_stripe

        with patch("app.tasks.tasks.get_settings") as mock:
            mock.return_value.stripe_api_key = ""

            with patch("app.tasks.tasks.get_broker"):
                with patch("dramatiq.actor"):
                    result = sync_usage_to_stripe.fn("org1", "2024-03", 10, 1000)

                    assert result["status"] == "skipped"


class TestRunbookExecution:
    """Tests for runbook execution task."""

    @pytest.mark.asyncio
    async def test_execute_runbook_async(self):
        """Should execute runbook asynchronously."""
        from app.tasks.tasks import execute_runbook_async

        with patch("app.tasks.tasks.RunbookExecutor") as mock_executor:
            mock_execution = MagicMock()
            mock_execution.id = "exec-123"
            mock_execution.status.value = "completed"
            mock_execution.started_at.isoformat.return_value = "2024-01-01T00:00:00"

            mock_instance = AsyncMock()
            mock_instance.execute.return_value = mock_execution
            mock_executor.return_value = mock_instance

            with patch("app.tasks.tasks.get_broker"):
                with patch("dramatiq.actor"):
                    result = execute_runbook_async.fn(
                        "restart-service",
                        {"service": "api"},
                        "user123",
                    )

                    assert result["runbook_id"] == "restart-service"
                    assert result["status"] == "completed"
