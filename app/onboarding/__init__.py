"""Onboarding wizard and activation tracking.

Provides:
- Step-by-step guided setup
- Integration configuration
- Team and organization setup
- Progress tracking
- Checklist management
- Activation milestone tracking
- Demo mode for instant value
- Health scoring and conversion prediction
- Rescue campaigns for stuck users
"""

from app.onboarding.wizard import (
    OnboardingWizard,
    OnboardingStep,
    OnboardingProgress,
    ChecklistItem,
    ChecklistStatus,
    get_onboarding_state,
    update_onboarding_step,
    complete_onboarding_step,
    get_onboarding_checklist,
    create_organization_setup,
)
from app.onboarding.flow import (
    ActivationMilestone,
    ActivationState,
    ActivationTracker,
    MILESTONE_CONFIG,
)
from app.onboarding.health import (
    ActivationCheck,
    ActivationChecklist,
    ActivationCheckStatus,
    OnboardingHealth,
    OnboardingHealthScore,
    get_onboarding_health,
)
from app.onboarding.demo import (
    DemoIncident,
    DemoMode,
    DemoScenario,
    DemoScenarioType,
    DemoTimelineEvent,
    get_demo_mode,
)
from app.onboarding.rescue import (
    RescueAction,
    RescueActionType,
    RescueCampaign,
    RescueEngine,
    RescueRecord,
    RescueStatus,
    RescueTrigger,
    RescueTriggerType,
    get_rescue_engine,
)

__all__ = [
    "ActivationCheck",
    "ActivationChecklist",
    "ActivationCheckStatus",
    "ActivationMilestone",
    "ActivationState",
    "ActivationTracker",
    "ChecklistItem",
    "ChecklistStatus",
    "DemoIncident",
    "DemoMode",
    "DemoScenario",
    "DemoScenarioType",
    "DemoTimelineEvent",
    "MILESTONE_CONFIG",
    "OnboardingHealth",
    "OnboardingHealthScore",
    "OnboardingProgress",
    "OnboardingStep",
    "OnboardingWizard",
    "RescueAction",
    "RescueActionType",
    "RescueCampaign",
    "RescueEngine",
    "RescueRecord",
    "RescueStatus",
    "RescueTrigger",
    "RescueTriggerType",
    "complete_onboarding_step",
    "create_organization_setup",
    "get_demo_mode",
    "get_onboarding_checklist",
    "get_onboarding_health",
    "get_onboarding_state",
    "get_rescue_engine",
    "update_onboarding_step",
]
