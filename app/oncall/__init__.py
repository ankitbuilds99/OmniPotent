"""On-call scheduling system.

Provides:
- Rotation schedules (daily, weekly, custom)
- Override management
- Escalation policies
- On-call notifications
- Calendar integration
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

__all__ = [
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
]
