"""Alert Correlation - Intelligent alert grouping and deduplication."""

from app.alert_correlation.correlator import (
    AlertSeverity,
    CorrelationType,
    Alert,
    AlertGroup,
    CorrelationRule,
    AlertCorrelator,
    create_alert_correlator,
)

__all__ = [
    "AlertSeverity",
    "CorrelationType",
    "Alert",
    "AlertGroup",
    "CorrelationRule",
    "AlertCorrelator",
    "create_alert_correlator",
]
