"""Incident War Room Management.

Auto-creates and manages incident channels with
proper structure, invites, and lifecycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.slack_bot.war_room")


class WarRoomStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class ThreadType(str, Enum):
    MAIN = "main"
    AI_INVESTIGATION = "ai_investigation"
    METRICS = "metrics"
    TIMELINE = "timeline"
    HUMAN_DISCUSSION = "human_discussion"


@dataclass
class WarRoomConfig:
    """Configuration for war room creation."""

    channel_prefix: str = "inc-"
    auto_invite_oncall: bool = True
    auto_invite_service_owners: bool = True
    auto_invite_sre_lead: bool = True
    pin_summary: bool = True
    pin_runbook: bool = True
    create_threads: bool = True
    auto_archive_days: int = 7
    archive_on_resolve: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_prefix": self.channel_prefix,
            "auto_invite_oncall": self.auto_invite_oncall,
            "auto_invite_service_owners": self.auto_invite_service_owners,
            "auto_invite_sre_lead": self.auto_invite_sre_lead,
            "pin_summary": self.pin_summary,
            "pin_runbook": self.pin_runbook,
            "create_threads": self.create_threads,
            "auto_archive_days": self.auto_archive_days,
        }


@dataclass
class WarRoomThread:
    """A thread within a war room."""

    thread_type: ThreadType
    thread_ts: str
    title: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.thread_type.value,
            "ts": self.thread_ts,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class WarRoom:
    """An incident war room channel."""

    war_room_id: str
    incident_id: str
    channel_id: str
    channel_name: str
    org_id: str
    status: WarRoomStatus = WarRoomStatus.ACTIVE
    severity: str = "P2"
    title: str = ""
    description: str = ""
    services: list[str] = field(default_factory=list)
    responders: list[str] = field(default_factory=list)
    threads: list[WarRoomThread] = field(default_factory=list)
    pinned_messages: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    archived_at: datetime | None = None

    @property
    def duration_minutes(self) -> float:
        end_time = self.resolved_at or datetime.now(timezone.utc)
        return (end_time - self.created_at).total_seconds() / 60

    @property
    def is_active(self) -> bool:
        return self.status == WarRoomStatus.ACTIVE

    def get_thread(self, thread_type: ThreadType) -> WarRoomThread | None:
        for thread in self.threads:
            if thread.thread_type == thread_type:
                return thread
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.war_room_id,
            "incident_id": self.incident_id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "status": self.status.value,
            "severity": self.severity,
            "title": self.title,
            "services": self.services,
            "responders": self.responders,
            "threads": [t.to_dict() for t in self.threads],
            "duration_minutes": round(self.duration_minutes, 1),
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class WarRoomManager:
    """Manages incident war rooms."""

    def __init__(self, config: WarRoomConfig | None = None) -> None:
        self.config = config or WarRoomConfig()
        self._war_rooms: dict[str, WarRoom] = {}
        self._incident_to_room: dict[str, str] = {}
        self._channel_to_room: dict[str, str] = {}

    async def create_war_room(
        self,
        incident_id: str,
        org_id: str,
        title: str,
        severity: str = "P2",
        services: list[str] | None = None,
        description: str = "",
    ) -> WarRoom:
        """Create a new war room for an incident."""
        channel_name = self._generate_channel_name(incident_id, title)

        war_room_id = f"wr_{incident_id}"

        war_room = WarRoom(
            war_room_id=war_room_id,
            incident_id=incident_id,
            channel_id=f"C{incident_id}",
            channel_name=channel_name,
            org_id=org_id,
            severity=severity,
            title=title,
            description=description,
            services=services or [],
        )

        self._war_rooms[war_room_id] = war_room
        self._incident_to_room[incident_id] = war_room_id
        self._channel_to_room[war_room.channel_id] = war_room_id

        await self._setup_war_room(war_room)

        log.info(
            "War room created: id=%s incident=%s channel=%s",
            war_room_id,
            incident_id,
            channel_name,
        )

        return war_room

    def _generate_channel_name(self, incident_id: str, title: str) -> str:
        """Generate a channel name from incident details."""
        title_slug = "-".join(title.lower().split()[:3])
        title_slug = "".join(c for c in title_slug if c.isalnum() or c == "-")

        return f"{self.config.channel_prefix}{incident_id.lower()}-{title_slug}"[:80]

    async def _setup_war_room(self, war_room: WarRoom) -> None:
        """Set up the war room with threads, pins, and invites."""
        if self.config.auto_invite_oncall:
            oncall = await self._get_oncall_users(war_room.services)
            war_room.responders.extend(oncall)

        if self.config.auto_invite_service_owners:
            owners = await self._get_service_owners(war_room.services)
            war_room.responders.extend(owners)

        if self.config.auto_invite_sre_lead and war_room.severity in ("P1", "P2"):
            sre_lead = await self._get_sre_lead(war_room.org_id)
            if sre_lead:
                war_room.responders.append(sre_lead)

        war_room.responders = list(set(war_room.responders))

        if self.config.create_threads:
            await self._create_threads(war_room)

        if self.config.pin_summary:
            await self._pin_summary(war_room)

        if self.config.pin_runbook:
            await self._pin_runbook(war_room)

    async def _get_oncall_users(self, services: list[str]) -> list[str]:
        """Get on-call users for services."""
        return ["oncall_user"]

    async def _get_service_owners(self, services: list[str]) -> list[str]:
        """Get service owners."""
        return [f"owner_{s}" for s in services[:3]]

    async def _get_sre_lead(self, org_id: str) -> str | None:
        """Get SRE lead for org."""
        return "sre_lead"

    async def _create_threads(self, war_room: WarRoom) -> None:
        """Create organized threads in the war room."""
        thread_configs = [
            (ThreadType.AI_INVESTIGATION, "AI Investigation"),
            (ThreadType.METRICS, "Live Metrics"),
            (ThreadType.TIMELINE, "Timeline"),
        ]

        for thread_type, title in thread_configs:
            thread = WarRoomThread(
                thread_type=thread_type,
                thread_ts=f"{datetime.now(timezone.utc).timestamp()}",
                title=title,
            )
            war_room.threads.append(thread)

    async def _pin_summary(self, war_room: WarRoom) -> None:
        """Pin incident summary to channel."""
        war_room.pinned_messages.append("summary")

    async def _pin_runbook(self, war_room: WarRoom) -> None:
        """Pin relevant runbook to channel."""
        if war_room.services:
            war_room.pinned_messages.append(f"runbook_{war_room.services[0]}")

    def get_war_room(self, war_room_id: str) -> WarRoom | None:
        """Get a war room by ID."""
        return self._war_rooms.get(war_room_id)

    def get_by_incident(self, incident_id: str) -> WarRoom | None:
        """Get war room by incident ID."""
        war_room_id = self._incident_to_room.get(incident_id)
        if war_room_id:
            return self._war_rooms.get(war_room_id)
        return None

    def get_by_channel(self, channel_id: str) -> WarRoom | None:
        """Get war room by channel ID."""
        war_room_id = self._channel_to_room.get(channel_id)
        if war_room_id:
            return self._war_rooms.get(war_room_id)
        return None

    async def add_responder(
        self,
        war_room_id: str,
        user_id: str,
    ) -> bool:
        """Add a responder to the war room."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return False

        if user_id not in war_room.responders:
            war_room.responders.append(user_id)

        log.info(
            "Added responder: war_room=%s user=%s",
            war_room_id,
            user_id,
        )

        return True

    async def remove_responder(
        self,
        war_room_id: str,
        user_id: str,
    ) -> bool:
        """Remove a responder from the war room."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return False

        if user_id in war_room.responders:
            war_room.responders.remove(user_id)

        return True

    async def update_status(
        self,
        war_room_id: str,
        status: WarRoomStatus,
    ) -> bool:
        """Update war room status."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return False

        war_room.status = status

        if status == WarRoomStatus.RESOLVED:
            war_room.resolved_at = datetime.now(timezone.utc)

        if status == WarRoomStatus.ARCHIVED:
            war_room.archived_at = datetime.now(timezone.utc)

        log.info(
            "War room status updated: id=%s status=%s",
            war_room_id,
            status.value,
        )

        return True

    async def resolve_war_room(
        self,
        war_room_id: str,
    ) -> WarRoom | None:
        """Resolve an incident war room."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return None

        war_room.status = WarRoomStatus.RESOLVED
        war_room.resolved_at = datetime.now(timezone.utc)

        await self._post_resolution_summary(war_room)

        log.info(
            "War room resolved: id=%s duration=%dm",
            war_room_id,
            int(war_room.duration_minutes),
        )

        return war_room

    async def _post_resolution_summary(self, war_room: WarRoom) -> None:
        """Post resolution summary to channel."""
        pass

    async def archive_war_room(
        self,
        war_room_id: str,
    ) -> bool:
        """Archive a war room channel."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return False

        war_room.status = WarRoomStatus.ARCHIVED
        war_room.archived_at = datetime.now(timezone.utc)

        await self._export_to_postmortem(war_room)

        log.info("War room archived: id=%s", war_room_id)

        return True

    async def _export_to_postmortem(self, war_room: WarRoom) -> None:
        """Export war room content to postmortem."""
        pass

    async def check_archive_eligible(self) -> list[WarRoom]:
        """Check for war rooms eligible for archiving."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.auto_archive_days)

        eligible = []

        for war_room in self._war_rooms.values():
            if war_room.status == WarRoomStatus.RESOLVED:
                if war_room.resolved_at and war_room.resolved_at < cutoff:
                    eligible.append(war_room)

        return eligible

    async def post_to_thread(
        self,
        war_room_id: str,
        thread_type: ThreadType,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Post a message to a specific thread."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return False

        thread = war_room.get_thread(thread_type)
        if not thread:
            return False

        log.info(
            "Posted to thread: war_room=%s thread=%s",
            war_room_id,
            thread_type.value,
        )

        return True

    async def post_ai_update(
        self,
        war_room_id: str,
        update: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Post an AI investigation update."""
        return await self.post_to_thread(
            war_room_id,
            ThreadType.AI_INVESTIGATION,
            update,
            blocks,
        )

    async def post_metrics_update(
        self,
        war_room_id: str,
        metrics: dict[str, Any],
    ) -> bool:
        """Post a metrics update."""
        metrics_text = "\n".join(
            f"*{k}*: {v}" for k, v in metrics.items()
        )

        return await self.post_to_thread(
            war_room_id,
            ThreadType.METRICS,
            metrics_text,
        )

    async def add_timeline_event(
        self,
        war_room_id: str,
        event: str,
        user_id: str | None = None,
    ) -> bool:
        """Add an event to the timeline."""
        timestamp = datetime.now(timezone.utc).strftime("%H:%M")
        user_mention = f" by <@{user_id}>" if user_id else ""

        return await self.post_to_thread(
            war_room_id,
            ThreadType.TIMELINE,
            f"*{timestamp}*: {event}{user_mention}",
        )

    def get_active_war_rooms(self, org_id: str | None = None) -> list[WarRoom]:
        """Get all active war rooms."""
        rooms = [
            wr for wr in self._war_rooms.values()
            if wr.status == WarRoomStatus.ACTIVE
        ]

        if org_id:
            rooms = [wr for wr in rooms if wr.org_id == org_id]

        return rooms

    def get_summary(self, war_room_id: str) -> dict[str, Any] | None:
        """Get summary of a war room."""
        war_room = self._war_rooms.get(war_room_id)
        if not war_room:
            return None

        return {
            "incident_id": war_room.incident_id,
            "channel": f"#{war_room.channel_name}",
            "status": war_room.status.value,
            "severity": war_room.severity,
            "title": war_room.title,
            "duration_minutes": round(war_room.duration_minutes, 1),
            "responders": len(war_room.responders),
            "services": war_room.services,
            "threads": len(war_room.threads),
        }

    def get_channel_topic(self, war_room: WarRoom) -> str:
        """Generate channel topic for war room."""
        status_emoji = {
            WarRoomStatus.ACTIVE: "INVESTIGATING",
            WarRoomStatus.RESOLVED: "RESOLVED",
            WarRoomStatus.ARCHIVED: "ARCHIVED",
        }

        return (
            f"{war_room.severity} | {war_room.title} | "
            f"Status: {status_emoji.get(war_room.status, 'UNKNOWN')}"
        )

    def get_initial_message_blocks(self, war_room: WarRoom) -> list[dict[str, Any]]:
        """Get initial message blocks for war room."""
        services_text = ", ".join(war_room.services) if war_room.services else "Unknown"
        responders_text = ", ".join(
            f"<@{r}>" for r in war_room.responders
        ) if war_room.responders else "None assigned"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{war_room.severity} INCIDENT: {war_room.title}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident ID:*\n{war_room.incident_id}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{war_room.severity}"},
                    {"type": "mrkdwn", "text": f"*Services:*\n{services_text}"},
                    {"type": "mrkdwn", "text": f"*Responders:*\n{responders_text}"},
                ],
            },
        ]

        if war_room.description:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n{war_room.description}",
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Start Investigation"},
                    "action_id": f"investigate_{war_room.incident_id}",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View Timeline"},
                    "action_id": f"timeline_{war_room.incident_id}",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Page Team"},
                    "action_id": f"page_{war_room.incident_id}",
                },
            ],
        })

        return blocks


_manager: WarRoomManager | None = None


def get_war_room_manager() -> WarRoomManager:
    """Get the default war room manager."""
    global _manager
    if _manager is None:
        _manager = WarRoomManager()
    return _manager
