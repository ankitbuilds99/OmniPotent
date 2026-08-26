"""The channel catalogue and the org-scoped store behind it.

The Notification Preferences page invented all of this in the browser: nine
`ChannelConfig` literals with `configured: true` hardcoded on the first two, and
eight `NotificationRule` literals. Nothing was ever read from or written to the
server, so "Slack is configured" was a claim the page made about itself.

Here the catalogue is server-side (it is a property of the build, not of the
browser), `configured` is computed from the values actually stored, and every
row is scoped to `org_uuid()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database.orgs import ensure_org_row, org_uuid


@dataclass(frozen=True)
class ConfigField:
    """One setting a channel needs before it can deliver anything."""

    key: str
    label: str
    type: str
    secret: bool = False
    required: bool = True


@dataclass(frozen=True)
class ChannelSpec:
    id: str
    name: str
    description: str
    icon: str
    fields: tuple[ConfigField, ...] = field(default_factory=tuple)


# Channels this build can actually deliver to, in display order.
CHANNELS: tuple[ChannelSpec, ...] = (
    ChannelSpec(
        "email", "Email", "Email notifications", "mail",
        (ConfigField("address", "Email Address", "email"),),
    ),
    ChannelSpec(
        "slack", "Slack", "Slack workspace messages", "message-square",
        (ConfigField("webhook", "Webhook URL", "url", secret=True),),
    ),
    ChannelSpec(
        "pagerduty", "PagerDuty", "PagerDuty incidents", "phone",
        (ConfigField("routing_key", "Routing Key", "password", secret=True),),
    ),
    ChannelSpec(
        "teams", "MS Teams", "Microsoft Teams channels", "message-square",
        (ConfigField("webhook", "Webhook URL", "url", secret=True),),
    ),
    ChannelSpec(
        "discord", "Discord", "Discord webhooks", "message-square",
        (ConfigField("webhook", "Webhook URL", "url", secret=True),),
    ),
    ChannelSpec(
        "sms", "SMS", "Text message alerts", "smartphone",
        (ConfigField("phone", "Phone Number", "tel"),),
    ),
    ChannelSpec("push", "Push", "Browser push notifications", "bell", ()),
    ChannelSpec(
        "webhook", "Webhook", "Custom webhook endpoint", "bell",
        (
            ConfigField("url", "Endpoint URL", "url"),
            ConfigField("secret", "HMAC Secret", "password", secret=True, required=False),
        ),
    ),
    ChannelSpec(
        "jira", "Jira", "Create Jira tickets", "alert-triangle",
        (ConfigField("project", "Project Key", "text"),),
    ),
)

CHANNELS_BY_ID: dict[str, ChannelSpec] = {c.id: c for c in CHANNELS}


@dataclass(frozen=True)
class EventSpec:
    id: str
    description: str
    #: Channels this event routes to for an org that has never edited its rules.
    default_channels: tuple[str, ...]
    #: Set when nothing in this build can raise the event, and why. The API sends
    #: it on so the page can say so next to the row. A routing matrix that offers
    #: a switch for something that can never fire is the same untruth as a Save
    #: button that saves nothing, just harder to notice — the org only finds out
    #: by not being told about the thing it asked to be told about.
    unproduced: str = ""


EVENTS: tuple[EventSpec, ...] = (
    EventSpec("incident_created", "New incident created", ("email", "slack", "push")),
    EventSpec("incident_resolved", "Incident resolved", ("email", "slack")),
    EventSpec(
        "deployment_started", "Deployment started", ("slack",),
        unproduced="This build has no deployments feature to raise it. Deploy "
                   "timings are read from Argo CD for incident correlation only.",
    ),
    EventSpec(
        "deployment_failed", "Deployment failed", ("email", "slack", "push"),
        unproduced="This build has no deployments feature to raise it. Deploy "
                   "timings are read from Argo CD for incident correlation only.",
    ),
    EventSpec("alert_triggered", "Alert triggered", ("slack", "push")),
    EventSpec("oncall_changed", "On-call rotation changed", ("email", "slack")),
    EventSpec("ai_action_pending", "AI action pending approval", ("slack", "push")),
    EventSpec("escalation", "Incident escalated", ("email", "pagerduty")),
)

EVENTS_BY_ID: dict[str, EventSpec] = {e.id: e for e in EVENTS}

DIGEST_MODES = ("realtime", "hourly", "daily")

#: Sent as a secret field's value to erase a stored credential. A plain empty
#: string cannot mean "erase", because the GET never returns secrets and every
#: save would therefore wipe whatever was already there.
CLEAR_SECRET = "__clear__"

#: Channels enabled for an org that has never saved anything.
DEFAULT_ENABLED = ("email", "slack", "push")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_configured(
    spec: ChannelSpec,
    config: dict[str, Any],
    secrets: dict[str, Any],
    *,
    saved: bool,
) -> bool:
    """True only when the org has stored settings and every required field is filled.

    `saved` matters for a channel with no fields at all (push): with nothing
    stored there is no value to check, and answering "configured" for an org that
    has never touched the page would be the hardcoded `configured: true` the old
    component shipped, wearing a server response as a disguise.

    A channel this build cannot deliver on is never configured, whatever is
    stored for it. Push is the one: it needs a registered device and there is no
    endpoint for a browser to register one, so `send_push_notification` has
    nothing to send to. Saying "configured" would be the same untruth by a
    different route.
    """
    from app.notifications.delivery import UNAVAILABLE

    if spec.id in UNAVAILABLE:
        return False
    if not saved:
        return False
    for f in spec.fields:
        if not f.required:
            continue
        source = secrets if f.secret else config
        if not str(source.get(f.key, "") or "").strip():
            return False
    return True


def channel_payload(
    spec: ChannelSpec,
    *,
    enabled: bool,
    config: dict[str, Any],
    secrets: dict[str, Any],
    saved: bool,
) -> dict[str, Any]:
    """The wire shape for one channel.

    Secret values are reported as "set" / "" and never echoed back: the browser
    posted them once, and a GET that returned them would hand every viewer of
    the page the org's PagerDuty routing key.

    `unavailable` carries the reason a channel cannot deliver in this build, so
    the page can say so rather than offering a switch that does nothing.
    """
    from app.notifications.delivery import UNAVAILABLE

    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "icon": spec.icon,
        "enabled": enabled,
        "configured": _is_configured(spec, config, secrets, saved=saved),
        "unavailable": UNAVAILABLE.get(spec.id, ""),
        "fields": [
            {
                "key": f.key,
                "label": f.label,
                "type": f.type,
                "secret": f.secret,
                "required": f.required,
                "value": "" if f.secret else str(config.get(f.key, "") or ""),
                "is_set": bool(
                    str((secrets if f.secret else config).get(f.key, "") or "").strip()
                ),
            }
            for f in spec.fields
        ],
    }


async def get_preferences() -> dict[str, Any]:
    """This org's channels, routing rules and delivery settings."""
    from app.database.engine import get_session
    from app.notifications.models import (
        NotificationChannelPreference,
        NotificationDeliverySettings,
        NotificationRule,
    )

    org = org_uuid()
    async with get_session() as session:
        channel_rows = {
            row.channel: row
            for row in (
                await session.execute(
                    select(NotificationChannelPreference).where(
                        NotificationChannelPreference.org_id == org
                    )
                )
            ).scalars()
        }
        rule_rows = list(
            (
                await session.execute(
                    select(NotificationRule).where(NotificationRule.org_id == org)
                )
            ).scalars()
        )
        settings = (
            await session.execute(
                select(NotificationDeliverySettings).where(
                    NotificationDeliverySettings.org_id == org
                )
            )
        ).scalar_one_or_none()

    channels = []
    for spec in CHANNELS:
        row = channel_rows.get(spec.id)
        channels.append(
            channel_payload(
                spec,
                # An org that has never saved gets the documented defaults, not
                # "everything off" — but the moment it saves, its own row wins.
                enabled=row.enabled if row is not None else spec.id in DEFAULT_ENABLED,
                config=(row.config or {}) if row is not None else {},
                secrets=(row.secrets or {}) if row is not None else {},
                saved=row is not None,
            )
        )

    saved_rules: dict[str, dict[str, bool]] = {}
    for rule_row in rule_rows:
        saved_rules.setdefault(rule_row.event, {})[rule_row.channel] = rule_row.enabled

    rules = []
    for event in EVENTS:
        stored = saved_rules.get(event.id)
        rules.append(
            {
                "event": event.id,
                "description": event.description,
                "unproduced": event.unproduced,
                "channels": {
                    # A save carries only the channels the page actually
                    # changed, so a channel with no row for this event keeps the
                    # documented default instead of silently switching itself
                    # off the first time any other channel is edited.
                    spec.id: (stored or {}).get(spec.id, spec.id in event.default_channels)
                    for spec in CHANNELS
                },
            }
        )

    return {
        "channels": channels,
        "rules": rules,
        "quiet_hours": {
            "enabled": settings.quiet_hours_enabled if settings else False,
            "start": settings.quiet_hours_start if settings else "22:00",
            "end": settings.quiet_hours_end if settings else "08:00",
        },
        "digest_mode": settings.digest_mode if settings else "realtime",
        "customised": bool(channel_rows or rule_rows or settings),
    }


async def save_preferences(
    *,
    channels: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    quiet_hours: dict[str, Any] | None,
    digest_mode: str | None,
) -> dict[str, Any]:
    """Persist a full preferences document and return the stored result.

    Callers pass whole sections; anything omitted is left alone. Unknown channel
    or event ids are rejected by the router before this runs.
    """
    from app.database.engine import get_session
    from app.notifications.models import (
        NotificationChannelPreference,
        NotificationDeliverySettings,
        NotificationRule,
    )

    async with get_session() as session:
        org = await ensure_org_row(session)
        existing_channels = {
            row.channel: row
            for row in (
                await session.execute(
                    select(NotificationChannelPreference).where(
                        NotificationChannelPreference.org_id == org
                    )
                )
            ).scalars()
        }

        for incoming in channels:
            spec = CHANNELS_BY_ID[incoming["id"]]
            row = existing_channels.get(spec.id)
            if row is None:
                row = NotificationChannelPreference(
                    org_id=org, channel=spec.id, config={}, secrets={}, updated_at=_now()
                )
                session.add(row)
                existing_channels[spec.id] = row
            row.enabled = bool(incoming.get("enabled", row.enabled))
            submitted = incoming.get("config") or {}
            config = dict(row.config or {})
            secrets = dict(row.secrets or {})
            for f in spec.fields:
                if f.key not in submitted:
                    continue
                value = str(submitted[f.key] or "").strip()
                target = secrets if f.secret else config
                # A blank submission for a secret means "leave it as it is" —
                # the GET never returns the value, so the form cannot echo it
                # back and a save would otherwise wipe it. Blanking is done by
                # sending the sentinel below.
                if f.secret and value == "":
                    continue
                if f.secret and value == CLEAR_SECRET:
                    target.pop(f.key, None)
                    continue
                target[f.key] = value
            # Rebound rather than mutated: SQLAlchemy does not track edits made
            # inside a plain JSON value, so an in-place update never flushes.
            row.config = config
            row.secrets = secrets
            row.updated_at = _now()

        if rules:
            existing_rules = {
                (row.event, row.channel): row
                for row in (
                    await session.execute(
                        select(NotificationRule).where(NotificationRule.org_id == org)
                    )
                ).scalars()
            }
            for incoming_rule in rules:
                event = incoming_rule["event"]
                for channel_id, enabled in (incoming_rule.get("channels") or {}).items():
                    rule_row = existing_rules.get((event, channel_id))
                    if rule_row is None:
                        rule_row = NotificationRule(
                            org_id=org, event=event, channel=channel_id, updated_at=_now()
                        )
                        session.add(rule_row)
                        existing_rules[(event, channel_id)] = rule_row
                    rule_row.enabled = bool(enabled)
                    rule_row.updated_at = _now()

        if quiet_hours is not None or digest_mode is not None:
            settings = (
                await session.execute(
                    select(NotificationDeliverySettings).where(
                        NotificationDeliverySettings.org_id == org
                    )
                )
            ).scalar_one_or_none()
            if settings is None:
                settings = NotificationDeliverySettings(org_id=org, updated_at=_now())
                session.add(settings)
            if quiet_hours is not None:
                settings.quiet_hours_enabled = bool(quiet_hours.get("enabled", False))
                settings.quiet_hours_start = str(quiet_hours.get("start", "22:00"))
                settings.quiet_hours_end = str(quiet_hours.get("end", "08:00"))
            if digest_mode is not None:
                settings.digest_mode = digest_mode
            settings.updated_at = _now()

        await session.commit()

    return await get_preferences()


def routed_channels(event: str, prefs: dict[str, Any]) -> list[str]:
    """The channels `event` reaches, given an already-loaded preferences document.

    Enabled *and* routed: a channel switched off at the top of the page must not
    receive anything however the per-event matrix is set.

    Deliberately not filtered by `configured`. A channel someone enabled and
    routed but never gave credentials to is a target, and it fails at send time
    with the field it is missing ("no webhook configured") — which is what an
    operator needs in order to fix it. Dropping it here instead would turn that
    into an event with no channels at all, indistinguishable from one nobody
    routed, and the mistake would stay invisible for exactly as long as nothing
    important happened.

    Takes the document rather than loading it so the delivery path can resolve
    channels and read settings from one snapshot.
    """
    by_id = {c["id"]: c for c in prefs["channels"]}
    for rule in prefs["rules"]:
        if rule["event"] != event:
            continue
        return [
            cid
            for cid, on in rule["channels"].items()
            if on and by_id.get(cid, {}).get("enabled")
        ]
    return []


async def channels_for_event(event: str) -> list[str]:
    """`routed_channels` for this org. The read `app/notifications/delivery.py`
    makes before it sends anything."""
    return routed_channels(event, await get_preferences())
