"""VPC Connector - Zero Data Exfiltration Architecture.

This is deployed INSIDE the customer's VPC/network.
Code, logs, and metrics NEVER leave the customer environment.
Only REDACTED evidence snippets cross the boundary.

Components:
- RedactionEngine: Scrubs PII, secrets, IPs before transmission
- DataCollectors: Logs, metrics, code, Kubernetes state
- VPCConnector: Main connector orchestrating data collection
"""

from app.vpc_connector.redaction import RedactionEngine, RedactionRule
from app.vpc_connector.collectors import (
    DataCollector,
    LogCollector,
    MetricsCollector,
    CodeCollector,
    KubernetesCollector,
)
from app.vpc_connector.connector import (
    VPCConnector,
    ConnectorConfig,
    EvidencePackage,
    ConnectorStatus,
)

__all__ = [
    "RedactionEngine",
    "RedactionRule",
    "DataCollector",
    "LogCollector",
    "MetricsCollector",
    "CodeCollector",
    "KubernetesCollector",
    "TracesCollector",
    "VPCConnector",
    "ConnectorConfig",
    "EvidencePackage",
    "ConnectorStatus",
]
