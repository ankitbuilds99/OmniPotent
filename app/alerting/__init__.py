"""Alerting Rules Engine — SLO-based alerts and anomaly detection.

This module provides a flexible alerting system that:
- Defines alert rules with thresholds and conditions
- Evaluates metrics against SLO targets
- Detects anomalies using statistical methods
- Triggers notifications via PagerDuty, Slack, etc.
"""

from app.alerting.engine import AlertEngine, get_engine
from app.alerting.rules import AlertRule, AlertSeverity, AlertCondition

__all__ = [
    "AlertEngine",
    "AlertRule",
    "AlertSeverity",
    "AlertCondition",
    "get_engine",
]
