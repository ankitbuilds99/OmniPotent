"""Onboarding wizard module.

Provides:
- Step-by-step guided setup
- Integration configuration
- Team and organization setup
- Progress tracking
- Checklist management
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
]
