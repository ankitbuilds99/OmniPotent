"""Advanced On-call Management System.

Enterprise-grade on-call scheduling and incident management:

Core Features:
- Rotation schedules (daily, weekly, biweekly, custom)
- Override management
- Escalation policies with multi-level support
- On-call notifications (push, SMS, phone, email, Slack)
- Calendar integration
- Holiday handling
- Time-off auto-routing

AI Features:
- Smart alert routing based on skills and context
- AI-powered coverage finder
- Alert fatigue detection
- Workload balancing recommendations
- Optimal schedule generation

Advanced Features:
- Shift swap marketplace
- Compensation tracking with multipliers
- Shadow rotations for training
- Follow-the-sun scheduling
- Gap detection
"""

# Base scheduler (legacy compatibility)
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

# Fatigue detection
from app.oncall.fatigue import (
    FatigueScore,
    FatigueAlert,
    FatigueDetector,
    get_fatigue_detector,
)

# Smart routing
from app.oncall.smart_routing import (
    IncidentRouting,
    RoutingDecision,
    ExpertiseMatch,
    SmartRouter,
    get_smart_router,
)

# Compensation
from app.oncall.compensation import (
    OnCallHours,
    CompensationPolicy,
    CompensationTracker,
    get_compensation_tracker,
)

# Swap marketplace
from app.oncall.swap_marketplace import (
    SwapRequest,
    SwapOffer,
    SwapMarketplace,
    get_swap_marketplace,
)

# New enterprise services
from app.oncall.schedule_service import (
    ScheduleService,
    get_schedule_service,
)

from app.oncall.escalation_service import (
    EscalationService,
    get_escalation_service,
)

from app.oncall.ai_oncall_service import (
    AIOncallService,
    get_ai_oncall_service,
)

# API router
from app.oncall.api_router import router as oncall_router

__all__ = [
    # Base scheduler (legacy)
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
    # Enterprise services
    "ScheduleService",
    "get_schedule_service",
    "EscalationService",
    "get_escalation_service",
    "AIOncallService",
    "get_ai_oncall_service",
    # API router
    "oncall_router",
]
