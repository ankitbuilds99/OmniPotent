"""Onboarding wizard and activation tracking.

Provides:
- Step-by-step guided setup
- Integration configuration
- Team and organization setup
- Progress tracking
- Checklist management
- Activation milestone tracking
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

__all__ = [
    "OnboardingWizard",
    "OnboardingStep",
    "OnboardingProgress",
    "ChecklistItem",
    "ChecklistStatus",
    "get_onboarding_state",
    "update_onboarding_step",
    "complete_onboarding_step",
    "get_onboarding_checklist",
    "create_organization_setup",
    "ActivationMilestone",
    "ActivationState",
    "ActivationTracker",
    "MILESTONE_CONFIG",
]
