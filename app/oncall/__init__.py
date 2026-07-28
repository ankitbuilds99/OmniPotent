"""Advanced On-call scheduling system.

Provides:
- Rotation schedules (daily, weekly, custom)
- Override management
- Escalation policies
- On-call notifications
- Calendar integration
- AI-optimized schedule generation
- Fatigue detection and prevention
- Skills-based incident routing
- Compensation tracking
- Coverage swap marketplace
"""

from app.oncall.scheduler import (
    OnCallScheduler,
    Schedule,
    Rotation,
    RotationType,
    Override,
    EscalationPolicy,
    EscalationLevel,
    OnCallUser,
    create_schedule,
    get_schedule,
    list_schedules,
    get_current_oncall,
    create_override,
    get_escalation_chain,
    notify_oncall,
)
from app.oncall.fatigue import (
    FatigueScore,
    FatigueAlert,
    FatigueDetector,
    get_fatigue_detector,
)
from app.oncall.smart_routing import (
    IncidentRouting,
    RoutingDecision,
    ExpertiseMatch,
    SmartRouter,
    get_smart_router,
)
from app.oncall.compensation import (
    OnCallHours,
    CompensationPolicy,
    CompensationTracker,
    get_compensation_tracker,
)
from app.oncall.swap_marketplace import (
    SwapRequest,
    SwapOffer,
    SwapMarketplace,
    get_swap_marketplace,
)

__all__ = [
    # Base scheduler
    "OnCallScheduler",
    "Schedule",
    "Rotation",
    "RotationType",
    "Override",
    "EscalationPolicy",
    "EscalationLevel",
    "OnCallUser",
    "create_schedule",
    "get_schedule",
    "list_schedules",
    "get_current_oncall",
    "create_override",
    "get_escalation_chain",
    "notify_oncall",
    # Fatigue detection
    "FatigueScore",
    "FatigueAlert",
    "FatigueDetector",
    "get_fatigue_detector",
    # Smart routing
    "IncidentRouting",
    "RoutingDecision",
    "ExpertiseMatch",
    "SmartRouter",
    "get_smart_router",
    # Compensation
    "OnCallHours",
    "CompensationPolicy",
    "CompensationTracker",
    "get_compensation_tracker",
    # Swap marketplace
    "SwapRequest",
    "SwapOffer",
    "SwapMarketplace",
    "get_swap_marketplace",
]
