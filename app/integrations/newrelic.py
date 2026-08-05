"""New Relic integration for APM and observability.

Provides:
- NRQL queries for metrics
- APM application data
- Alert/incident management
- Infrastructure monitoring
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("omnipotent.integrations.newrelic")


@dataclass
class NewRelicConfig:
    """New Relic integration configuration."""

    api_key: str = ""
    account_id: str = ""
    region: str = "US"  # US or EU

    @classmethod
    def from_env(cls) -> "NewRelicConfig":
        """Load config from environment."""
        return cls(
            api_key=os.environ.get("NEWRELIC_API_KEY", ""),
            account_id=os.environ.get("NEWRELIC_ACCOUNT_ID", ""),
            region=os.environ.get("NEWRELIC_REGION", "US").upper(),
        )

    def is_configured(self) -> bool:
        """Check if New Relic is configured."""
        return bool(self.api_key and self.account_id)

    @property
    def graphql_url(self) -> str:
        """Get GraphQL API URL based on region."""
        if self.region == "EU":
            return "https://api.eu.newrelic.com/graphql"
        return "https://api.newrelic.com/graphql"

    @property
    def rest_api_url(self) -> str:
        """Get REST API URL based on region."""
        if self.region == "EU":
            return "https://api.eu.newrelic.com/v2"
        return "https://api.newrelic.com/v2"


@dataclass
class NRQLResult:
    """Result from an NRQL query."""

    success: bool
    data: list[dict[str, Any]]
    error: str | None = None
    query: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "query": self.query,
            "duration_ms": self.duration_ms,
        }


@dataclass
class NewRelicAlert:
    """A New Relic alert incident."""

    id: str
    incident_id: str
    title: str
    priority: str
    state: str
    policy_name: str = ""
    condition_name: str = ""
    entity_name: str = ""
    entity_type: str = ""
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "title": self.title,
            "priority": self.priority,
            "state": self.state,
            "policy_name": self.policy_name,
            "condition_name": self.condition_name,
            "entity_name": self.entity_name,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "url": self.url,
        }


@dataclass
class NewRelicApplication:
    """A New Relic APM application."""

    id: str
    name: str
    language: str = ""
    health_status: str = ""
    reporting: bool = True
    last_reported_at: datetime | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "health_status": self.health_status,
            "reporting": self.reporting,
        }


class NewRelicClient:
    """New Relic API client."""

    def __init__(self, config: NewRelicConfig | None = None):
        self.config = config or NewRelicConfig.from_env()

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Api-Key": self.config.api_key,
            "Content-Type": "application/json",
        }

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query."""
        if not self.config.is_configured():
            log.warning("New Relic not configured")
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.config.graphql_url,
                    headers=self._get_headers(),
                    json={"query": query, "variables": variables or {}},
                    timeout=30,
                )

                if resp.status_code >= 400:
                    log.error("New Relic API error: %s %s", resp.status_code, resp.text)
                    return None

                result = resp.json()
                if "errors" in result:
                    log.error("New Relic GraphQL errors: %s", result["errors"])
                    return None

                return result.get("data")
        except Exception as e:
            log.error("New Relic request failed: %s", e)
            return None

    async def _rest_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Make a REST API request."""
        if not self.config.is_configured():
            log.warning("New Relic not configured")
            return None

        url = f"{self.config.rest_api_url}{endpoint}"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    url,
                    headers=self._get_headers(),
                    json=data,
                    params=params,
                    timeout=30,
                )

                if resp.status_code >= 400:
                    log.error("New Relic API error: %s %s", resp.status_code, resp.text)
                    return None

                return resp.json()
        except Exception as e:
            log.error("New Relic request failed: %s", e)
            return None

    # NRQL Queries
    async def nrql(
        self,
        query: str,
        account_id: str | None = None,
    ) -> NRQLResult:
        """Execute an NRQL query.

        Args:
            query: NRQL query string
            account_id: Account ID (defaults to config)

        Returns:
            NRQLResult with query results
        """
        import time

        start = time.time()
        acct = account_id or self.config.account_id

        # Escape the query for GraphQL
        escaped_query = query.replace("\\", "\\\\").replace('"', '\\"')

        graphql_query = """
        {
            actor {
                account(id: %s) {
                    nrql(query: "%s") {
                        results
                    }
                }
            }
        }
        """ % (
            acct,
            escaped_query,
        )

        result = await self._graphql(graphql_query)
        duration_ms = int((time.time() - start) * 1000)

        if not result:
            return NRQLResult(
                success=False,
                data=[],
                error="Failed to execute NRQL query",
                query=query,
                duration_ms=duration_ms,
            )

        results = (
            result.get("actor", {})
            .get("account", {})
            .get("nrql", {})
            .get("results", [])
        )

        return NRQLResult(
            success=True,
            data=results,
            query=query,
            duration_ms=duration_ms,
        )

    async def get_error_rate(
        self,
        app_name: str,
        since: str = "1 hour ago",
    ) -> NRQLResult:
        """Get error rate for an application."""
        query = f"""
        SELECT percentage(count(*), WHERE error IS true) as errorRate
        FROM Transaction
        WHERE appName = '{app_name}'
        SINCE {since}
        """
        return await self.nrql(query)

    async def get_throughput(
        self,
        app_name: str,
        since: str = "1 hour ago",
    ) -> NRQLResult:
        """Get throughput (requests per minute) for an application."""
        query = f"""
        SELECT rate(count(*), 1 minute) as rpm
        FROM Transaction
        WHERE appName = '{app_name}'
        SINCE {since}
        TIMESERIES
        """
        return await self.nrql(query)

    async def get_response_time(
        self,
        app_name: str,
        since: str = "1 hour ago",
    ) -> NRQLResult:
        """Get response time percentiles for an application."""
        query = f"""
        SELECT average(duration) as avgDuration,
               percentile(duration, 50) as p50,
               percentile(duration, 95) as p95,
               percentile(duration, 99) as p99
        FROM Transaction
        WHERE appName = '{app_name}'
        SINCE {since}
        """
        return await self.nrql(query)

    async def get_errors(
        self,
        app_name: str | None = None,
        since: str = "1 hour ago",
        limit: int = 100,
    ) -> NRQLResult:
        """Get error traces."""
        where_clause = f"WHERE appName = '{app_name}'" if app_name else ""
        query = f"""
        SELECT message, error.class, transactionName, appName, timestamp
        FROM TransactionError
        {where_clause}
        SINCE {since}
        LIMIT {limit}
        """
        return await self.nrql(query)

    async def get_slowest_transactions(
        self,
        app_name: str,
        since: str = "1 hour ago",
        limit: int = 10,
    ) -> NRQLResult:
        """Get slowest transactions."""
        query = f"""
        SELECT average(duration) as avgDuration, count(*) as count
        FROM Transaction
        WHERE appName = '{app_name}'
        FACET name
        SINCE {since}
        LIMIT {limit}
        """
        return await self.nrql(query)

    # APM Applications
    async def get_applications(self) -> list[NewRelicApplication]:
        """Get all APM applications."""
        query = """
        {
            actor {
                entitySearch(query: "domain = 'APM' AND type = 'APPLICATION'") {
                    results {
                        entities {
                            guid
                            name
                            reporting
                            alertSeverity
                            ... on ApmApplicationEntityOutline {
                                language
                            }
                        }
                    }
                }
            }
        }
        """

        result = await self._graphql(query)
        if not result:
            return []

        entities = (
            result.get("actor", {})
            .get("entitySearch", {})
            .get("results", {})
            .get("entities", [])
        )

        return [
            NewRelicApplication(
                id=entity.get("guid", ""),
                name=entity.get("name", ""),
                language=entity.get("language", ""),
                health_status=entity.get("alertSeverity", ""),
                reporting=entity.get("reporting", False),
            )
            for entity in entities
        ]

    async def get_application(self, app_name: str) -> NewRelicApplication | None:
        """Get an APM application by name."""
        query = """
        {
            actor {
                entitySearch(query: "domain = 'APM' AND type = 'APPLICATION' AND name = '%s'") {
                    results {
                        entities {
                            guid
                            name
                            reporting
                            alertSeverity
                            ... on ApmApplicationEntityOutline {
                                language
                            }
                        }
                    }
                }
            }
        }
        """ % app_name

        result = await self._graphql(query)
        if not result:
            return None

        entities = (
            result.get("actor", {})
            .get("entitySearch", {})
            .get("results", {})
            .get("entities", [])
        )

        if not entities:
            return None

        entity = entities[0]
        return NewRelicApplication(
            id=entity.get("guid", ""),
            name=entity.get("name", ""),
            language=entity.get("language", ""),
            health_status=entity.get("alertSeverity", ""),
            reporting=entity.get("reporting", False),
        )

    # Alerts and Incidents
    async def get_alerts(
        self,
        state: str | None = None,
        priority: str | None = None,
    ) -> list[NewRelicAlert]:
        """Get alert incidents.

        Args:
            state: Filter by state (open, closed)
            priority: Filter by priority (critical, high, medium, low)
        """
        # Build filter conditions
        conditions = []
        if state:
            conditions.append(f'state = "{state.upper()}"')
        if priority:
            conditions.append(f'priority = "{priority.upper()}"')

        condition_str = " AND ".join(conditions) if conditions else ""

        query = """
        {
            actor {
                account(id: %s) {
                    aiIssues {
                        issues(filter: { %s }) {
                            issues {
                                issueId
                                title
                                priority
                                state
                                activatedAt
                                closedAt
                                sources {
                                    alertPolicyNames
                                    alertConditionNames
                                }
                                impactedEntities {
                                    name
                                    type
                                }
                            }
                        }
                    }
                }
            }
        }
        """ % (
            self.config.account_id,
            condition_str,
        )

        result = await self._graphql(query)
        if not result:
            return []

        issues = (
            result.get("actor", {})
            .get("account", {})
            .get("aiIssues", {})
            .get("issues", {})
            .get("issues", [])
        )

        alerts = []
        for issue in issues:
            sources = issue.get("sources", [{}])
            impacted = issue.get("impactedEntities", [{}])

            alerts.append(
                NewRelicAlert(
                    id=issue.get("issueId", ""),
                    incident_id=issue.get("issueId", ""),
                    title=issue.get("title", ""),
                    priority=issue.get("priority", ""),
                    state=issue.get("state", ""),
                    policy_name=sources[0].get("alertPolicyNames", [""])[0] if sources else "",
                    condition_name=sources[0].get("alertConditionNames", [""])[0] if sources else "",
                    entity_name=impacted[0].get("name", "") if impacted else "",
                    entity_type=impacted[0].get("type", "") if impacted else "",
                    opened_at=self._parse_datetime(issue.get("activatedAt")),
                    closed_at=self._parse_datetime(issue.get("closedAt")),
                )
            )

        return alerts

    async def get_alert(self, issue_id: str) -> NewRelicAlert | None:
        """Get a specific alert incident."""
        query = """
        {
            actor {
                account(id: %s) {
                    aiIssues {
                        issues(filter: { ids: "%s" }) {
                            issues {
                                issueId
                                title
                                priority
                                state
                                activatedAt
                                closedAt
                                sources {
                                    alertPolicyNames
                                    alertConditionNames
                                }
                                impactedEntities {
                                    name
                                    type
                                }
                            }
                        }
                    }
                }
            }
        }
        """ % (
            self.config.account_id,
            issue_id,
        )

        result = await self._graphql(query)
        if not result:
            return None

        issues = (
            result.get("actor", {})
            .get("account", {})
            .get("aiIssues", {})
            .get("issues", {})
            .get("issues", [])
        )

        if not issues:
            return None

        issue = issues[0]
        sources = issue.get("sources", [{}])
        impacted = issue.get("impactedEntities", [{}])

        return NewRelicAlert(
            id=issue.get("issueId", ""),
            incident_id=issue.get("issueId", ""),
            title=issue.get("title", ""),
            priority=issue.get("priority", ""),
            state=issue.get("state", ""),
            policy_name=sources[0].get("alertPolicyNames", [""])[0] if sources else "",
            condition_name=sources[0].get("alertConditionNames", [""])[0] if sources else "",
            entity_name=impacted[0].get("name", "") if impacted else "",
            entity_type=impacted[0].get("type", "") if impacted else "",
            opened_at=self._parse_datetime(issue.get("activatedAt")),
            closed_at=self._parse_datetime(issue.get("closedAt")),
        )

    def _parse_datetime(self, value: Any) -> datetime | None:
        """Parse datetime from timestamp or ISO string."""
        if not value:
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, AttributeError, TypeError):
            return None

    # Infrastructure
    async def get_hosts(
        self,
        since: str = "10 minutes ago",
    ) -> NRQLResult:
        """Get infrastructure hosts."""
        query = f"""
        SELECT latest(cpuPercent), latest(memoryUsedPercent), latest(diskUsedPercent)
        FROM SystemSample
        FACET hostname
        SINCE {since}
        """
        return await self.nrql(query)

    async def get_host_metrics(
        self,
        hostname: str,
        since: str = "1 hour ago",
    ) -> NRQLResult:
        """Get metrics for a specific host."""
        query = f"""
        SELECT average(cpuPercent), average(memoryUsedPercent), average(diskUsedPercent)
        FROM SystemSample
        WHERE hostname = '{hostname}'
        SINCE {since}
        TIMESERIES
        """
        return await self.nrql(query)

    # SLOs
    async def get_service_levels(
        self,
        app_name: str | None = None,
        since: str = "1 hour ago",
    ) -> NRQLResult:
        """Get SLO/SLI data."""
        where_clause = f"WHERE appName = '{app_name}'" if app_name else ""
        query = f"""
        SELECT percentage(count(*), WHERE error IS false) as availability,
               percentile(duration, 95) as latency_p95
        FROM Transaction
        {where_clause}
        FACET appName
        SINCE {since}
        """
        return await self.nrql(query)

    # Deployments
    async def record_deployment(
        self,
        app_id: str,
        revision: str,
        description: str = "",
        user: str = "DevOps Copilot",
        changelog: str = "",
    ) -> dict[str, Any] | None:
        """Record a deployment marker."""
        payload = {
            "deployment": {
                "revision": revision,
                "description": description,
                "user": user,
                "changelog": changelog,
            }
        }

        return await self._rest_request(
            "POST",
            f"/applications/{app_id}/deployments.json",
            data=payload,
        )

    async def get_deployments(
        self,
        app_name: str,
        since: str = "7 days ago",
    ) -> NRQLResult:
        """Get deployment markers."""
        query = f"""
        SELECT *
        FROM Deployment
        WHERE appName = '{app_name}'
        SINCE {since}
        """
        return await self.nrql(query)


_client: NewRelicClient | None = None


def get_newrelic_client() -> NewRelicClient:
    """Get the New Relic client singleton."""
    global _client
    if _client is None:
        _client = NewRelicClient()
    return _client


async def query_metrics(
    query: str,
    account_id: str | None = None,
) -> NRQLResult:
    """Execute an NRQL query for metrics."""
    client = get_newrelic_client()
    return await client.nrql(query, account_id)


async def get_application_health(
    app_name: str,
    since: str = "1 hour ago",
) -> dict[str, Any]:
    """Get comprehensive application health metrics."""
    client = get_newrelic_client()

    error_rate = await client.get_error_rate(app_name, since)
    throughput = await client.get_throughput(app_name, since)
    response_time = await client.get_response_time(app_name, since)
    app_info = await client.get_application(app_name)

    return {
        "application": app_info.to_dict() if app_info else None,
        "error_rate": error_rate.data[0] if error_rate.success and error_rate.data else None,
        "throughput": throughput.data if throughput.success else None,
        "response_time": response_time.data[0] if response_time.success and response_time.data else None,
    }


async def get_active_alerts() -> list[NewRelicAlert]:
    """Get all open alerts."""
    client = get_newrelic_client()
    return await client.get_alerts(state="open")


async def get_recent_errors(
    app_name: str | None = None,
    since: str = "1 hour ago",
    limit: int = 50,
) -> NRQLResult:
    """Get recent errors for troubleshooting."""
    client = get_newrelic_client()
    return await client.get_errors(app_name, since, limit)
