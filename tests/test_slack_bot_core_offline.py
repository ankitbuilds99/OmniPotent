"""SlackBot core fabricated success when no client is configured (C1).

With SLACK_BOT_TOKEN unset, every messaging method used to return a fabricated
`ok: True` — `send_message` minted a `ts` from the wall clock, `create_channel`
invented a channel id (`C{NAME}`), and the rest returned bare `{"ok": True}`.
Incident war-rooms and approval requests therefore reported success while
nothing was sent to Slack.

The contract (app/config.py): fabrication is OFF by default — an unconfigured
bot fails closed with a not-configured error; only under COPILOT_DEMO_MODE=true
may a simulated payload be returned, and then explicitly labelled.
"""

from __future__ import annotations

import pytest

import app.config as cfg
from app.slack_bot.core import SlackBot, SlackBotConfig


@pytest.fixture
def offline_bot(monkeypatch) -> SlackBot:
    """A bot with no token — the offline/unconfigured case, demo mode off."""
    monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
    cfg.get_settings.cache_clear()
    bot = SlackBot(SlackBotConfig(bot_token="", app_token="", signing_secret=""))
    yield bot
    cfg.get_settings.cache_clear()


@pytest.fixture
def demo_bot(monkeypatch) -> SlackBot:
    """Same unconfigured bot, but with the operator opted into demo mode."""
    monkeypatch.setenv("COPILOT_DEMO_MODE", "true")
    cfg.get_settings.cache_clear()
    bot = SlackBot(SlackBotConfig(bot_token="", app_token="", signing_secret=""))
    yield bot
    monkeypatch.delenv("COPILOT_DEMO_MODE", raising=False)
    cfg.get_settings.cache_clear()


class TestFailClosedWithoutClient:
    """No client + no demo mode = an honest not-configured error."""

    async def test_send_message_does_not_fabricate_a_ts(self, offline_bot):
        result = await offline_bot.send_message("C123", "war room update")

        assert result["ok"] is False
        assert result["error"] == "slack_not_configured"
        assert "ts" not in result
        assert "message" not in result

    async def test_send_ephemeral_fails_closed(self, offline_bot):
        result = await offline_bot.send_ephemeral("C123", "U1", "psst")

        assert result["ok"] is False
        assert result["error"] == "slack_not_configured"
        assert "message_ts" not in result

    async def test_update_message_fails_closed(self, offline_bot):
        result = await offline_bot.update_message("C123", "1.0", "edited")

        assert result["ok"] is False
        assert result["error"] == "slack_not_configured"

    async def test_create_channel_does_not_invent_a_channel_id(self, offline_bot):
        """The old fallback announced channel `CINC-CHEC` for a war room that
        was never created — and `/warroom list` then failed to find it."""
        result = await offline_bot.create_channel("inc-checkout")

        assert result["ok"] is False
        assert result["error"] == "slack_not_configured"
        assert "channel" not in result

    async def test_the_remaining_operations_fail_closed_too(self, offline_bot):
        for coro in (
            offline_bot.invite_to_channel("C123", ["U1"]),
            offline_bot.set_channel_topic("C123", "incident"),
            offline_bot.pin_message("C123", "1.0"),
            offline_bot.archive_channel("C123"),
        ):
            result = await coro
            assert result["ok"] is False
            assert result["error"] == "slack_not_configured"

    async def test_the_error_says_what_to_configure(self, offline_bot):
        result = await offline_bot.send_message("C123", "hi")
        assert "SLACK_BOT_TOKEN" in result["detail"]


class TestDemoModeIsLabelled:
    """Demo mode may simulate, but every payload says so."""

    async def test_send_message_is_marked_simulated(self, demo_bot):
        result = await demo_bot.send_message("C123", "demo update")

        assert result["ok"] is True
        assert result["simulated"] is True
        assert "SIMULATED" in result["notice"]
        assert "nothing was sent to Slack" in result["notice"]

    async def test_create_channel_is_marked_simulated(self, demo_bot):
        result = await demo_bot.create_channel("inc-demo")

        assert result["ok"] is True
        assert result["simulated"] is True
        assert result["channel"]["name"] == "inc-demo"
        assert "SIMULATED" in result["notice"]


class TestRealClientPathUntouched:
    """With a client configured, the real response passes through unlabelled."""

    async def test_send_message_returns_the_client_response(self, offline_bot):
        class FakeResponse:
            data = {"ok": True, "channel": "C123", "ts": "1726000000.000100"}

        class FakeClient:
            async def send_message(self, **kwargs):
                return FakeResponse()

        offline_bot._client = FakeClient()

        result = await offline_bot.send_message("C123", "real")

        assert result == FakeResponse.data
        assert "simulated" not in result
