"""External delivery / trigger integrations.

Production-ready async clients for:
- Slack (messages, channels, interactivity)
- PagerDuty (incidents, services, events)
- OpsGenie (alerts, schedules, on-call)
- ServiceNow (incidents, changes, CMDB)
- Teams (messages, adaptive cards)
- Jira (issues, projects)
"""

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

from app.integrations.pagerduty_client import (
    PagerDutyClient,
    PagerDutyConfig,
    PagerDutyError,
    Incident as PDIncident,
    Service as PDService,
    User as PDUser,
    OnCall as PDOnCall,
    IncidentStatus as PDIncidentStatus,
    IncidentUrgency as PDIncidentUrgency,
    get_pagerduty_client,
    close_pagerduty_client,
)

from app.integrations.opsgenie_client import (
    OpsGenieClient,
    OpsGenieConfig,
    OpsGenieError,
    Alert as OGAlert,
    Schedule as OGSchedule,
    AlertStatus as OGAlertStatus,
    AlertPriority as OGAlertPriority,
    get_opsgenie_client,
    close_opsgenie_client,
)

from app.integrations.servicenow_client import (
    ServiceNowClient,
    ServiceNowConfig,
    ServiceNowError,
    Incident as SNIncident,
    Change as SNChange,
    CMDBItem,
    IncidentState as SNIncidentState,
    IncidentPriority as SNIncidentPriority,
    ChangeState as SNChangeState,
    get_servicenow_client,
    close_servicenow_client,
)

__all__ = [
    # Slack (legacy)
    "SlackAction",
    "SlackBlockBuilder",
    "SlackBot",
    "SlackChannel",
    "SlackCommand",
    "SlackEventType",
    "SlackMessage",
    "SlackUser",
    "get_slack_bot",
    # PagerDuty
    "PagerDutyClient",
    "PagerDutyConfig",
    "PagerDutyError",
    "PDIncident",
    "PDService",
    "PDUser",
    "PDOnCall",
    "PDIncidentStatus",
    "PDIncidentUrgency",
    "get_pagerduty_client",
    "close_pagerduty_client",
    # OpsGenie
    "OpsGenieClient",
    "OpsGenieConfig",
    "OpsGenieError",
    "OGAlert",
    "OGSchedule",
    "OGAlertStatus",
    "OGAlertPriority",
    "get_opsgenie_client",
    "close_opsgenie_client",
    # ServiceNow
    "ServiceNowClient",
    "ServiceNowConfig",
    "ServiceNowError",
    "SNIncident",
    "SNChange",
    "CMDBItem",
    "SNIncidentState",
    "SNIncidentPriority",
    "SNChangeState",
    "get_servicenow_client",
    "close_servicenow_client",
]
