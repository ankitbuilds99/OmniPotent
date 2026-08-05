"""Advanced Slack Bot for OmniPotent.

Best-in-class Slack integration matching incident.io/Rootly quality:
- Conversational AI with context retention
- Incident war rooms with auto-created channels
- Interactive Block Kit components with Acknowledge/Escalate/Resolve/Snooze
- Comprehensive slash commands (/incident, /oncall, /status, /warroom, /postmortem)
- Rich message formatting with incident cards
- Status updates to stakeholder channels
- Thread management for investigations
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
    get_critical_incident_manager,
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
from app.slack_bot.interactions import (
    IncidentAction,
    ActionResult,
    IncidentActionHandler,
    get_incident_action_handler,
)
from app.slack_bot.messages import (
    MessageBuilder,
    MessageType,
    IncidentData,
    get_message_builder,
)

__all__ = [
    # Core
    "SlackBot",
    "SlackBotConfig",
    "get_slack_bot",
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
    "get_critical_incident_manager",
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
    # Interactions (incident actions)
    "IncidentAction",
    "ActionResult",
    "IncidentActionHandler",
    "get_incident_action_handler",
    # Messages (rich formatting)
    "MessageBuilder",
    "MessageType",
    "IncidentData",
    "get_message_builder",
]
