"""External delivery / trigger integrations (Slack, PagerDuty webhooks).

These are pure helpers — signature verification, payload parsing, and message
building — with no dependency on the agent or FastAPI app, so they're easy to
unit-test. The API layer (app/api/main.py) wires them to the agent.
"""

from app.integrations.slack_bot import (
    SlackAction,
    SlackBlockBuilder,
    SlackBot,
    SlackChannel,
    SlackCommand,
    SlackEventType,
    SlackMessage,
    SlackUser,
    get_slack_bot,
)

__all__ = [
    "SlackAction",
    "SlackBlockBuilder",
    "SlackBot",
    "SlackChannel",
    "SlackCommand",
    "SlackEventType",
    "SlackMessage",
    "SlackUser",
    "get_slack_bot",
]
