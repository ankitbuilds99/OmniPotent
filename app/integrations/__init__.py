"""External delivery / trigger integrations.

These are pure helpers - signature verification, payload parsing, and message
building - with no dependency on the agent or FastAPI app, so they're easy to
unit-test. The API layer (app/api/main.py) wires them to the agent.

Connectors:
- Slack: Chat and bot integrations
- Jira: Issue tracking and project management
- Linear: Modern issue tracking with GraphQL
- Opsgenie: Alert and on-call management
- NewRelic: APM and observability
- PagerDuty: Incident management
- Teams: Microsoft Teams notifications
"""

# Slack integration
from app.integrations.slack_bot import (
    SlackAction,
    SlackBlockBuilder,
    SlackBot,
    SlackChannel,
    SlackCommand,
    SlackEventType,
    SlackMessage,
    SlackUser,
    get_slack_bot,
)

# Jira integration
from app.integrations.jira import (
    JiraClient,
    JiraComment,
    JiraConfig,
    JiraIssue,
    JiraIssueType,
    JiraPriority,
    add_investigation_update as jira_add_investigation_update,
    create_action_item_ticket,
    create_incident_ticket as jira_create_incident_ticket,
    get_jira_client,
    sync_incident_status as jira_sync_incident_status,
)

# Linear integration
from app.integrations.linear import (
    LinearClient,
    LinearComment,
    LinearConfig,
    LinearIssue,
    LinearPriority,
    LinearStateType,
    LinearTeam,
    add_investigation_update as linear_add_investigation_update,
    create_incident_issue as linear_create_incident_issue,
    get_linear_client,
    sync_incident_status as linear_sync_incident_status,
)

# Opsgenie integration
from app.integrations.opsgenie import (
    OpsgenieAlert,
    OpsgenieAlertPriority,
    OpsgenieAlertStatus,
    OpsgenieClient,
    OpsgenieConfig,
    OpsgenieOnCallParticipant,
    OpsgenieSchedule,
    add_investigation_update as opsgenie_add_investigation_update,
    create_incident_alert as opsgenie_create_incident_alert,
    get_current_on_call,
    get_opsgenie_client,
    sync_incident_status as opsgenie_sync_incident_status,
)

# New Relic integration
from app.integrations.newrelic import (
    NewRelicAlert,
    NewRelicApplication,
    NewRelicClient,
    NewRelicConfig,
    NRQLResult,
    get_active_alerts as newrelic_get_active_alerts,
    get_application_health,
    get_newrelic_client,
    get_recent_errors,
    query_metrics,
)

__all__ = [
    # Slack
    "SlackAction",
    "SlackBlockBuilder",
    "SlackBot",
    "SlackChannel",
    "SlackCommand",
    "SlackEventType",
    "SlackMessage",
    "SlackUser",
    "get_slack_bot",
    # Jira
    "JiraClient",
    "JiraComment",
    "JiraConfig",
    "JiraIssue",
    "JiraIssueType",
    "JiraPriority",
    "create_action_item_ticket",
    "get_jira_client",
    "jira_add_investigation_update",
    "jira_create_incident_ticket",
    "jira_sync_incident_status",
    # Linear
    "LinearClient",
    "LinearComment",
    "LinearConfig",
    "LinearIssue",
    "LinearPriority",
    "LinearStateType",
    "LinearTeam",
    "get_linear_client",
    "linear_add_investigation_update",
    "linear_create_incident_issue",
    "linear_sync_incident_status",
    # Opsgenie
    "OpsgenieAlert",
    "OpsgenieAlertPriority",
    "OpsgenieAlertStatus",
    "OpsgenieClient",
    "OpsgenieConfig",
    "OpsgenieOnCallParticipant",
    "OpsgenieSchedule",
    "get_current_on_call",
    "get_opsgenie_client",
    "opsgenie_add_investigation_update",
    "opsgenie_create_incident_alert",
    "opsgenie_sync_incident_status",
    # New Relic
    "NewRelicAlert",
    "NewRelicApplication",
    "NewRelicClient",
    "NewRelicConfig",
    "NRQLResult",
    "get_application_health",
    "get_newrelic_client",
    "get_recent_errors",
    "newrelic_get_active_alerts",
    "query_metrics",
]
