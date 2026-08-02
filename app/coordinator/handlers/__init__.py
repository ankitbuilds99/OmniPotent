"""Task Handlers for DAG Execution.

Real integrations with:
- Kubernetes
- Prometheus/Metrics
- Logs (Loki/Elasticsearch)
- Git/GitHub
- LLM for analysis
"""

from app.coordinator.handlers.investigation import (
    CorrelationHandler,
    DeploymentsHandler,
    LogsHandler,
    MetricsHandler,
    create_investigation_handlers,
)
from app.coordinator.handlers.remediation import (
    ConfigUpdateHandler,
    RestartHandler,
    RollbackHandler,
    ScaleHandler,
    create_remediation_handlers,
)

__all__ = [
    "create_investigation_handlers",
    "create_remediation_handlers",
    "LogsHandler",
    "MetricsHandler",
    "DeploymentsHandler",
    "CorrelationHandler",
    "ScaleHandler",
    "RestartHandler",
    "RollbackHandler",
    "ConfigUpdateHandler",
]
