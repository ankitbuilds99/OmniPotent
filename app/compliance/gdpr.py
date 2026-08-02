"""GDPR compliance implementation.

Features:
- Data Subject Access Requests (DSAR)
- Data export and portability
- Consent management
- Right to erasure (deletion)
- Audit logging
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol

log = logging.getLogger("omnipotent.compliance.gdpr")


class ConsentType(str, Enum):
    ESSENTIAL = "essential"
    ANALYTICS = "analytics"
    MARKETING = "marketing"
    PERSONALIZATION = "personalization"
    THIRD_PARTY = "third_party"


class DSARStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"


class DSARType(str, Enum):
    ACCESS = "access"
    RECTIFICATION = "rectification"
    ERASURE = "erasure"
    PORTABILITY = "portability"
    RESTRICTION = "restriction"
    OBJECTION = "objection"


DSAR_DEADLINE_DAYS = 30


@dataclass
class ConsentRecord:
    """Record of user consent."""

    user_id: str
    consent_type: ConsentType
    granted: bool
    version: str = "1.0"

    ip_address: str = ""
    user_agent: str = ""

    granted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    @property
    def is_valid(self) -> bool:
        if not self.granted:
            return False
        if self.expires_at and datetime.now(timezone.utc) > self.expires_at:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "consent_type": self.consent_type.value,
            "granted": self.granted,
            "version": self.version,
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_valid": self.is_valid,
        }


@dataclass
class DSARRequest:
    """Data Subject Access Request."""

    id: str
    user_id: str
    request_type: DSARType
    status: DSARStatus = DSARStatus.PENDING

    details: str = ""
    requester_email: str = ""
    requester_verified: bool = False

    data_export_url: str | None = None
    rejection_reason: str | None = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    deadline: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=DSAR_DEADLINE_DAYS)
    )
    completed_at: datetime | None = None

    @property
    def days_remaining(self) -> int:
        delta = self.deadline - datetime.now(timezone.utc)
        return max(0, delta.days)

    @property
    def is_overdue(self) -> bool:
        return (
            self.status not in (DSARStatus.COMPLETED, DSARStatus.REJECTED)
            and datetime.now(timezone.utc) > self.deadline
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "request_type": self.request_type.value,
            "status": self.status.value,
            "days_remaining": self.days_remaining,
            "is_overdue": self.is_overdue,
            "created_at": self.created_at.isoformat(),
            "deadline": self.deadline.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class ComplianceStore(Protocol):
    """Protocol for compliance data storage."""

    async def get_consent(
        self, user_id: str, consent_type: ConsentType
    ) -> ConsentRecord | None: ...
    async def save_consent(self, record: ConsentRecord) -> None: ...
    async def get_all_consents(self, user_id: str) -> list[ConsentRecord]: ...

    async def create_dsar(self, request: DSARRequest) -> str: ...
    async def get_dsar(self, request_id: str) -> DSARRequest | None: ...
    async def update_dsar(self, request: DSARRequest) -> None: ...
    async def list_pending_dsars(self) -> list[DSARRequest]: ...

    async def get_user_data(self, user_id: str) -> dict[str, Any]: ...
    async def delete_user_data(self, user_id: str, retain_audit: bool = True) -> None: ...

    async def log_audit_event(
        self, user_id: str, action: str, details: dict[str, Any]
    ) -> None: ...


class GDPRManager:
    """Manages GDPR compliance operations."""

    def __init__(self, store: ComplianceStore):
        self._store = store

    async def record_consent(
        self,
        user_id: str,
        consent_type: ConsentType,
        granted: bool,
        ip_address: str = "",
        user_agent: str = "",
        version: str = "1.0",
    ) -> ConsentRecord:
        """Record user consent decision."""
        record = ConsentRecord(
            user_id=user_id,
            consent_type=consent_type,
            granted=granted,
            version=version,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        await self._store.save_consent(record)
        await self._store.log_audit_event(
            user_id,
            "consent_recorded",
            {
                "consent_type": consent_type.value,
                "granted": granted,
                "version": version,
            },
        )

        log.info(
            "Consent recorded: user=%s type=%s granted=%s",
            user_id,
            consent_type.value,
            granted,
        )
        return record

    async def check_consent(
        self,
        user_id: str,
        consent_type: ConsentType,
    ) -> bool:
        """Check if user has valid consent for a type."""
        if consent_type == ConsentType.ESSENTIAL:
            return True

        record = await self._store.get_consent(user_id, consent_type)
        return record is not None and record.is_valid

    async def get_consent_status(
        self,
        user_id: str,
    ) -> dict[str, Any]:
        """Get all consent statuses for a user."""
        consents = await self._store.get_all_consents(user_id)
        consent_map = {c.consent_type: c for c in consents}

        return {
            consent_type.value: {
                "granted": (
                    consent_map[consent_type].granted
                    if consent_type in consent_map
                    else False
                ),
                "is_valid": (
                    consent_map[consent_type].is_valid
                    if consent_type in consent_map
                    else False
                ),
            }
            for consent_type in ConsentType
        }

    async def create_dsar(
        self,
        user_id: str,
        request_type: DSARType,
        requester_email: str,
        details: str = "",
    ) -> DSARRequest:
        """Create a new Data Subject Access Request."""
        now = datetime.now(timezone.utc)
        request_id = f"dsar_{int(now.timestamp())}_{user_id[:8]}"

        request = DSARRequest(
            id=request_id,
            user_id=user_id,
            request_type=request_type,
            requester_email=requester_email,
            details=details,
        )

        await self._store.create_dsar(request)
        await self._store.log_audit_event(
            user_id,
            "dsar_created",
            {"request_id": request_id, "type": request_type.value},
        )

        log.info(
            "DSAR created: id=%s user=%s type=%s deadline=%s",
            request_id,
            user_id,
            request_type.value,
            request.deadline.isoformat(),
        )
        return request

    async def process_access_request(
        self,
        request_id: str,
    ) -> dict[str, Any]:
        """Process a data access request."""
        request = await self._store.get_dsar(request_id)
        if not request:
            raise ValueError(f"DSAR not found: {request_id}")

        if request.request_type != DSARType.ACCESS:
            raise ValueError(f"Not an access request: {request.request_type}")

        request.status = DSARStatus.IN_PROGRESS
        await self._store.update_dsar(request)

        user_data = await self._store.get_user_data(request.user_id)

        export_data = self._prepare_export(user_data)

        request.status = DSARStatus.COMPLETED
        request.completed_at = datetime.now(timezone.utc)
        await self._store.update_dsar(request)

        await self._store.log_audit_event(
            request.user_id,
            "dsar_completed",
            {"request_id": request_id, "type": "access"},
        )

        log.info(
            "Access request completed: id=%s user=%s",
            request_id,
            request.user_id,
        )
        return export_data

    def _prepare_export(self, user_data: dict[str, Any]) -> dict[str, Any]:
        """Prepare user data for export."""
        sensitive_fields = ["password_hash", "api_secret", "stripe_customer_id"]

        def redact(data: Any) -> Any:
            if isinstance(data, dict):
                return {
                    k: "[REDACTED]" if k in sensitive_fields else redact(v)
                    for k, v in data.items()
                }
            elif isinstance(data, list):
                return [redact(item) for item in data]
            return data

        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "format_version": "1.0",
            "data": redact(user_data),
        }

    async def process_erasure_request(
        self,
        request_id: str,
        retain_audit: bool = True,
    ) -> bool:
        """Process a right to erasure (deletion) request."""
        request = await self._store.get_dsar(request_id)
        if not request:
            raise ValueError(f"DSAR not found: {request_id}")

        if request.request_type != DSARType.ERASURE:
            raise ValueError(f"Not an erasure request: {request.request_type}")

        request.status = DSARStatus.IN_PROGRESS
        await self._store.update_dsar(request)

        await self._store.delete_user_data(request.user_id, retain_audit=retain_audit)

        request.status = DSARStatus.COMPLETED
        request.completed_at = datetime.now(timezone.utc)
        await self._store.update_dsar(request)

        await self._store.log_audit_event(
            request.user_id,
            "dsar_completed",
            {"request_id": request_id, "type": "erasure", "audit_retained": retain_audit},
        )

        log.info(
            "Erasure request completed: id=%s user=%s audit_retained=%s",
            request_id,
            request.user_id,
            retain_audit,
        )
        return True

    async def export_user_data(
        self,
        user_id: str,
        format: str = "json",
    ) -> str:
        """Export user data for portability."""
        user_data = await self._store.get_user_data(user_id)
        export_data = self._prepare_export(user_data)

        await self._store.log_audit_event(
            user_id,
            "data_exported",
            {"format": format},
        )

        if format == "json":
            return json.dumps(export_data, indent=2)
        else:
            raise ValueError(f"Unsupported format: {format}")

    async def get_pending_requests(self) -> list[DSARRequest]:
        """Get all pending DSARs."""
        return await self._store.list_pending_dsars()

    async def get_overdue_requests(self) -> list[DSARRequest]:
        """Get overdue DSARs requiring immediate attention."""
        pending = await self._store.list_pending_dsars()
        return [r for r in pending if r.is_overdue]

    async def get_compliance_dashboard(self) -> dict[str, Any]:
        """Get compliance metrics for dashboard."""
        pending = await self._store.list_pending_dsars()

        by_type: dict[str, int] = {}
        overdue_count = 0
        avg_days_remaining = 0.0

        for request in pending:
            by_type[request.request_type.value] = (
                by_type.get(request.request_type.value, 0) + 1
            )
            if request.is_overdue:
                overdue_count += 1

        if pending:
            avg_days_remaining = sum(r.days_remaining for r in pending) / len(pending)

        return {
            "pending_requests": len(pending),
            "overdue_requests": overdue_count,
            "requests_by_type": by_type,
            "avg_days_remaining": avg_days_remaining,
            "compliance_rate": (
                (len(pending) - overdue_count) / len(pending) if pending else 1.0
            ),
        }
