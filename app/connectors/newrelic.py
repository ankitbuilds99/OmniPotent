"""New Relic Connector - APM and infrastructure monitoring."""

from __future__ import annotations

import logging
import time

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("omnipotent.connectors.newrelic")


class NewRelicConnector(Connector):
    """Connector for New Relic One platform.

    Supports:
    - NRQL queries
    - APM data (transactions, errors)
    - Infrastructure data
    - Entity queries
    - Alerts and incidents
    """

    GRAPHQL_ENDPOINT = "/graphql"

    @property
    def connector_type(self) -> str:
        return "newrelic"

    def _default_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "API-Key": self.config.api_key,
        }

    async def health_check(self) -> bool:
        """Check New Relic is reachable."""
        try:
            result = await self.nrql("SELECT count(*) FROM Transaction SINCE 1 minute ago")
            return result.success
        except ConnectorError:
            return False

    async def nrql(self, query: str, account_id: str | None = None) -> QueryResult:
        """Execute an NRQL query.

        Args:
            query: NRQL query string
            account_id: Account ID (defaults to config)

        Returns:
            QueryResult with query results
        """
        start = time.time()
        acct = account_id or self.config.extra.get("account_id", "")

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
        """ % (acct, query.replace('"', '\\"'))

        try:
            response = await self._post(
                self.GRAPHQL_ENDPOINT,
                json={"query": graphql_query},
            )
            data = response.json()

            if "errors" in data:
                return QueryResult(
                    success=False,
                    data=[],
                    error=str(data["errors"]),
                    duration_ms=int((time.time() - start) * 1000),
                )

            results = (
                data.get("data", {})
                .get("actor", {})
                .get("account", {})
                .get("nrql", {})
                .get("results", [])
            )

            return QueryResult(
                success=True,
                data=results,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"query": query, "account_id": acct},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_applications(self) -> QueryResult:
        """Get APM applications."""
        return await self.nrql(
            "SELECT uniques(appName) FROM Transaction SINCE 1 day ago"
        )

    async def get_transactions(
        self,
        app_name: str,
        since: str = "1 hour ago",
        limit: int = 100,
    ) -> QueryResult:
        """Get transactions for an application.

        Args:
            app_name: Application name
            since: Time range (NRQL syntax)
            limit: Max results

        Returns:
            QueryResult with transaction data
        """
        query = f"""
        SELECT name, duration, httpResponseCode, error
        FROM Transaction
        WHERE appName = '{app_name}'
        SINCE {since}
        LIMIT {limit}
        """
        return await self.nrql(query)

    async def get_errors(
        self,
        app_name: str | None = None,
        since: str = "1 hour ago",
        limit: int = 100,
    ) -> QueryResult:
        """Get error traces.

        Args:
            app_name: Filter by application
            since: Time range
            limit: Max results

        Returns:
            QueryResult with error data
        """
        where = f"WHERE appName = '{app_name}'" if app_name else ""
        query = f"""
        SELECT error.message, error.class, transactionName, appName
        FROM TransactionError
        {where}
        SINCE {since}
        LIMIT {limit}
        """
        return await self.nrql(query)

    async def get_hosts(self) -> QueryResult:
        """Get infrastructure hosts."""
        query = """
        SELECT uniqueCount(hostname), latest(cpuPercent), latest(memoryUsedPercent)
        FROM SystemSample
        FACET hostname
        SINCE 10 minutes ago
        """
        return await self.nrql(query)

    async def get_alerts(self, state: str | None = None) -> QueryResult:
        """Get alert incidents.

        Args:
            state: Filter by state (open, closed)
        """
        start = time.time()

        graphql_query = """
        {
            actor {
                entitySearch(query: "alertSeverity IS NOT NULL") {
                    results {
                        entities {
                            name
                            alertSeverity
                            reporting
                        }
                    }
                }
            }
        }
        """

        try:
            response = await self._post(
                self.GRAPHQL_ENDPOINT,
                json={"query": graphql_query},
            )
            data = response.json()

            entities = (
                data.get("data", {})
                .get("actor", {})
                .get("entitySearch", {})
                .get("results", {})
                .get("entities", [])
            )

            # Filter entities with active alerts
            alerting = [e for e in entities if e.get("alertSeverity") not in (None, "NOT_CONFIGURED")]

            return QueryResult(
                success=True,
                data=alerting,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"total_entities": len(entities), "alerting": len(alerting)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_service_levels(self, entity_guid: str | None = None) -> QueryResult:
        """Get SLO/SLI data.

        Args:
            entity_guid: Optional entity to filter by
        """
        query = """
        SELECT percentage(count(*), WHERE error IS false) as availability,
               percentile(duration, 95) as latency_p95
        FROM Transaction
        FACET appName
        SINCE 1 hour ago
        """
        return await self.nrql(query)

    async def get_deployments(
        self,
        app_name: str,
        since: str = "7 days ago",
    ) -> QueryResult:
        """Get deployment markers.

        Args:
            app_name: Application name
            since: Time range
        """
        query = f"""
        SELECT *
        FROM Deployment
        WHERE appName = '{app_name}'
        SINCE {since}
        """
        return await self.nrql(query)
