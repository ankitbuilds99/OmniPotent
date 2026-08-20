"""The dispatcher's channel handlers must actually be wired, and wired correctly.

`NotificationDispatcher` delivers through handler slots that nothing in the
codebase ever populated, so SLACK, WEBHOOK, SMS, VOICE, WHATSAPP and TEAMS all
reported "<channel> not configured" no matter how the deployment was configured.
These tests pin the wiring and, more importantly, the two ways it can be wired
wrongly: the per-channel return-type contract, and failing open.
"""

from __future__ import annotations

import pytest

from app.notifications import dispatcher as disp
from app.notifications.handlers import install_default_handlers

# Channels whose delivery branch requires a populated handler slot.
HANDLER_BACKED_CHANNELS = ["slack", "webhook", "sms", "voice", "whatsapp", "teams"]

# The dispatcher consumes SLACK/WEBHOOK/SMS results directly as booleans and reads
# VOICE/WHATSAPP/TEAMS results with .get(). Returning the wrong shape does not
# raise — a dict is truthy — so a failed send would be recorded as delivered.
BOOL_RETURNING = ["slack", "webhook", "sms"]
MAPPING_RETURNING = ["voice", "whatsapp", "teams"]

CALL_ARGS = {
    "slack": {"user_id": "U123", "text": "hi"},
    "webhook": {"notification_id": "n1", "title": "t", "body": "b", "data": {}},
    "sms": {"phone": "+15550000000", "message": "m"},
    "voice": {"phone": "+15550000000", "message": "m"},
    "whatsapp": {"phone": "+15550000000", "message": "m"},
    "teams": {"webhook_url": None, "title": "t", "body": "b"},
}


@pytest.fixture(autouse=True)
def _reset_dispatcher_singleton():
    """Clear the module-level dispatcher so each test builds a fresh one."""
    disp._dispatcher = None
    yield
    disp._dispatcher = None


@pytest.fixture
def unconfigured(monkeypatch):
    """Strip every credential these handlers could pick up from the environment.

    Without this the result depends on whoever runs the suite: a developer with
    TWILIO_* or TEAMS_WEBHOOK_URL exported would exercise a different path than CI.
    """
    for var in (
        "SLACK_BOT_TOKEN",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "TWILIO_WHATSAPP_NUMBER",
        "TEAMS_WEBHOOK_URL",
        "TEAMS_BOT_ID",
        "TEAMS_BOT_PASSWORD",
        "COPILOT_NOTIFICATION_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    from app import config as cfg

    cfg.get_settings.cache_clear()
    yield
    cfg.get_settings.cache_clear()


@pytest.mark.parametrize("channel", HANDLER_BACKED_CHANNELS)
def test_handler_slot_is_populated(channel: str) -> None:
    """Every handler-backed channel has a handler after get_dispatcher()."""
    d = disp.get_dispatcher()
    assert getattr(d, f"{channel}_handler") is not None, (
        f"{channel}_handler is unset, so the {channel.upper()} branch will return "
        f"'{channel} not configured' for every notification"
    )


def test_get_dispatcher_installs_handlers_not_just_constructor() -> None:
    """A bare NotificationDispatcher stays unwired; get_dispatcher() is the seam."""
    bare = disp.NotificationDispatcher()
    assert bare.slack_handler is None
    assert disp.get_dispatcher().slack_handler is not None


def test_install_does_not_overwrite_a_caller_supplied_handler() -> None:
    """A handler set before first use must survive installation."""

    async def custom(**_):
        return True

    d = disp.NotificationDispatcher()
    d.set_slack_handler(custom)
    install_default_handlers(d)
    assert d.slack_handler is custom


def test_discord_and_telegram_slots_left_alone() -> None:
    """Those branches call their sender modules directly and ignore the slot.

    Filling them would imply a wiring that does not exist — the value would never
    be read, so a caller inspecting the slot would draw the wrong conclusion.
    """
    d = disp.get_dispatcher()
    assert d.discord_handler is None
    assert d.telegram_handler is None


@pytest.mark.parametrize("channel", BOOL_RETURNING)
async def test_bool_returning_handlers_return_bool(channel: str, unconfigured) -> None:
    """SLACK/WEBHOOK/SMS results are used directly as booleans."""
    handler = getattr(disp.get_dispatcher(), f"{channel}_handler")
    result = await handler(**CALL_ARGS[channel])
    assert isinstance(result, bool), (
        f"{channel}_handler returned {type(result).__name__}; the dispatcher assigns "
        f"this straight to `success`, so a truthy non-bool would record a failed "
        f"send as delivered"
    )


@pytest.mark.parametrize("channel", MAPPING_RETURNING)
async def test_mapping_returning_handlers_return_mapping(channel: str, unconfigured) -> None:
    """VOICE/WHATSAPP/TEAMS results are read with .get('success')."""
    handler = getattr(disp.get_dispatcher(), f"{channel}_handler")
    result = await handler(**CALL_ARGS[channel])
    assert isinstance(result, dict), (
        f"{channel}_handler returned {type(result).__name__}; the dispatcher calls "
        f".get('success') on it, which would raise on a bool"
    )
    assert "success" in result


@pytest.mark.parametrize("channel", HANDLER_BACKED_CHANNELS)
async def test_handlers_fail_closed_when_unconfigured(channel: str, unconfigured) -> None:
    """No credentials must mean an explicit failure, never a reported success."""
    handler = getattr(disp.get_dispatcher(), f"{channel}_handler")
    result = await handler(**CALL_ARGS[channel])
    success = result.get("success") if isinstance(result, dict) else result
    assert success is False, (
        f"{channel}_handler reported success with no credentials configured"
    )


@pytest.mark.parametrize("channel", HANDLER_BACKED_CHANNELS)
async def test_handlers_never_raise(channel: str, unconfigured) -> None:
    """Handlers must not propagate: the dispatcher would store the traceback text.

    `_deliver` wraps delivery in `except Exception` and records `str(e)` as the
    error, so a raising handler replaces an actionable reason ("Teams webhook not
    configured") with whatever the exception happened to stringify to.
    """
    handler = getattr(disp.get_dispatcher(), f"{channel}_handler")
    await handler(**CALL_ARGS[channel])  # must not raise


async def test_teams_accepts_per_user_webhook_url(monkeypatch, unconfigured) -> None:
    """The regression that motivated this: a configured user still got an error.

    The TEAMS guard accepted `prefs.teams_webhook_url`, but the delivery branch
    then required the handler slot — which was never set — so a user with a valid
    webhook URL received "Teams handler not set".
    """
    posted: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            posted["url"] = url
            posted["json"] = kwargs.get("json")
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    handler = disp.get_dispatcher().teams_handler
    result = await handler(
        webhook_url="https://outlook.office.com/webhook/per-user",
        title="Disk pressure",
        body="node-3 at 91%",
    )

    assert result["success"] is True
    assert posted["url"] == "https://outlook.office.com/webhook/per-user"
    assert "Disk pressure" in posted["json"]["text"]


async def test_webhook_channel_posts_to_configured_url(monkeypatch) -> None:
    """The generic webhook channel targets deployment-level configuration."""
    monkeypatch.setenv("COPILOT_NOTIFICATION_WEBHOOK_URL", "https://example.test/hook")
    from app import config as cfg

    cfg.get_settings.cache_clear()

    posted: dict = {}

    class _Resp:
        status_code = 202

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            posted["url"] = url
            posted["json"] = kwargs.get("json")
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    try:
        result = await disp.get_dispatcher().webhook_handler(
            notification_id="n-1", title="t", body="b", data={"k": "v"}
        )
        assert result is True
        assert posted["url"] == "https://example.test/hook"
        assert posted["json"]["notification_id"] == "n-1"
    finally:
        cfg.get_settings.cache_clear()
