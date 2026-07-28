"""Compliance module with SOC2, GDPR, and multi-framework support.

Provides:
- GDPR: Data Subject Access Requests, Consent, Erasure
- SOC2: Automated evidence collection, Control monitoring
- Compliance-as-Code controls
- Real-time compliance dashboard
- Customer Trust Portal
- Pre-emptive violation alerts
- Multi-framework mapping (SOC2, ISO27001, HIPAA)
"""

from app.compliance.gdpr import (
    ConsentType,
    ConsentRecord,
    DSARRequest,
    DSARStatus,
    GDPRManager,
)
from app.compliance.controls import (
    Control,
    ControlCategory,
    ControlStatus,
    ControlEvidence,
    ControlFramework,
    ComplianceControl,
    get_control_registry,
)
from app.compliance.evidence import (
    EvidenceType,
    EvidenceCollector,
    AutomatedEvidence,
    EvidenceSnapshot,
    get_evidence_collector,
)
from app.compliance.dashboard import (
    ComplianceScore,
    ComplianceDashboard,
    ControlStatusSummary,
    ViolationAlert,
    get_compliance_dashboard,
)
from app.compliance.trust_portal import (
    TrustDocument,
    DocumentAccess,
    TrustPortal,
    QuestionnaireAnswer,
    get_trust_portal,
)

__all__ = [
    # GDPR
    "ConsentType",
    "ConsentRecord",
    "DSARRequest",
    "DSARStatus",
    "GDPRManager",
    # Controls
    "Control",
    "ControlCategory",
    "ControlStatus",
    "ControlEvidence",
    "ControlFramework",
    "ComplianceControl",
    "get_control_registry",
    # Evidence
    "EvidenceType",
    "EvidenceCollector",
    "AutomatedEvidence",
    "EvidenceSnapshot",
    "get_evidence_collector",
    # Dashboard
    "ComplianceScore",
    "ComplianceDashboard",
    "ControlStatusSummary",
    "ViolationAlert",
    "get_compliance_dashboard",
    # Trust Portal
    "TrustDocument",
    "DocumentAccess",
    "TrustPortal",
    "QuestionnaireAnswer",
    "get_trust_portal",
]
