"""Runbook Automation — automated remediation with approval workflows.

This module provides a runbook system that:
- Defines remediation procedures as executable steps
- Supports manual, auto-approved, and human-approval modes
- Tracks execution history and outcomes
- Integrates with Kubernetes, Terraform, and other tools
"""

from app.runbooks.executor import RunbookExecutor, get_executor
from app.runbooks.models import Runbook, RunbookStep, ExecutionMode, StepType

__all__ = [
    "RunbookExecutor",
    "Runbook",
    "RunbookStep",
    "ExecutionMode",
    "StepType",
    "get_executor",
]
