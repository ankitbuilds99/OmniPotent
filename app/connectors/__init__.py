"""External Service Connectors.

Production-ready connectors for enterprise observability and DevOps tools.
Each connector provides a unified interface for querying external services.

Connectors:
- Prometheus: Metrics and alerting
- Grafana: Dashboards and annotations
- New Relic: APM and infrastructure monitoring
- GitLab: CI/CD and repository management
- Jenkins: Build automation
- ServiceNow: ITSM and incident management
- Splunk: Log analytics
"""

from app.connectors.base import (
    Connector,
    ConnectorConfig,
    ConnectorError,
    QueryResult,
)
from app.connectors.prometheus import PrometheusConnector
from app.connectors.grafana import GrafanaConnector
from app.connectors.newrelic import NewRelicConnector
from app.connectors.gitlab import GitLabConnector
from app.connectors.jenkins import JenkinsConnector
from app.connectors.servicenow import ServiceNowConnector
from app.connectors.splunk import SplunkConnector

__all__ = [
    "Connector",
    "ConnectorConfig",
    "ConnectorError",
    "QueryResult",
    "PrometheusConnector",
    "GrafanaConnector",
    "NewRelicConnector",
    "GitLabConnector",
    "JenkinsConnector",
    "ServiceNowConnector",
    "SplunkConnector",
]
