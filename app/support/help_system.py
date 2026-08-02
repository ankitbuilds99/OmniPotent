"""Support ticket and knowledge base system.

Features:
- Ticket creation and tracking
- Priority-based routing
- Knowledge base with search
- Response time SLAs
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

log = logging.getLogger("omnipotent.support.help_system")


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


SLA_RESPONSE_MINUTES: dict[TicketPriority, int] = {
    TicketPriority.LOW: 24 * 60,
    TicketPriority.MEDIUM: 8 * 60,
    TicketPriority.HIGH: 4 * 60,
    TicketPriority.URGENT: 60,
}

SLA_RESOLUTION_HOURS: dict[TicketPriority, int] = {
    TicketPriority.LOW: 72,
    TicketPriority.MEDIUM: 24,
    TicketPriority.HIGH: 8,
    TicketPriority.URGENT: 4,
}


@dataclass
class TicketMessage:
    """A message in a support ticket."""

    id: str
    author_id: str
    author_type: str
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_internal: bool = False


@dataclass
class Ticket:
    """Support ticket."""

    id: str
    org_id: str
    user_id: str
    subject: str
    description: str

    status: TicketStatus = TicketStatus.OPEN
    priority: TicketPriority = TicketPriority.MEDIUM

    category: str = "general"
    tags: list[str] = field(default_factory=list)

    assignee_id: str | None = None
    messages: list[TicketMessage] = field(default_factory=list)

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.status not in (TicketStatus.RESOLVED, TicketStatus.CLOSED)

    @property
    def response_time_minutes(self) -> int | None:
        if not self.first_response_at:
            return None
        delta = self.first_response_at - self.created_at
        return int(delta.total_seconds() / 60)

    @property
    def resolution_time_hours(self) -> float | None:
        if not self.resolved_at:
            return None
        delta = self.resolved_at - self.created_at
        return delta.total_seconds() / 3600

    @property
    def sla_response_due(self) -> datetime:
        minutes = SLA_RESPONSE_MINUTES[self.priority]
        return self.created_at + timedelta(minutes=minutes)

    @property
    def sla_resolution_due(self) -> datetime:
        hours = SLA_RESOLUTION_HOURS[self.priority]
        return self.created_at + timedelta(hours=hours)

    @property
    def is_sla_breached(self) -> bool:
        now = datetime.now(timezone.utc)
        if not self.first_response_at and now > self.sla_response_due:
            return True
        if self.is_open and now > self.sla_resolution_due:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "status": self.status.value,
            "priority": self.priority.value,
            "category": self.category,
            "is_open": self.is_open,
            "created_at": self.created_at.isoformat(),
            "sla_response_due": self.sla_response_due.isoformat(),
            "sla_resolution_due": self.sla_resolution_due.isoformat(),
            "is_sla_breached": self.is_sla_breached,
            "response_time_minutes": self.response_time_minutes,
            "resolution_time_hours": self.resolution_time_hours,
        }


@dataclass
class KnowledgeArticle:
    """Knowledge base article."""

    id: str
    title: str
    content: str
    summary: str = ""

    category: str = "general"
    tags: list[str] = field(default_factory=list)

    view_count: int = 0
    helpful_count: int = 0
    not_helpful_count: int = 0

    published: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def helpfulness_score(self) -> float:
        total = self.helpful_count + self.not_helpful_count
        if total == 0:
            return 0.0
        return self.helpful_count / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "category": self.category,
            "tags": self.tags,
            "view_count": self.view_count,
            "helpfulness_score": self.helpfulness_score,
        }


class SupportStore(Protocol):
    """Protocol for support data storage."""

    async def create_ticket(self, ticket: Ticket) -> str: ...
    async def get_ticket(self, ticket_id: str) -> Ticket | None: ...
    async def update_ticket(self, ticket: Ticket) -> None: ...
    async def list_tickets(
        self, org_id: str, status: TicketStatus | None = None
    ) -> list[Ticket]: ...
    async def get_article(self, article_id: str) -> KnowledgeArticle | None: ...
    async def search_articles(self, query: str, limit: int = 5) -> list[KnowledgeArticle]: ...
    async def increment_article_view(self, article_id: str) -> None: ...


class SupportManager:
    """Manages support tickets and knowledge base."""

    def __init__(self, store: SupportStore):
        self._store = store

    async def create_ticket(
        self,
        org_id: str,
        user_id: str,
        subject: str,
        description: str,
        priority: TicketPriority = TicketPriority.MEDIUM,
        category: str = "general",
    ) -> Ticket:
        """Create a new support ticket."""
        ticket_id = f"tkt_{org_id}_{int(datetime.now(timezone.utc).timestamp())}"

        detected_priority = self._detect_priority(subject, description)
        if detected_priority.value > priority.value:
            priority = detected_priority

        ticket = Ticket(
            id=ticket_id,
            org_id=org_id,
            user_id=user_id,
            subject=subject,
            description=description,
            priority=priority,
            category=category,
        )

        await self._store.create_ticket(ticket)
        await self._notify_support_team(ticket)

        log.info(
            "Created ticket %s for org %s (priority=%s)",
            ticket_id,
            org_id,
            priority.value,
        )
        return ticket

    def _detect_priority(self, subject: str, description: str) -> TicketPriority:
        """Auto-detect priority from content."""
        text = f"{subject} {description}".lower()

        urgent_patterns = [
            r"production.*down",
            r"outage",
            r"data loss",
            r"security.*breach",
            r"urgent",
            r"critical",
        ]
        for pattern in urgent_patterns:
            if re.search(pattern, text):
                return TicketPriority.URGENT

        high_patterns = [
            r"not working",
            r"error",
            r"failing",
            r"broken",
            r"blocked",
        ]
        for pattern in high_patterns:
            if re.search(pattern, text):
                return TicketPriority.HIGH

        return TicketPriority.MEDIUM

    async def _notify_support_team(self, ticket: Ticket) -> None:
        """Notify support team of new ticket."""
        log.info(
            "Notifying support team of ticket %s (priority=%s)",
            ticket.id,
            ticket.priority.value,
        )

    async def add_response(
        self,
        ticket_id: str,
        author_id: str,
        content: str,
        is_agent: bool = False,
        is_internal: bool = False,
    ) -> Ticket:
        """Add a response to a ticket."""
        ticket = await self._store.get_ticket(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket not found: {ticket_id}")

        now = datetime.now(timezone.utc)
        message = TicketMessage(
            id=f"msg_{int(now.timestamp())}",
            author_id=author_id,
            author_type="agent" if is_agent else "customer",
            content=content,
            created_at=now,
            is_internal=is_internal,
        )

        ticket.messages.append(message)
        ticket.updated_at = now

        if is_agent and not ticket.first_response_at:
            ticket.first_response_at = now

        if is_agent and ticket.status == TicketStatus.OPEN:
            ticket.status = TicketStatus.IN_PROGRESS
        elif not is_agent and ticket.status == TicketStatus.WAITING_ON_CUSTOMER:
            ticket.status = TicketStatus.OPEN

        await self._store.update_ticket(ticket)
        return ticket

    async def resolve_ticket(
        self,
        ticket_id: str,
        resolution: str,
    ) -> Ticket:
        """Mark ticket as resolved."""
        ticket = await self._store.get_ticket(ticket_id)
        if not ticket:
            raise ValueError(f"Ticket not found: {ticket_id}")

        now = datetime.now(timezone.utc)
        ticket.status = TicketStatus.RESOLVED
        ticket.resolved_at = now
        ticket.updated_at = now

        message = TicketMessage(
            id=f"msg_{int(now.timestamp())}",
            author_id="system",
            author_type="system",
            content=f"Resolved: {resolution}",
            created_at=now,
        )
        ticket.messages.append(message)

        await self._store.update_ticket(ticket)

        log.info(
            "Resolved ticket %s (response_time=%s min, resolution_time=%.1f hrs)",
            ticket_id,
            ticket.response_time_minutes,
            ticket.resolution_time_hours,
        )
        return ticket

    async def search_knowledge_base(
        self,
        query: str,
        limit: int = 5,
    ) -> list[KnowledgeArticle]:
        """Search knowledge base articles."""
        articles = await self._store.search_articles(query, limit)
        return articles

    async def get_article(
        self,
        article_id: str,
        track_view: bool = True,
    ) -> KnowledgeArticle | None:
        """Get a knowledge base article."""
        article = await self._store.get_article(article_id)
        if article and track_view:
            await self._store.increment_article_view(article_id)
        return article

    async def suggest_articles(
        self,
        ticket: Ticket,
    ) -> list[KnowledgeArticle]:
        """Suggest relevant articles for a ticket."""
        search_text = f"{ticket.subject} {ticket.description}"
        articles = await self._store.search_articles(search_text, limit=3)
        return articles

    async def get_sla_metrics(self, org_id: str) -> dict[str, Any]:
        """Get SLA compliance metrics."""
        tickets = await self._store.list_tickets(org_id)

        total = len(tickets)
        if total == 0:
            return {"total_tickets": 0}

        response_times = [
            t.response_time_minutes for t in tickets if t.response_time_minutes
        ]
        resolution_times = [
            t.resolution_time_hours for t in tickets if t.resolution_time_hours
        ]
        breached = sum(1 for t in tickets if t.is_sla_breached)

        return {
            "total_tickets": total,
            "open_tickets": sum(1 for t in tickets if t.is_open),
            "sla_breach_count": breached,
            "sla_compliance_rate": (total - breached) / total,
            "avg_response_time_minutes": (
                sum(response_times) / len(response_times) if response_times else 0
            ),
            "avg_resolution_time_hours": (
                sum(resolution_times) / len(resolution_times) if resolution_times else 0
            ),
            "by_priority": {
                p.value: sum(1 for t in tickets if t.priority == p)
                for p in TicketPriority
            },
        }
