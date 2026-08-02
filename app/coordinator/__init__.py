"""Advanced DAG-based Task Coordinator.

Features:
- Topological sort with parallel level detection
- Dynamic replanning on failures
- Conditional branching and decision gates
- Resource-aware scheduling
- Checkpoint/resume capability
- Approval workflows
- Integration with real Kubernetes, metrics, and LLM backends
"""

from app.coordinator.dag_executor import (
    DAGExecution,
    DAGExecutor,
    ExecutionState,
    ResourceRequirements,
    RetryPolicy,
    TaskDAG,
    TaskExecution,
    TaskNode,
    TaskPriority,
)
from app.coordinator.replanner import DynamicReplanner, ReplanStrategy
from app.coordinator.resource_pool import ResourceAllocation, ResourcePool
from app.coordinator.decision_integration import (
    DecisionAwareDAGExecutor,
    DecisionGate,
    EnhancedTaskExecution,
    create_decision_aware_executor,
)

__all__ = [
    "DAGExecutor",
    "TaskDAG",
    "TaskNode",
    "DAGExecution",
    "TaskExecution",
    "ExecutionState",
    "TaskPriority",
    "ResourceRequirements",
    "RetryPolicy",
    "DynamicReplanner",
    "ReplanStrategy",
    "ResourcePool",
    "ResourceAllocation",
    "DecisionAwareDAGExecutor",
    "DecisionGate",
    "EnhancedTaskExecution",
    "create_decision_aware_executor",
]
