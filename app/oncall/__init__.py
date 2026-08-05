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

# Rotation templates (PagerDuty-level)
from app.oncall.rotations import (
    RotationTemplate,
    RotationPatternType,
    FollowTheSunRegion,
    RotationLayer,
    TimeRestriction,
    HandoffDay,
    create_weekly_rotation,
    create_biweekly_rotation,
    create_follow_the_sun_rotation,
    create_business_hours_rotation,
    create_weekend_rotation,
    create_custom_rotation,
    create_layered_rotation,
)

# Enhanced schedules
from app.oncall.schedules import (
    OverrideReason,
    GapSeverity,
    ScheduleGap,
    ScheduleOverride,
    ScheduleShift,
    TimezoneManager,
    GapDetector,
    CalendarExporter,
    ConflictDetector,
    ScheduleAnalyzer,
)

# Enhanced escalations
from app.oncall.escalations import (
    TargetType,
    EscalationUrgency,
    NotificationChannel,
    EscalationTarget,
    EscalationStep,
    TimeBasedRule,
    MatchCondition,
    EscalationPolicy as EnhancedEscalationPolicy,
    EscalationState,
    EscalationPolicyBuilder,
    create_simple_policy,
    create_critical_alerts_policy,
    create_business_hours_policy,
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
    # Rotation templates
    "RotationTemplate",
    "RotationPatternType",
    "FollowTheSunRegion",
    "RotationLayer",
    "TimeRestriction",
    "HandoffDay",
    "create_weekly_rotation",
    "create_biweekly_rotation",
    "create_follow_the_sun_rotation",
    "create_business_hours_rotation",
    "create_weekend_rotation",
    "create_custom_rotation",
    "create_layered_rotation",
    # Enhanced schedules
    "OverrideReason",
    "GapSeverity",
    "ScheduleGap",
    "ScheduleOverride",
    "ScheduleShift",
    "TimezoneManager",
    "GapDetector",
    "CalendarExporter",
    "ConflictDetector",
    "ScheduleAnalyzer",
    # Enhanced escalations
    "TargetType",
    "EscalationUrgency",
    "NotificationChannel",
    "EscalationTarget",
    "EscalationStep",
    "TimeBasedRule",
    "MatchCondition",
    "EnhancedEscalationPolicy",
    "EscalationState",
    "EscalationPolicyBuilder",
    "create_simple_policy",
    "create_critical_alerts_policy",
    "create_business_hours_policy",
    # API router
    "oncall_router",
]
