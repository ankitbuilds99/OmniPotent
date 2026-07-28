"""Advanced Status Page module.

Provides:
- Component status monitoring
- AI-generated status updates
- Personalized customer views
- SLA tracking with burn-down
- Predictive status
- Embeddable widgets
- Multi-channel sync
"""

from app.status_page.components import (
    Component,
    ComponentStatus,
    ComponentGroup,
    StatusRegistry,
    get_status_registry,
)
from app.status_page.incidents import (
    StatusIncident,
    IncidentUpdate,
    IncidentImpact,
    IncidentManager,
    get_incident_manager,
)
from app.status_page.sla import (
    SLATarget,
    SLAStatus,
    SLATracker,
    get_sla_tracker,
)
from app.status_page.updates import (
    AIUpdateGenerator,
    StatusUpdate,
    UpdateTone,
    get_update_generator,
)
from app.status_page.widgets import (
    WidgetConfig,
    WidgetTheme,
    WidgetGenerator,
    get_widget_generator,
)

__all__ = [
    # Components
    "Component",
    "ComponentStatus",
    "ComponentGroup",
    "StatusRegistry",
    "get_status_registry",
    # Incidents
    "StatusIncident",
    "IncidentUpdate",
    "IncidentImpact",
    "IncidentManager",
    "get_incident_manager",
    # SLA
    "SLATarget",
    "SLAStatus",
    "SLATracker",
    "get_sla_tracker",
    # Updates
    "AIUpdateGenerator",
    "StatusUpdate",
    "UpdateTone",
    "get_update_generator",
    # Widgets
    "WidgetConfig",
    "WidgetTheme",
    "WidgetGenerator",
    "get_widget_generator",
]
