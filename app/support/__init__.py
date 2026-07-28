"""Support and help system module.

Provides:
- Support ticket management
- Knowledge base search
- In-app help system
"""

from app.support.help_system import (
    Ticket,
    TicketStatus,
    TicketPriority,
    KnowledgeArticle,
    SupportManager,
)

__all__ = [
    "Ticket",
    "TicketStatus",
    "TicketPriority",
    "KnowledgeArticle",
    "SupportManager",
]
