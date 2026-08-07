"""Advanced Slack Bot for OmniPotent.

Best-in-class Slack integration featuring:
- Real Slack API client with httpx
- Conversational AI with context retention
- Incident war rooms with auto-created channels
- Interactive Block Kit components
- Comprehensive slash commands
- Natural language understanding
- Live metrics embedding
- Workflow automation
- Collaboration features
"""

from app.slack_bot.core import (
    SlackBot,
    SlackBotConfig,
    get_slack_bot,
)
from app.slack_bot.client import (
    SlackClient,
    SlackClientConfig,
    SlackResponse,
    SlackAPIError,
    SlackRateLimitError,
    get_slack_client,
    close_slack_client,
)
from app.slack_bot.commands import (
    SlashCommand,
    CommandRouter,
    CommandResult,
    get_command_router,
)
from app.slack_bot.war_room import (
    WarRoom,
    WarRoomConfig,
    WarRoomManager,
    get_war_room_manager,
)
from app.slack_bot.blocks import (
    BlockBuilder,
    InvestigationCard,
    ApprovalCard,
    MetricsCard,
    TimelineCard,
    get_block_builder,
)
from app.slack_bot.conversation import (
    ConversationContext,
    ConversationManager,
    Intent,
    get_conversation_manager,
)
from app.slack_bot.workflows import (
    Workflow,
    WorkflowStep,
    WorkflowEngine,
    get_workflow_engine,
)
from app.slack_bot.handlers import (
    EventHandler,
    InteractionHandler,
    get_event_handler,
    get_interaction_handler,
)
from app.slack_bot.api import router as slack_router

__all__ = [
    # Core
    "SlackBot",
    "SlackBotConfig",
    "get_slack_bot",
    # Client (Real API)
    "SlackClient",
    "SlackClientConfig",
    "SlackResponse",
    "SlackAPIError",
    "SlackRateLimitError",
    "get_slack_client",
    "close_slack_client",
    # Commands
    "SlashCommand",
    "CommandRouter",
    "CommandResult",
    "get_command_router",
    # War Room
    "WarRoom",
    "WarRoomConfig",
    "WarRoomManager",
    "get_war_room_manager",
    # Blocks
    "BlockBuilder",
    "InvestigationCard",
    "ApprovalCard",
    "MetricsCard",
    "TimelineCard",
    "get_block_builder",
    # Conversation
    "ConversationContext",
    "ConversationManager",
    "Intent",
    "get_conversation_manager",
    # Workflows
    "Workflow",
    "WorkflowStep",
    "WorkflowEngine",
    "get_workflow_engine",
    # Handlers
    "EventHandler",
    "InteractionHandler",
    "get_event_handler",
    "get_interaction_handler",
    # API Router
    "slack_router",
]
