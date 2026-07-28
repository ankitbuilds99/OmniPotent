"""Advanced Integration Platform.

Features:
- Cross-integration correlation
- One-click OAuth setup
- Health monitoring and auto-recovery
- Bi-directional sync
- Integration SDK for partners
"""

from app.integrations_advanced.correlation import (
    CrossIntegrationCorrelator,
    CorrelatedEvent,
    CorrelationChain,
)
from app.integrations_advanced.oauth import (
    OAuthProvider,
    OAuthFlow,
    OAuthManager,
)
from app.integrations_advanced.health import (
    IntegrationHealth,
    HealthStatus,
    IntegrationHealthMonitor,
)
from app.integrations_advanced.bidirectional import (
    SyncDirection,
    SyncAction,
    BidirectionalSync,
)
from app.integrations_advanced.sdk import (
    IntegrationSDK,
    IntegrationCapabilities,
    IntegrationManifest,
)

__all__ = [
    "CrossIntegrationCorrelator",
    "CorrelatedEvent",
    "CorrelationChain",
    "OAuthProvider",
    "OAuthFlow",
    "OAuthManager",
    "IntegrationHealth",
    "HealthStatus",
    "IntegrationHealthMonitor",
    "SyncDirection",
    "SyncAction",
    "BidirectionalSync",
    "IntegrationSDK",
    "IntegrationCapabilities",
    "IntegrationManifest",
]
