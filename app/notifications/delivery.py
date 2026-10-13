"""The consumer the Notification Preferences page never had.

`app/notifications/preferences.py` stores which channels an org has enabled,
their credentials, which events route to which channels, its quiet hours and its
digest mode. Until this module existed nothing read any of it: `channels_for_event`
— documented as "the read the dispatcher wants" — had exactly one caller, and it
was a test. Creating an incident sent nothing anywhere. Every switch, webhook URL
and routing checkbox on that page was stored faithfully and then ignored.

`app/notifications/dispatcher.py` is not that consumer and could not be made into
one without rewriting it: its preferences are a per-*user* dataclass in a process
dictionary, unrelated to the per-org rows the page writes, and most of its
channels delegate to handlers that something is expected to inject with
`set_slack_handler(...)` and nothing ever does. This module reads the stored rows
directly and sends with the credentials in them.

What can actually be delivered is a property of what the catalogue stores. Eight
of the nine channels carry enough to send with. `push` does not and says so: this
build has no endpoint through which a browser could register a device, so
`send_push_notification` has nothing to send to, and reporting that channel as
"configured" would be the same untruth the preferences page was written to
remove.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.notifications.preferences import (
    CHANNELS_BY_ID,
    EVENTS_BY_ID,
    get_preferences,
    routed_channels,
)

log = logging.getLogger("omnipotent.notifications.delivery")

#: PagerDuty's Events API v2. Fixed rather than configurable: the routing key
#: stored per org is what selects the service, and a settable endpoint would be
#: somewhere else for a credential to be posted to.
PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


@dataclass(frozen=True)
class ChannelResult:
    """What happened on one channel."""

    channel: str
    delivered: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "delivered": self.delivered, "detail": self.detail}


def _timeout() -> float:
    return get_settings().http_timeout_default


def _parse_hhmm(value: str, fallback: dt_time) -> dt_time:
    try:
        hh, mm = value.split(":", 1)
        return dt_time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return fallback


def in_quiet_hours(settings: dict[str, Any], now: datetime | None = None) -> bool:
    """Whether `now` falls inside the org's configured quiet window.

    Windows wrap midnight — the default is 22:00 to 08:00, which is only a
    window at all if "start after end" means "over the night" rather than an
    empty range. A start equal to the end is treated as no window rather than
    as the whole day, because the page offers no way to mean "mute everything"
    and reading it that way would silence an org that set both fields the same
    by accident.
    """
    if not settings.get("enabled"):
        return False
    start = _parse_hhmm(str(settings.get("start") or ""), dt_time(22, 0))
    end = _parse_hhmm(str(settings.get("end") or ""), dt_time(8, 0))
    if start == end:
        return False
    current = (now or datetime.now(timezone.utc)).time()
    if start < end:
        return start <= current < end
    return current >= start or current < end


async def _post_json(
    url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None
) -> tuple[bool, str]:
    """POST `payload` and say whether the far end accepted it.

    The status code is the answer. Several of the senders this replaces returned
    True regardless, which is how an unconfigured channel came to be reported as
    a delivered notification.
    """
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, json=payload, headers=headers or {})
    except Exception as exc:  # noqa: BLE001 — one channel failing is not fatal
        return False, f"{type(exc).__name__}: {exc}"
    if resp.status_code < 300:
        return True, f"HTTP {resp.status_code}"
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


# --------------------------------------------------------------------------- #
# Per-channel senders. Each takes the org's stored settings for that channel and
# returns (delivered, detail); none of them raise.
# --------------------------------------------------------------------------- #
async def _send_email(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    from app.notifications.email import send_email

    address = str(cfg.get("address") or "").strip()
    if not address:
        return False, "no address configured"
    try:
        ok = await send_email(to=address, subject=note.title, body_text=note.body)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return bool(ok), "sent" if ok else "provider rejected the message"


async def _send_slack(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    url = str(sec.get("webhook") or "").strip()
    if not url:
        return False, "no webhook configured"
    return await _post_json(url, {"text": f"*{note.title}*\n{note.body}"})


async def _send_teams(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    url = str(sec.get("webhook") or "").strip()
    if not url:
        return False, "no webhook configured"
    # The legacy MessageCard shape, which every Teams incoming-webhook connector
    # accepts; Adaptive Cards need a Workflows-style endpoint that the catalogue
    # gives no way to distinguish.
    return await _post_json(
        url,
        {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": note.title,
            "themeColor": "D93F0B" if note.critical else "0076D7",
            "title": note.title,
            "text": note.body,
        },
    )


async def _send_discord(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    url = str(sec.get("webhook") or "").strip()
    if not url:
        return False, "no webhook configured"
    return await _post_json(url, {"content": f"**{note.title}**\n{note.body}"[:2000]})


async def _send_webhook(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    url = str(cfg.get("url") or "").strip()
    if not url:
        return False, "no url configured"
    body = json.dumps(note.to_payload(), default=str)
    headers = {"Content-Type": "application/json", "User-Agent": "OmniPotent/1.0"}
    secret = str(sec.get("secret") or "").strip()
    if secret:
        # Same scheme as the status page's subscriber webhooks, so a receiver
        # can verify both with one implementation.
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
        headers["X-OmniPotent-Signature"] = f"t={ts},v1={sig}"
        headers["X-OmniPotent-Timestamp"] = ts
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, content=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if resp.status_code < 300:
        return True, f"HTTP {resp.status_code}"
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


async def _send_pagerduty(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    routing_key = str(sec.get("routing_key") or "").strip()
    if not routing_key:
        return False, "no routing key configured"
    severity = "critical" if note.critical else "warning"
    payload = {
        "routing_key": routing_key,
        "event_action": "resolve" if note.event.endswith("_resolved") else "trigger",
        "dedup_key": note.dedupe_key or f"{note.event}:{note.title}",
        "payload": {
            "summary": note.title[:1024],
            "source": str(note.data.get("service") or "omnipotent"),
            "severity": severity,
            "custom_details": {"body": note.body, **note.data},
        },
    }
    return await _post_json(PAGERDUTY_EVENTS_URL, payload)


async def _send_sms(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    from app.notifications.sms import send_sms

    phone = str(cfg.get("phone") or "").strip()
    if not phone:
        return False, "no phone number configured"
    try:
        result = await send_sms(to=phone, body=f"{note.title}: {note.body}"[:320])
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if result.get("success"):
        return True, str(result.get("sid") or "sent")
    return False, str(result.get("error") or "provider rejected the message")


async def _send_jira(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    """Open a ticket in the org's project.

    Only the project key is per-org; the site URL and API credentials come from
    the deployment's environment, because they belong to the installation rather
    than to one org's routing preferences.
    """
    from app.integrations.jira import JiraClient, JiraConfig

    project = str(cfg.get("project") or "").strip()
    if not project:
        return False, "no project configured"
    config = JiraConfig.from_env()
    config.project_key = project
    if not config.is_configured():
        return False, "Jira credentials are not set on this deployment (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN)"
    try:
        issue = await JiraClient(config).create_issue(
            summary=note.title[:250],
            description=note.body,
            labels=[f"omnipotent:{note.event}"],
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if issue is None:
        return False, "Jira rejected the issue"
    return True, issue.key


async def _send_push(cfg: dict, sec: dict, note: "Notification") -> tuple[bool, str]:
    """Deliver to the org's registered devices — of which there are never any.

    Kept as a real sender rather than deleted so that the day a registration
    endpoint ships this starts working on its own. Until then `UNAVAILABLE`
    below short-circuits it in `_fan_out` and the preferences API reports the
    channel as permanently unconfigured.
    """
    from app.notifications.push import send_push_notification

    results = await send_push_notification(
        user_id=str(note.data.get("user_id") or ""), title=note.title, body=note.body
    )
    if not results:
        return False, "no registered devices"
    return any(results.values()), f"{sum(results.values())}/{len(results)} devices"


_SENDERS = {
    "email": _send_email,
    "slack": _send_slack,
    "teams": _send_teams,
    "discord": _send_discord,
    "webhook": _send_webhook,
    "pagerduty": _send_pagerduty,
    "sms": _send_sms,
    "jira": _send_jira,
    "push": _send_push,
}

#: Channels this build stores settings for but cannot deliver on, and why. The
#: preferences API reports these as permanently unconfigured so the page can say
#: so instead of showing a switch that does nothing.
UNAVAILABLE: dict[str, str] = {
    "push": (
        "Browser push needs a registered device, and this build has no endpoint "
        "for a browser to register one."
    ),
}


@dataclass(frozen=True)
class Notification:
    """One thing worth telling somebody about."""

    event: str
    title: str
    body: str
    #: Critical notifications ignore quiet hours and are never held for a
    #: digest — the page describes digests as batching "non-critical"
    #: notifications, and a muted page at 3am is precisely when a SEV1 matters.
    critical: bool = False
    data: dict[str, Any] = None  # type: ignore[assignment]
    dedupe_key: str = ""

    def __post_init__(self) -> None:
        if self.data is None:
            object.__setattr__(self, "data", {})

    def to_payload(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "title": self.title,
            "body": self.body,
            "critical": self.critical,
            "data": self.data,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }


async def deliver(note: Notification) -> dict[str, Any]:
    """Send `note` everywhere this org has routed its event. Never raises.

    Returns what happened per channel, so a caller can log it and a test can
    assert on it. Suppression is reported too — "quiet hours" and "held for the
    daily digest" are outcomes an operator needs to be able to see, not silence.
    """
    if note.event not in EVENTS_BY_ID:
        log.warning("unknown notification event %r", note.event)
        return {"event": note.event, "status": "unknown_event", "results": []}

    try:
        prefs = await get_preferences()
    except Exception:  # noqa: BLE001 — notification failure must not fail the action
        log.warning("notification preferences unavailable", exc_info=True)
        return {"event": note.event, "status": "preferences_unavailable", "results": []}

    channels = routed_channels(note.event, prefs)
    if not channels:
        return {"event": note.event, "status": "no_channels", "results": []}

    if not note.critical and in_quiet_hours(prefs.get("quiet_hours") or {}):
        log.info("notification %s suppressed by quiet hours", note.event)
        return {"event": note.event, "status": "quiet_hours", "results": []}

    digest_mode = str(prefs.get("digest_mode") or "realtime")
    if not note.critical and digest_mode != "realtime":
        held = await hold_for_digest(note, channels, digest_mode)
        return {
            "event": note.event,
            "status": "digest" if held else "digest_unavailable",
            "digest_mode": digest_mode,
            "results": [],
        }

    return await _fan_out(note, channels, prefs)


async def _fan_out(
    note: Notification, channels: list[str], prefs: dict[str, Any]
) -> dict[str, Any]:
    """Send to every channel at once and collect the answers."""
    by_id = {c["id"]: c for c in prefs["channels"]}
    stored = await _stored_settings()

    async def _one(channel: str) -> ChannelResult:
        sender = _SENDERS.get(channel)
        if sender is None or channel not in CHANNELS_BY_ID:
            return ChannelResult(channel, False, "unknown channel")
        if channel in UNAVAILABLE:
            # Answered here rather than in the sender so the result carries the
            # reason it cannot work at all, instead of a symptom ("no registered
            # devices") that reads like something the operator could go and fix.
            return ChannelResult(channel, False, UNAVAILABLE[channel])
        cfg, sec = stored.get(channel, ({}, {}))
        try:
            delivered, detail = await sender(cfg, sec, note)
        except Exception as exc:  # noqa: BLE001
            log.warning("notification channel %s failed", channel, exc_info=True)
            return ChannelResult(channel, False, f"{type(exc).__name__}: {exc}")
        return ChannelResult(channel, delivered, detail)

    results = await asyncio.gather(*(_one(c) for c in channels))
    delivered = [r.channel for r in results if r.delivered]
    log.info(
        "notification %s -> %s",
        note.event,
        ", ".join(delivered) if delivered else "nothing delivered",
        extra={"event": note.event, "channels": list(by_id)},
    )
    return {
        "event": note.event,
        "status": "sent" if delivered else "failed",
        "results": [r.to_dict() for r in results],
    }


async def _stored_settings() -> dict[str, tuple[dict, dict]]:
    """Each channel's `(config, secrets)` as stored for this org.

    Read straight from the rows rather than from `get_preferences()`, which
    deliberately withholds secret values — that redaction exists so a page
    reload cannot exfiltrate a routing key, and it is exactly the credential a
    sender needs.
    """
    from sqlalchemy import select

    from app.database.engine import get_session
    from app.database.orgs import org_uuid
    from app.notifications.models import NotificationChannelPreference

    org = org_uuid()
    async with get_session() as session:
        rows = (
            await session.execute(
                select(NotificationChannelPreference).where(
                    NotificationChannelPreference.org_id == org
                )
            )
        ).scalars()
        return {row.channel: (dict(row.config or {}), dict(row.secrets or {})) for row in rows}


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #
async def hold_for_digest(
    note: Notification, channels: list[str], digest_mode: str
) -> bool:
    """Queue a notification for the next digest window. Best-effort."""
    from app.database.engine import get_session
    from app.database.orgs import ensure_org_row
    from app.notifications.models import NotificationDigestEntry

    try:
        async with get_session() as session:
            org = await ensure_org_row(session)
            session.add(
                NotificationDigestEntry(
                    org_id=org,
                    event=note.event,
                    title=note.title,
                    body=note.body,
                    channels=list(channels),
                    data=dict(note.data),
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        log.warning("could not queue %s for the %s digest", note.event, digest_mode, exc_info=True)
        return False
    return True


def digest_body(entries: list[Any]) -> str:
    """The text of a digest covering `entries`, oldest first."""
    lines = []
    for entry in entries:
        stamp = entry.created_at.strftime("%H:%M") if entry.created_at else "--:--"
        lines.append(f"[{stamp}] {entry.title}")
        if entry.body:
            lines.append(f"    {entry.body.splitlines()[0][:200]}")
    return "\n".join(lines)


async def flush_digest(now: datetime | None = None) -> dict[str, Any]:
    """Send everything queued for this org and clear it.

    Called by the periodic worker on the hour; an org on `daily` only has its
    queue sent when the hour matches the end of its quiet window, or 09:00 for
    an org with quiet hours switched off — a "daily digest" that arrives at
    whatever minute the process happened to start is not a daily digest.
    """
    prefs = await get_preferences()
    mode = str(prefs.get("digest_mode") or "realtime")
    if mode == "realtime":
        return {"status": "realtime", "sent": 0}

    stamp = now or datetime.now(timezone.utc)
    if mode == "daily":
        quiet = prefs.get("quiet_hours") or {}
        due_hour = (
            _parse_hhmm(str(quiet.get("end") or ""), dt_time(8, 0)).hour
            if quiet.get("enabled")
            else 9
        )
        if stamp.hour != due_hour:
            return {"status": "not_due", "sent": 0}

    entries = await _load_digest_entries()
    if not entries:
        return {"status": "empty", "sent": 0}
    channels = sorted({c for e in entries for c in (e.channels or [])})

    note = Notification(
        event=entries[-1].event,
        title=f"OmniPotent digest — {len(entries)} notification(s)",
        body=digest_body(entries),
        data={"count": len(entries)},
    )
    # Past the routing check deliberately: the channels were resolved when each
    # entry was queued, and re-deriving them now would drop everything an org
    # had already collected the moment it edited its rules.
    result = await _fan_out(note, channels, prefs)
    if result.get("status") == "sent":
        await _delete_digest_entries(entries)
        result["sent"] = len(entries)
    else:
        result["sent"] = 0
        result["retained_for_retry"] = len(entries)
    return result


async def _load_digest_entries() -> list[Any]:
    """Read the durable outbox without claiming delivery."""
    from sqlalchemy import select
    from app.database.engine import get_session
    from app.database.orgs import org_uuid
    from app.notifications.models import NotificationDigestEntry
    async with get_session() as session:
        return list((await session.execute(
            select(NotificationDigestEntry)
            .where(NotificationDigestEntry.org_id == org_uuid())
            .order_by(NotificationDigestEntry.created_at)
        )).scalars())


async def _delete_digest_entries(entries: list[Any]) -> None:
    """Acknowledge only rows whose delivery was accepted by a channel."""
    from sqlalchemy import delete
    from app.database.engine import get_session
    from app.notifications.models import NotificationDigestEntry
    async with get_session() as session:
        await session.execute(delete(NotificationDigestEntry).where(
            NotificationDigestEntry.id.in_([entry.id for entry in entries])
        ))
        await session.commit()


async def flush_due_digests(now: datetime | None = None) -> list[dict[str, Any]]:
    """Flush every org that has something queued. What the hourly job calls.

    `flush_digest` reads the current request's tenant, and a timer has no
    request behind it — under no tenant `org_uuid()` resolves to the
    single-tenant org, so an unqualified hourly call would flush that one org and
    leave every other org's digest queued forever. The orgs with pending rows are
    the authority on who needs flushing, and each is established as the tenant for
    the duration of its own flush.
    """
    from sqlalchemy import select

    from app.database.engine import get_session
    from app.notifications.models import NotificationDigestEntry
    from app.tenant_context import TenantConfig, run_with_tenant

    async with get_session() as session:
        orgs = list(
            (
                await session.execute(select(NotificationDigestEntry.org_id).distinct())
            ).scalars()
        )

    out: list[dict[str, Any]] = []
    for org in orgs:
        try:
            result = await run_with_tenant(TenantConfig(org_id=str(org)), flush_digest(now))
        except Exception:  # noqa: BLE001 — one org's broken webhook is not the others' problem
            log.warning("digest flush failed for org %s", org, exc_info=True)
            continue
        out.append({"org_id": str(org), **result})
    return out


async def notify(
    event: str,
    title: str,
    body: str,
    *,
    critical: bool = False,
    data: dict[str, Any] | None = None,
    dedupe_key: str = "",
) -> dict[str, Any]:
    """Convenience wrapper — the call sites all look like this."""
    return await deliver(
        Notification(
            event=event,
            title=title,
            body=body,
            critical=critical,
            data=data or {},
            dedupe_key=dedupe_key,
        )
    )
