"""GDPR compliance module.

Provides:
- Data Subject Access Requests (DSAR)
- Data portability and export
- Consent management
- Right to erasure
"""

from app.compliance.gdpr import (
    ConsentType,
    ConsentRecord,
    DSARRequest,
    DSARStatus,
    GDPRManager,
)

__all__ = [
    "ConsentType",
    "ConsentRecord",
    "DSARRequest",
    "DSARStatus",
    "GDPRManager",
]
