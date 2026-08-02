"""Policy Engine - Advanced policy evaluation with OPA/Kyverno.

Provides:
- OPA (Open Policy Agent) integration for general policies
- Kyverno integration for Kubernetes policies
- Change window management
- Entry/exit criteria evaluation
- Policy versioning and audit
"""

from app.policy_engine.engine import (
    PolicyEngine,
    PolicyEvaluationResult,
    PolicyViolation,
    PolicyDecision,
    create_policy_engine,
)
from app.policy_engine.opa_client import (
    OPAClient,
    OPAPolicy,
    OPAInput,
    OPAResult,
)
from app.policy_engine.kyverno_client import (
    KyvernoClient,
    KyvernoPolicy,
    KyvernoResult,
)
from app.policy_engine.change_windows import (
    ChangeWindowManager,
    ChangeWindow,
    WindowType,
    WindowCheckResult,
    RecurrencePattern,
    create_change_window_manager,
)
from app.policy_engine.criteria import (
    CriteriaEvaluator,
    EntryCriteria,
    ExitCriteria,
    CriteriaResult,
    Criterion,
    CriteriaType,
    CriteriaOperator,
    create_criteria_evaluator,
)

__all__ = [
    "PolicyEngine",
    "PolicyEvaluationResult",
    "PolicyViolation",
    "PolicyDecision",
    "create_policy_engine",
    "OPAClient",
    "OPAPolicy",
    "OPAInput",
    "OPAResult",
    "KyvernoClient",
    "KyvernoPolicy",
    "KyvernoResult",
    "ChangeWindowManager",
    "ChangeWindow",
    "WindowType",
    "CriteriaEvaluator",
    "EntryCriteria",
    "ExitCriteria",
    "CriteriaResult",
    "Criterion",
    "CriteriaType",
    "CriteriaOperator",
    "WindowCheckResult",
    "RecurrencePattern",
    "create_change_window_manager",
    "create_criteria_evaluator",
]
