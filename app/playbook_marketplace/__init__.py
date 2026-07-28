"""Playbook Marketplace.

Features:
- Community contributions
- Quality ratings and reviews
- Auto-adaptation to user's stack
- Revenue sharing for premium playbooks
- AI playbook generation
"""

from app.playbook_marketplace.catalog import (
    Playbook,
    PlaybookCatalog,
    PlaybookCategory,
)
from app.playbook_marketplace.ratings import (
    PlaybookRating,
    RatingsEngine,
)
from app.playbook_marketplace.publishing import (
    Publisher,
    PublisherStatus,
    PublishingManager,
)
from app.playbook_marketplace.adaptation import (
    StackAdapter,
    AdaptationResult,
)
from app.playbook_marketplace.ai_generator import (
    AIPlaybookGenerator,
    GenerationRequest,
)

__all__ = [
    "Playbook",
    "PlaybookCatalog",
    "PlaybookCategory",
    "PlaybookRating",
    "RatingsEngine",
    "Publisher",
    "PublisherStatus",
    "PublishingManager",
    "StackAdapter",
    "AdaptationResult",
    "AIPlaybookGenerator",
    "GenerationRequest",
]
