"""GraphQL API for OmniPotent.

Provides a GraphQL interface for:
- Investigations (queries, mutations, subscriptions)
- Incidents and alerts
- Services and dependencies
- Metrics and observability data
- Configuration management
"""

from app.api.graphql.schema import schema, graphql_app
from app.api.graphql.types import (
    Investigation,
    Incident,
    Service,
    Metric,
    Alert,
)

__all__ = [
    "schema",
    "graphql_app",
    "Investigation",
    "Incident",
    "Service",
    "Metric",
    "Alert",
]
