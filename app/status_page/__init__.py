"""Advanced Status Page module.

Provides:
- Component status monitoring
- Incident management with timeline
- Scheduled maintenance windows
- Subscriber notifications (email/webhook)
- Uptime metrics (30d, 90d)
- AI-generated status updates
- Personalized customer views
- SLA tracking with burn-down
- Predictive status
- Embeddable widgets
- Multi-channel sync
"""

# New production-ready status page models and store
from app.status_page.models import (
    Component as StatusComponent,
    ComponentStatus as StatusComponentStatus,
    Incident,
    IncidentStatus,
    IncidentSeverity,
    IncidentUpdate as StatusIncidentUpdate,
    Maintenance,
    MaintenanceStatus,
    Subscriber,
    SubscriptionType,
)
from app.status_page.store import (
    StatusStore,
    InMemoryStatusStore,
    LiveStatusStore,
    get_status_store,
    get_live_status_store,
    set_status_store,
    start_live_status_polling,
    stop_live_status_polling,
)
from app.status_page.notifications import (
    StatusNotifier,
    NotificationPayload,
    get_status_notifier,
)
from app.status_page.api_router import router as status_router

# Legacy components (kept for backwards compatibility)
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
    # New status page API
    "status_router",
    "StatusComponent",
    "StatusComponentStatus",
    "Incident",
    "IncidentStatus",
    "IncidentSeverity",
    "StatusIncidentUpdate",
    "Maintenance",
    "MaintenanceStatus",
    "Subscriber",
    "SubscriptionType",
    "StatusStore",
    "InMemoryStatusStore",
    "LiveStatusStore",
    "get_status_store",
    "get_live_status_store",
    "set_status_store",
    "start_live_status_polling",
    "stop_live_status_polling",
    "StatusNotifier",
    "NotificationPayload",
    "get_status_notifier",
    # Legacy Components
    "Component",
    "ComponentStatus",
    "ComponentGroup",
    "StatusRegistry",
    "get_status_registry",
    # Legacy Incidents
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
