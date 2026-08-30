"""New Relic observability MCP server.

Gives the agent APM and infrastructure monitoring capabilities:
- NRQL queries for metrics
- APM application data
- Alert/incident management
- Infrastructure monitoring

When `NEWRELIC_API_KEY` is set it calls the real New Relic GraphQL API; otherwise
it runs in OFFLINE DEMO mode with fixtures.

Live API (per https://docs.newrelic.com/docs/apis/nerdgraph/):
  - POST https://api.newrelic.com/graphql (US)
  - POST https://api.eu.newrelic.com/graphql (EU)
  - auth: API-Key header

Run standalone for debugging:
    NEWRELIC_API_KEY=... NEWRELIC_ACCOUNT_ID=... python -m app.mcp.servers.newrelic.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

NEWRELIC_API_KEY = os.environ.get("NEWRELIC_API_KEY", "").strip()
NEWRELIC_ACCOUNT_ID = os.environ.get("NEWRELIC_ACCOUNT_ID", "").strip()
NEWRELIC_REGION = os.environ.get("NEWRELIC_REGION", "US").strip().upper()
OFFLINE = not (NEWRELIC_API_KEY and NEWRELIC_ACCOUNT_ID)

# API endpoints
API_URL_US = "https://api.newrelic.com/graphql"
API_URL_EU = "https://api.eu.newrelic.com/graphql"

mcp = FastMCP("newrelic")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _api_url() -> str:
    return API_URL_EU if NEWRELIC_REGION == "EU" else API_URL_US


def _headers() -> dict:
    return {
        "API-Key": NEWRELIC_API_KEY,
        "Content-Type": "application/json",
    }


def _mcp_timeout() -> float:
    """Deadline for the outbound NerdGraph calls (HTTP_TIMEOUT_MCP).

    Read per call rather than frozen at import: tests monkeypatch the env, and a
    long-lived server process should honour a value set after it started.
    """
    try:
        return float(os.environ.get("HTTP_TIMEOUT_MCP", "") or 20.0)
    except ValueError:
        # A malformed override must not take the timeout to infinity.
        return 20.0


def _demo(payload):
    """Label fixture data so the agent can't mistake it for a live New Relic read.

    Offline responses were shaped exactly like real API ones, so an RCA built on
    invented error rates looked identical to one built on real telemetry.
    """
    from app.mcp.fixtures import mark_demo
    return mark_demo(payload, "newrelic")


def _gate() -> dict | None:
    """Fail closed when credentials are absent outside demo mode: fixtures are a
    demo feature, not a production fallback (app/config.py's contract)."""
    from app.mcp.fixtures import offline_gate
    return offline_gate("New Relic", "NEWRELIC_API_KEY and NEWRELIC_ACCOUNT_ID")


# --- Offline fixtures ---
def _get_fixture_apps() -> list[dict]:
    return [
        {
            "id": "app-guid-1",
            "name": "checkout-svc",
            "language": "nodejs",
            "health_status": "CRITICAL",
            "reporting": True,
        },
        {
            "id": "app-guid-2",
            "name": "payment-svc",
            "language": "java",
            "health_status": "WARNING",
            "reporting": True,
        },
        {
            "id": "app-guid-3",
            "name": "inventory-svc",
            "language": "python",
            "health_status": "NOT_ALERTING",
            "reporting": True,
        },
    ]


def _get_fixture_metrics() -> dict:
    """Return fixture metrics matching the demo scenario."""
    return {
        "checkout-svc": {
            "error_rate": [
                {"ts": 1705320000, "value": 0.02},
                {"ts": 1705320300, "value": 0.03},
                {"ts": 1705320600, "value": 0.08},
                {"ts": 1705320900, "value": 0.15},
                {"ts": 1705321200, "value": 0.12},
            ],
            "throughput": [
                {"ts": 1705320000, "value": 1200},
                {"ts": 1705320300, "value": 1150},
                {"ts": 1705320600, "value": 980},
                {"ts": 1705320900, "value": 850},
                {"ts": 1705321200, "value": 920},
            ],
            "latency_p95": [
                {"ts": 1705320000, "value": 120},
                {"ts": 1705320300, "value": 135},
                {"ts": 1705320600, "value": 280},
                {"ts": 1705320900, "value": 450},
                {"ts": 1705321200, "value": 380},
            ],
        },
        "payment-svc": {
            "error_rate": [
                {"ts": 1705320000, "value": 0.01},
                {"ts": 1705320300, "value": 0.01},
                {"ts": 1705320600, "value": 0.02},
                {"ts": 1705320900, "value": 0.02},
                {"ts": 1705321200, "value": 0.01},
            ],
            "throughput": [
                {"ts": 1705320000, "value": 500},
                {"ts": 1705320300, "value": 480},
                {"ts": 1705320600, "value": 420},
                {"ts": 1705320900, "value": 380},
                {"ts": 1705321200, "value": 410},
            ],
            "latency_p95": [
                {"ts": 1705320000, "value": 200},
                {"ts": 1705320300, "value": 220},
                {"ts": 1705320600, "value": 350},
                {"ts": 1705320900, "value": 520},
                {"ts": 1705321200, "value": 480},
            ],
        },
    }


def _get_fixture_alerts() -> list[dict]:
    return [
        {
            "id": "issue-1",
            "title": "checkout-svc error rate > 5%",
            "priority": "CRITICAL",
            "state": "ACTIVATED",
            "policy_name": "Production Alerts",
            "condition_name": "High Error Rate",
            "entity_name": "checkout-svc",
        },
        {
            "id": "issue-2",
            "title": "payment-svc latency > 500ms",
            "priority": "HIGH",
            "state": "ACTIVATED",
            "policy_name": "Production Alerts",
            "condition_name": "High Latency",
            "entity_name": "payment-svc",
        },
    ]


@mcp.tool()
def nrql(query: str, account_id: str = "") -> dict:
    """Execute an NRQL query.

    Args:
        query: NRQL query string.
        account_id: Account ID (defaults to NEWRELIC_ACCOUNT_ID env var).

    Returns:
        Query results with data array.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        # Return fixture data for common queries
        query_lower = query.lower()
        if "transaction" in query_lower and "error" in query_lower:
            return _demo({
                "success": True,
                "data": [{"errorRate": 0.12}],
                "query": query,
            })
        elif "transaction" in query_lower and "duration" in query_lower:
            return _demo({
                "success": True,
                "data": [{"avgDuration": 0.35, "p95": 0.45, "p99": 0.8}],
                "query": query,
            })
        elif "transaction" in query_lower:
            return _demo({
                "success": True,
                "data": [{"count": 15000}],
                "query": query,
            })
        return _demo({
            "success": True,
            "data": [],
            "query": query,
            "note": "offline demo - limited fixture data",
        })

    import httpx

    acct = account_id or NEWRELIC_ACCOUNT_ID
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
    """ % (acct, escaped_query)

    resp = httpx.post(
        _api_url(),
        headers=_headers(),
        json={"query": graphql_query},
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return {"success": False, "error": str(data["errors"]), "query": query}

    results = (
        data.get("data", {})
        .get("actor", {})
        .get("account", {})
        .get("nrql", {})
        .get("results", [])
    )

    return {"success": True, "data": results, "query": query}


@mcp.tool()
def get_error_rate(app_name: str, since: str = "1 hour ago") -> dict:
    """Get error rate for an application.

    Args:
        app_name: Application name.
        since: Time range (NRQL syntax, e.g., '1 hour ago', '30 minutes ago').
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        metrics = _get_fixture_metrics()
        if app_name in metrics:
            series = metrics[app_name].get("error_rate", [])
            latest = series[-1]["value"] if series else 0
            return _demo({
                "app_name": app_name,
                "error_rate": latest,
                "series": series,
                "since": since,
            })
        return _demo({"app_name": app_name, "error_rate": 0, "note": "app not found in fixtures"})

    query = f"""
    SELECT percentage(count(*), WHERE error IS true) as errorRate
    FROM Transaction
    WHERE appName = '{app_name}'
    SINCE {since}
    """
    result = nrql(query)
    if result.get("success") and result.get("data"):
        return {
            "app_name": app_name,
            "error_rate": result["data"][0].get("errorRate", 0),
            "since": since,
        }
    return {"app_name": app_name, "error_rate": None, "error": result.get("error")}


@mcp.tool()
def get_throughput(app_name: str, since: str = "1 hour ago") -> dict:
    """Get throughput (requests per minute) for an application.

    Args:
        app_name: Application name.
        since: Time range.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        metrics = _get_fixture_metrics()
        if app_name in metrics:
            series = metrics[app_name].get("throughput", [])
            latest = series[-1]["value"] if series else 0
            return _demo({
                "app_name": app_name,
                "rpm": latest,
                "series": series,
                "since": since,
            })
        return _demo({"app_name": app_name, "rpm": 0, "note": "app not found in fixtures"})

    query = f"""
    SELECT rate(count(*), 1 minute) as rpm
    FROM Transaction
    WHERE appName = '{app_name}'
    SINCE {since}
    TIMESERIES
    """
    return nrql(query)


@mcp.tool()
def get_latency(app_name: str, since: str = "1 hour ago") -> dict:
    """Get response time percentiles for an application.

    Args:
        app_name: Application name.
        since: Time range.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        metrics = _get_fixture_metrics()
        if app_name in metrics:
            series = metrics[app_name].get("latency_p95", [])
            latest = series[-1]["value"] if series else 0
            return _demo({
                "app_name": app_name,
                "latency_p95_ms": latest,
                "series": series,
                "since": since,
            })
        return _demo({"app_name": app_name, "latency_p95_ms": 0, "note": "app not found in fixtures"})

    query = f"""
    SELECT average(duration) * 1000 as avgMs,
           percentile(duration, 50) * 1000 as p50Ms,
           percentile(duration, 95) * 1000 as p95Ms,
           percentile(duration, 99) * 1000 as p99Ms
    FROM Transaction
    WHERE appName = '{app_name}'
    SINCE {since}
    """
    result = nrql(query)
    if result.get("success") and result.get("data"):
        data = result["data"][0]
        return {
            "app_name": app_name,
            "avg_ms": data.get("avgMs"),
            "p50_ms": data.get("p50Ms"),
            "p95_ms": data.get("p95Ms"),
            "p99_ms": data.get("p99Ms"),
            "since": since,
        }
    return {"app_name": app_name, "error": result.get("error")}


@mcp.tool()
def get_errors(
    app_name: str = "",
    since: str = "1 hour ago",
    limit: int | str = 50,
) -> dict:
    """Get error traces.

    Args:
        app_name: Filter by application (optional).
        since: Time range.
        limit: Max results.
    """
    limit = _as_int(limit, 50)

    if OFFLINE:
        err = _gate()
        if err:
            return err
        return _demo({
            "errors": [
                {
                    "message": "Connection refused: redis-cart:6379",
                    "class": "ECONNREFUSED",
                    "transaction": "WebTransaction/checkout",
                    "app_name": "checkout-svc",
                },
                {
                    "message": "Timeout waiting for connection",
                    "class": "TimeoutError",
                    "transaction": "WebTransaction/checkout",
                    "app_name": "checkout-svc",
                },
            ],
            "count": 2,
            "since": since,
        })

    where = f"WHERE appName = '{app_name}'" if app_name else ""
    query = f"""
    SELECT message, error.class, transactionName, appName
    FROM TransactionError
    {where}
    SINCE {since}
    LIMIT {limit}
    """
    return nrql(query)


@mcp.tool()
def get_slowest_transactions(
    app_name: str,
    since: str = "1 hour ago",
    limit: int | str = 10,
) -> dict:
    """Get slowest transactions for an application.

    Args:
        app_name: Application name.
        since: Time range.
        limit: Max results.
    """
    limit = _as_int(limit, 10)

    if OFFLINE:
        err = _gate()
        if err:
            return err
        return _demo({
            "transactions": [
                {"name": "WebTransaction/checkout", "avgMs": 450, "count": 1200},
                {"name": "WebTransaction/cart/add", "avgMs": 180, "count": 3500},
                {"name": "WebTransaction/products", "avgMs": 120, "count": 8000},
            ],
            "app_name": app_name,
            "since": since,
        })

    query = f"""
    SELECT average(duration) * 1000 as avgMs, count(*) as count
    FROM Transaction
    WHERE appName = '{app_name}'
    FACET name
    SINCE {since}
    LIMIT {limit}
    """
    return nrql(query)


@mcp.tool()
def get_applications() -> list[dict]:
    """Get all APM applications."""
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        return _demo(_get_fixture_apps())

    import httpx

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

    resp = httpx.post(
        _api_url(),
        headers=_headers(),
        json={"query": query},
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return []

    entities = (
        data.get("data", {})
        .get("actor", {})
        .get("entitySearch", {})
        .get("results", {})
        .get("entities", [])
    )

    return [
        {
            "id": e.get("guid", ""),
            "name": e.get("name", ""),
            "language": e.get("language", ""),
            "health_status": e.get("alertSeverity", ""),
            "reporting": e.get("reporting", False),
        }
        for e in entities
    ]


@mcp.tool()
def get_alerts(state: str = "", priority: str = "") -> list[dict]:
    """Get alert incidents.

    Args:
        state: Filter by state (open, closed).
        priority: Filter by priority (critical, high, medium, low).
    """
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        results = _get_fixture_alerts()
        if state:
            state_map = {"open": "ACTIVATED", "closed": "CLOSED"}
            results = [a for a in results if a.get("state") == state_map.get(state, state.upper())]
        if priority:
            results = [a for a in results if a.get("priority", "").lower() == priority.lower()]
        return _demo(results)

    import httpx

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
                            sources {
                                alertPolicyNames
                                alertConditionNames
                            }
                            impactedEntities {
                                name
                            }
                        }
                    }
                }
            }
        }
    }
    """ % (NEWRELIC_ACCOUNT_ID, condition_str)

    resp = httpx.post(
        _api_url(),
        headers=_headers(),
        json={"query": query},
        timeout=_mcp_timeout(),
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return []

    issues = (
        data.get("data", {})
        .get("actor", {})
        .get("account", {})
        .get("aiIssues", {})
        .get("issues", {})
        .get("issues", [])
    )

    return [
        {
            "id": i.get("issueId", ""),
            "title": i.get("title", ""),
            "priority": i.get("priority", ""),
            "state": i.get("state", ""),
            "policy_name": (i.get("sources", [{}])[0].get("alertPolicyNames", [""])[0] if i.get("sources") else ""),
            "condition_name": (i.get("sources", [{}])[0].get("alertConditionNames", [""])[0] if i.get("sources") else ""),
            "entity_name": (i.get("impactedEntities", [{}])[0].get("name", "") if i.get("impactedEntities") else ""),
        }
        for i in issues
    ]


@mcp.tool()
def get_hosts(since: str = "10 minutes ago") -> dict:
    """Get infrastructure hosts.

    Args:
        since: Time range.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        return _demo({
            "hosts": [
                {"hostname": "checkout-prod-1", "cpu": 78.5, "memory": 65.2, "disk": 45.0},
                {"hostname": "checkout-prod-2", "cpu": 82.1, "memory": 68.8, "disk": 44.5},
                {"hostname": "payment-prod-1", "cpu": 45.3, "memory": 52.1, "disk": 38.2},
            ],
            "since": since,
        })

    query = f"""
    SELECT latest(cpuPercent) as cpu, latest(memoryUsedPercent) as memory, latest(diskUsedPercent) as disk
    FROM SystemSample
    FACET hostname
    SINCE {since}
    """
    return nrql(query)


@mcp.tool()
def get_service_health(app_name: str, since: str = "1 hour ago") -> dict:
    """Get comprehensive health metrics for an application.

    Args:
        app_name: Application name.
        since: Time range.

    Returns:
        Combined health metrics including error rate, throughput, and latency.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        metrics = _get_fixture_metrics()
        if app_name not in metrics:
            return {"app_name": app_name, "error": "app not found in fixtures"}

        app_metrics = metrics[app_name]
        return _demo({
            "app_name": app_name,
            "error_rate": app_metrics.get("error_rate", [])[-1]["value"] if app_metrics.get("error_rate") else 0,
            "throughput": app_metrics.get("throughput", [])[-1]["value"] if app_metrics.get("throughput") else 0,
            "latency_p95_ms": app_metrics.get("latency_p95", [])[-1]["value"] if app_metrics.get("latency_p95") else 0,
            "health_status": "CRITICAL" if app_name == "checkout-svc" else "NOT_ALERTING",
            "since": since,
        })

    # Get all metrics in parallel
    error_result = get_error_rate(app_name, since)
    latency_result = get_latency(app_name, since)

    # Find app in list
    apps = get_applications()
    app_info = next((a for a in apps if a["name"] == app_name), None)

    return {
        "app_name": app_name,
        "error_rate": error_result.get("error_rate"),
        "latency_p95_ms": latency_result.get("p95_ms"),
        "health_status": app_info.get("health_status") if app_info else "UNKNOWN",
        "reporting": app_info.get("reporting") if app_info else False,
        "since": since,
    }


@mcp.tool()
def get_deployments(app_name: str, since: str = "7 days ago") -> dict:
    """Get deployment markers for an application.

    Args:
        app_name: Application name.
        since: Time range.
    """
    if OFFLINE:
        err = _gate()
        if err:
            return err
        return _demo({
            "deployments": [
                {
                    "revision": "abc123",
                    "timestamp": "2024-01-15T10:00:00Z",
                    "user": "deploy-bot",
                    "description": "Deploy v2.3.1",
                },
                {
                    "revision": "def456",
                    "timestamp": "2024-01-14T15:30:00Z",
                    "user": "deploy-bot",
                    "description": "Deploy v2.3.0",
                },
            ],
            "app_name": app_name,
            "since": since,
        })

    query = f"""
    SELECT *
    FROM Deployment
    WHERE appName = '{app_name}'
    SINCE {since}
    """
    return nrql(query)


if __name__ == "__main__":
    mcp.run(transport="stdio")
