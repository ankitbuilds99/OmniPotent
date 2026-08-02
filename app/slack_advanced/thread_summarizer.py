"""Thread Summarization for Slack.

Summarizes incident threads for quick context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("omnipotent.slack_advanced.thread_summarizer")


@dataclass
class SlackMessage:
    """A Slack message for summarization."""

    message_id: str
    user_id: str
    user_name: str
    text: str
    timestamp: datetime
    is_bot: bool = False
    reactions: list[str] = field(default_factory=list)
    reply_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "user": self.user_name,
            "text": self.text[:200],
            "timestamp": self.timestamp.isoformat(),
            "is_bot": self.is_bot,
            "reactions": self.reactions,
            "replies": self.reply_count,
        }


@dataclass
class ParticipantContribution:
    """A participant's contribution to the thread."""

    user_id: str
    user_name: str
    message_count: int
    key_contributions: list[str]
    role: str
    first_message_at: datetime
    last_message_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "user": self.user_name,
            "messages": self.message_count,
            "key_contributions": self.key_contributions[:3],
            "role": self.role,
            "first_at": self.first_message_at.isoformat(),
            "last_at": self.last_message_at.isoformat(),
        }


@dataclass
class TimelineEvent:
    """An event in the incident timeline."""

    timestamp: datetime
    event_type: str
    description: str
    user: str
    is_key_event: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.timestamp.strftime("%I:%M %p"),
            "type": self.event_type,
            "description": self.description,
            "user": self.user,
            "key": self.is_key_event,
        }


@dataclass
class ThreadSummary:
    """Summary of an incident thread."""

    summary_id: str
    channel_id: str
    thread_ts: str
    incident_id: str
    total_messages: int
    total_participants: int
    duration_minutes: float
    timeline: list[TimelineEvent]
    participants: list[ParticipantContribution]
    root_cause: str
    resolution: str
    impact: str
    action_items: list[str]
    key_decisions: list[str]
    ai_summary: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "channel_id": self.channel_id,
            "thread_ts": self.thread_ts,
            "incident_id": self.incident_id,
            "stats": {
                "messages": self.total_messages,
                "participants": self.total_participants,
                "duration_minutes": round(self.duration_minutes, 1),
            },
            "timeline": [e.to_dict() for e in self.timeline],
            "participants": [p.to_dict() for p in self.participants],
            "root_cause": self.root_cause,
            "resolution": self.resolution,
            "impact": self.impact,
            "action_items": self.action_items,
            "key_decisions": self.key_decisions,
            "summary": self.ai_summary,
            "created_at": self.created_at.isoformat(),
        }


class ThreadSummarizer:
    """Summarizes Slack incident threads."""

    def __init__(self) -> None:
        self._summaries: dict[str, ThreadSummary] = {}
        self._action_keywords = [
            "will", "going to", "need to", "should",
            "must", "action:", "todo:", "task:",
        ]
        self._decision_keywords = [
            "decided", "agreed", "let's", "we will",
            "confirmed", "approved", "rejected",
        ]

    def ingest_messages(
        self,
        channel_id: str,
        thread_ts: str,
        messages: list[dict[str, Any]],
    ) -> list[SlackMessage]:
        """Ingest raw Slack messages."""
        parsed: list[SlackMessage] = []

        for msg in messages:
            timestamp = datetime.fromtimestamp(
                float(msg.get("ts", 0)),
                tz=timezone.utc,
            )

            parsed.append(SlackMessage(
                message_id=msg.get("ts", ""),
                user_id=msg.get("user", ""),
                user_name=msg.get("user_name", msg.get("user", "unknown")),
                text=msg.get("text", ""),
                timestamp=timestamp,
                is_bot=msg.get("bot_id") is not None,
                reactions=[r.get("name", "") for r in msg.get("reactions", [])],
                reply_count=msg.get("reply_count", 0),
            ))

        parsed.sort(key=lambda m: m.timestamp)

        return parsed

    def summarize_thread(
        self,
        channel_id: str,
        thread_ts: str,
        incident_id: str,
        messages: list[SlackMessage],
    ) -> ThreadSummary:
        """Generate a summary of an incident thread."""
        summary_id = f"summary_{incident_id}"

        timeline = self._extract_timeline(messages)

        participants = self._analyze_participants(messages)

        root_cause = self._extract_root_cause(messages)
        resolution = self._extract_resolution(messages)
        impact = self._extract_impact(messages)

        action_items = self._extract_action_items(messages)
        key_decisions = self._extract_decisions(messages)

        duration = 0.0
        if len(messages) >= 2:
            duration = (
                messages[-1].timestamp - messages[0].timestamp
            ).total_seconds() / 60

        ai_summary = self._generate_ai_summary(
            timeline,
            root_cause,
            resolution,
            impact,
            action_items,
        )

        summary = ThreadSummary(
            summary_id=summary_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            incident_id=incident_id,
            total_messages=len(messages),
            total_participants=len(participants),
            duration_minutes=duration,
            timeline=timeline,
            participants=participants,
            root_cause=root_cause,
            resolution=resolution,
            impact=impact,
            action_items=action_items,
            key_decisions=key_decisions,
            ai_summary=ai_summary,
        )

        self._summaries[summary_id] = summary

        log.info(
            "Summarized thread: incident=%s messages=%d participants=%d",
            incident_id,
            len(messages),
            len(participants),
        )

        return summary

    def _extract_timeline(
        self,
        messages: list[SlackMessage],
    ) -> list[TimelineEvent]:
        """Extract key events for the timeline."""
        events: list[TimelineEvent] = []

        incident_keywords = [
            "alert", "incident", "issue", "error", "outage",
            "down", "failing", "broken",
        ]
        investigation_keywords = [
            "investigating", "checking", "looking", "found",
            "root cause", "identified",
        ]
        action_keywords = [
            "rollback", "rolling back", "scaling", "restarting",
            "deploying", "fixed", "resolved",
        ]
        resolution_keywords = [
            "resolved", "fixed", "back to normal", "stable",
            "recovered", "mitigated",
        ]

        for msg in messages:
            text_lower = msg.text.lower()

            event_type = None
            is_key = False

            if any(kw in text_lower for kw in incident_keywords):
                if not events:
                    event_type = "incident_start"
                    is_key = True

            if any(kw in text_lower for kw in investigation_keywords):
                if "root cause" in text_lower or "found" in text_lower:
                    event_type = "root_cause_found"
                    is_key = True
                elif not any(e.event_type == "investigation_start" for e in events):
                    event_type = "investigation_start"

            if any(kw in text_lower for kw in action_keywords):
                if "rollback" in text_lower:
                    event_type = "rollback_initiated"
                    is_key = True
                elif "fixed" in text_lower or "deployed" in text_lower:
                    event_type = "fix_deployed"
                    is_key = True
                else:
                    event_type = "action_taken"

            if any(kw in text_lower for kw in resolution_keywords):
                event_type = "incident_resolved"
                is_key = True

            if event_type:
                events.append(TimelineEvent(
                    timestamp=msg.timestamp,
                    event_type=event_type,
                    description=msg.text[:100],
                    user=msg.user_name,
                    is_key_event=is_key,
                ))

        return events

    def _analyze_participants(
        self,
        messages: list[SlackMessage],
    ) -> list[ParticipantContribution]:
        """Analyze participant contributions."""
        by_user: dict[str, list[SlackMessage]] = {}

        for msg in messages:
            if msg.is_bot:
                continue

            if msg.user_id not in by_user:
                by_user[msg.user_id] = []
            by_user[msg.user_id].append(msg)

        participants: list[ParticipantContribution] = []

        for user_id, user_messages in by_user.items():
            if not user_messages:
                continue

            role = self._determine_role(user_messages)

            key_contributions = []
            for msg in user_messages:
                text_lower = msg.text.lower()
                if any(kw in text_lower for kw in [
                    "found", "root cause", "fixed", "rollback",
                    "deployed", "resolved",
                ]):
                    key_contributions.append(msg.text[:50])

            participants.append(ParticipantContribution(
                user_id=user_id,
                user_name=user_messages[0].user_name,
                message_count=len(user_messages),
                key_contributions=key_contributions[:3],
                role=role,
                first_message_at=user_messages[0].timestamp,
                last_message_at=user_messages[-1].timestamp,
            ))

        participants.sort(key=lambda p: p.message_count, reverse=True)

        return participants

    def _determine_role(self, messages: list[SlackMessage]) -> str:
        """Determine participant's role in the incident."""
        all_text = " ".join(m.text.lower() for m in messages)

        if any(kw in all_text for kw in ["i'll lead", "commander", "taking lead"]):
            return "commander"
        elif any(kw in all_text for kw in ["deploying", "rollback", "scaling"]):
            return "responder"
        elif any(kw in all_text for kw in ["found", "root cause", "investigating"]):
            return "investigator"
        elif any(kw in all_text for kw in ["?", "what", "how", "update"]):
            return "stakeholder"
        else:
            return "participant"

    def _extract_root_cause(self, messages: list[SlackMessage]) -> str:
        """Extract root cause from messages."""
        for msg in messages:
            text_lower = msg.text.lower()
            if "root cause" in text_lower or "caused by" in text_lower:
                return msg.text[:200]

        return "Not identified in thread"

    def _extract_resolution(self, messages: list[SlackMessage]) -> str:
        """Extract resolution from messages."""
        for msg in reversed(messages):
            text_lower = msg.text.lower()
            if any(kw in text_lower for kw in [
                "resolved", "fixed", "rolled back", "recovered",
            ]):
                return msg.text[:200]

        return "Resolution not found in thread"

    def _extract_impact(self, messages: list[SlackMessage]) -> str:
        """Extract impact information from messages."""
        for msg in messages:
            text_lower = msg.text.lower()
            if any(kw in text_lower for kw in [
                "users affected", "errors:", "duration:", "impact:",
                "revenue", "customers",
            ]):
                return msg.text[:200]

        return "Impact not explicitly stated"

    def _extract_action_items(self, messages: list[SlackMessage]) -> list[str]:
        """Extract action items from messages."""
        action_items: list[str] = []

        for msg in messages:
            text_lower = msg.text.lower()

            for keyword in self._action_keywords:
                if keyword in text_lower:
                    action_items.append(
                        f"@{msg.user_name}: {msg.text[:100]}"
                    )
                    break

        return action_items[:10]

    def _extract_decisions(self, messages: list[SlackMessage]) -> list[str]:
        """Extract key decisions from messages."""
        decisions: list[str] = []

        for msg in messages:
            text_lower = msg.text.lower()

            for keyword in self._decision_keywords:
                if keyword in text_lower:
                    decisions.append(msg.text[:100])
                    break

        return decisions[:5]

    def _generate_ai_summary(
        self,
        timeline: list[TimelineEvent],
        root_cause: str,
        resolution: str,
        impact: str,
        action_items: list[str],
    ) -> str:
        """Generate an AI summary of the thread."""
        key_events = [e for e in timeline if e.is_key_event]

        timeline_text = ""
        for event in key_events[:4]:
            timeline_text += f"- {event.timestamp.strftime('%I:%M %p')} - {event.description[:50]}\n"

        summary = f"**Timeline:**\n{timeline_text}\n"
        summary += f"**Root Cause:**\n{root_cause[:150]}\n\n"
        summary += f"**Impact:**\n{impact[:100]}\n\n"

        if action_items:
            summary += "**Action Items Discussed:**\n"
            for item in action_items[:3]:
                summary += f"- {item[:50]}\n"

        return summary

    def get_summary(self, summary_id: str) -> ThreadSummary | None:
        """Get a summary by ID."""
        return self._summaries.get(summary_id)

    def get_slack_message(
        self,
        summary: ThreadSummary,
    ) -> dict[str, Any]:
        """Format summary for Slack message."""
        timeline_text = ""
        for event in summary.timeline:
            if event.is_key_event:
                timeline_text += f"- {event.timestamp.strftime('%I:%M %p')} - {event.description[:50]}\n"

        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Thread Summary",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Timeline:*\n{timeline_text}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Root Cause:*\n{summary.root_cause[:200]}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Impact:*\n{summary.impact[:150]}",
                },
            },
        ]

        if summary.action_items:
            items_text = "\n".join(f"- {item[:50]}" for item in summary.action_items[:3])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Action Items:*\n{items_text}",
                },
            })

        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Copy to Postmortem"},
                    "action_id": "copy_to_postmortem",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Export as Markdown"},
                    "action_id": "export_markdown",
                },
            ],
        })

        return {
            "blocks": blocks,
            "text": "Thread Summary",
        }


_summarizer: ThreadSummarizer | None = None


def get_thread_summarizer() -> ThreadSummarizer:
    """Get the default thread summarizer."""
    global _summarizer
    if _summarizer is None:
        _summarizer = ThreadSummarizer()
    return _summarizer
