"""Advanced Slack-Native Incident Experience.

Goes beyond basic Slack integration with:
- Full investigation without leaving Slack
- Natural language command processing
- Thread summarization and context
- Real-time progress streaming
- Collaborative annotations
- Action confirmation flows
- Cross-channel correlation
- Scheduled digests
"""

from app.slack_advanced.investigation import (
    InvestigationSession,
    InvestigationStep,
    SlackInvestigationEngine,
    get_slack_investigation_engine,
)
from app.slack_advanced.natural_language import (
    Command,
    CommandType,
    NaturalLanguageProcessor,
    get_nlp_processor,
)
from app.slack_advanced.thread_summarizer import (
    ThreadSummary,
    ParticipantContribution,
    ThreadSummarizer,
    get_thread_summarizer,
)
from app.slack_advanced.action_flows import (
    ActionConfirmation,
    ConfirmationStatus,
    ActionFlowManager,
    get_action_flow_manager,
)
from app.slack_advanced.digests import (
    DigestSchedule,
    IncidentDigest,
    DigestManager,
    get_digest_manager,
)

__all__ = [
    # Investigation
    "InvestigationSession",
    "InvestigationStep",
    "SlackInvestigationEngine",
    "get_slack_investigation_engine",
    # Natural Language
    "Command",
    "CommandType",
    "NaturalLanguageProcessor",
    "get_nlp_processor",
    # Thread Summarizer
    "ThreadSummary",
    "ParticipantContribution",
    "ThreadSummarizer",
    "get_thread_summarizer",
    # Action Flows
    "ActionConfirmation",
    "ConfirmationStatus",
    "ActionFlowManager",
    "get_action_flow_manager",
    # Digests
    "DigestSchedule",
    "IncidentDigest",
    "DigestManager",
    "get_digest_manager",
]
