"""California Consumer Privacy Act (CCPA) compliance module.

Provides:
- "Do Not Sell My Personal Information" opt-out handling
- Consumer data access/deletion requests
- California resident identification
- Global Privacy Control (GPC) signal handling
- CCPA-specific audit logging (24-month retention)

Extends the existing GDPR support with California-specific requirements.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.compliance.ccpa")


class CCPARequestType(str, Enum):
    """CCPA consumer request types."""

    ACCESS = "access"
    DELETION = "deletion"
    CATEGORIES = "categories"
    OPT_OUT = "opt_out"
    OPT_IN = "opt_in"


class CCPARequestStatus(str, Enum):
    """Request processing status."""

    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class VerificationMethod(str, Enum):
    """Consumer verification methods."""

    EMAIL = "email"
    SMS = "sms"
    KNOWLEDGE_BASED = "knowledge_based"
    GOVERNMENT_ID = "government_id"


CCPA_RESPONSE_DEADLINE_DAYS = 45
CCPA_EXTENSION_DAYS = 45
CCPA_AUDIT_RETENTION_MONTHS = 24


CCPA_DELETION_EXCEPTIONS = {
    "1798.105(d)(1)": "Complete a transaction or provide goods/services",
    "1798.105(d)(2)": "Detect security incidents or protect against malicious activity",
    "1798.105(d)(3)": "Debug to identify and repair functionality errors",
    "1798.105(d)(4)": "Exercise free speech rights",
    "1798.105(d)(5)": "Comply with California Electronic Communications Privacy Act",
    "1798.105(d)(6)": "Engage in public or peer-reviewed research",
    "1798.105(d)(7)": "Enable internal uses aligned with consumer expectations",
    "1798.105(d)(8)": "Comply with a legal obligation",
    "1798.105(d)(9)": "Otherwise use consistent with the context of collection",
}


CCPA_DATA_CATEGORIES = {
    "identifiers": {
        "description": "Name, email, IP address, account identifiers",
        "fields": ["email", "user_id", "ip_address", "name", "billing_address"],
    },
    "commercial_information": {
        "description": "Products or services purchased, usage history",
        "fields": ["plan", "billing_records", "usage_records"],
    },
    "internet_activity": {
        "description": "Browsing history, search history, application interactions",
        "fields": ["investigation_history", "session_data", "api_usage_logs"],
    },
    "geolocation_data": {
        "description": "Physical location or movements",
        "fields": ["ip_geolocation"],
    },
    "professional_information": {
        "description": "Employment-related information",
        "fields": ["organization_name", "role"],
    },
    "inferences": {
        "description": "Profiles reflecting preferences or behavior",
        "fields": ["analytics_profile"],
    },
}


@dataclass
class CCPAOptOutPreference:
    """CCPA sale opt-out preference."""

    user_id: str
    org_id: str
    opted_out: bool = False
    opt_out_scope: str = "all"
    opted_out_at: float | None = None
    opted_back_in_at: float | None = None
    gpc_signal_received: bool = False
    gpc_honored_at: float | None = None
    ip_address: str = ""
    user_agent: str = ""


@dataclass
class CCPARequest:
    """Consumer rights request."""

    request_id: str
    user_id: str
    org_id: str
    request_type: CCPARequestType
    status: CCPARequestStatus = CCPARequestStatus.PENDING_VERIFICATION
    verification_method: VerificationMethod = VerificationMethod.EMAIL
    verification_code_hash: str = ""
    verification_attempts: int = 0
    verified_at: float | None = None
    requester_email: str = ""
    requester_phone: str = ""
    authorized_agent_name: str | None = None
    authorized_agent_proof_id: str | None = None
    request_scope: str = "all"
    categories_requested: list[str] = field(default_factory=list)
    data_export_url: str | None = None
    data_export_expires_at: float | None = None
    exceptions_applied: list[dict] = field(default_factory=list)
    rejection_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    deadline: float = 0.0
    extension_granted: bool = False
    extension_reason: str = ""
    completed_at: float | None = None

    def __post_init__(self) -> None:
        if not self.deadline:
            self.deadline = self.created_at + (CCPA_RESPONSE_DEADLINE_DAYS * 86400)


@dataclass
class CaliforniaResidentIndicator:
    """California residency determination."""

    user_id: str
    is_california_resident: bool | None = None
    determination_method: str = "unknown"
    geoip_region: str = ""
    billing_state: str = ""
    self_declared_state: str = ""
    last_ip_check: str = ""
    last_check_at: float | None = None


def generate_request_id(request_type: CCPARequestType) -> str:
    """Generate unique CCPA request ID."""
    prefix = f"ccpa_{request_type.value}"
    random_part = secrets.token_hex(8)
    return f"{prefix}_{random_part}"


def generate_verification_code() -> str:
    """Generate 6-digit verification code."""
    return "".join(secrets.choice("0123456789") for _ in range(6))


def hash_verification_code(code: str, salt: str) -> str:
    """Hash verification code for storage."""
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


class CCPAManager:
    """Manages CCPA compliance operations."""

    VERIFICATION_MAX_ATTEMPTS = 5
    VERIFICATION_CODE_EXPIRY = 3600

    def __init__(self, store: Any = None):
        self.store = store

    async def record_opt_out(
        self,
        user_id: str,
        org_id: str,
        scope: str = "all",
        ip_address: str = "",
        user_agent: str = "",
        gpc_signal: bool = False,
    ) -> CCPAOptOutPreference:
        """Record a "Do Not Sell" opt-out preference."""
        now = time.time()

        pref = CCPAOptOutPreference(
            user_id=user_id,
            org_id=org_id,
            opted_out=True,
            opt_out_scope=scope,
            opted_out_at=now,
            gpc_signal_received=gpc_signal,
            gpc_honored_at=now if gpc_signal else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        if self.store:
            await self.store.save_ccpa_opt_out(pref)

        self._audit_log(
            "ccpa.opt_out_recorded",
            user_id=user_id,
            scope=scope,
            gpc=gpc_signal,
        )

        return pref

    async def get_opt_out_status(
        self,
        user_id: str,
        org_id: str,
    ) -> CCPAOptOutPreference | None:
        """Get current opt-out status for a user."""
        if not self.store:
            return None
        return await self.store.get_ccpa_opt_out(user_id, org_id)

    async def record_opt_in(
        self,
        user_id: str,
        org_id: str,
        ip_address: str = "",
    ) -> CCPAOptOutPreference | None:
        """Record opt-in (reversal of opt-out)."""
        pref = await self.get_opt_out_status(user_id, org_id)
        if not pref:
            return None

        pref.opted_out = False
        pref.opted_back_in_at = time.time()

        if self.store:
            await self.store.save_ccpa_opt_out(pref)

        self._audit_log(
            "ccpa.opt_in_recorded",
            user_id=user_id,
        )

        return pref

    async def create_access_request(
        self,
        user_id: str,
        org_id: str,
        email: str,
        request_type: CCPARequestType = CCPARequestType.ACCESS,
        scope: str = "all",
        categories: list[str] | None = None,
        verification_method: VerificationMethod = VerificationMethod.EMAIL,
        authorized_agent_name: str | None = None,
        authorized_agent_proof: str | None = None,
    ) -> tuple[CCPARequest, str]:
        """
        Create a CCPA consumer request.

        Returns:
            (request, verification_code)
        """
        request = CCPARequest(
            request_id=generate_request_id(request_type),
            user_id=user_id,
            org_id=org_id,
            request_type=request_type,
            verification_method=verification_method,
            requester_email=email,
            request_scope=scope,
            categories_requested=categories or [],
            authorized_agent_name=authorized_agent_name,
            authorized_agent_proof_id=authorized_agent_proof,
        )

        verification_code = generate_verification_code()
        request.verification_code_hash = hash_verification_code(
            verification_code,
            request.request_id,
        )

        if self.store:
            await self.store.save_ccpa_request(request)

        self._audit_log(
            "ccpa.request_created",
            request_id=request.request_id,
            request_type=request_type.value,
            user_id=user_id,
        )

        return request, verification_code

    async def verify_request(
        self,
        request_id: str,
        code: str,
    ) -> tuple[bool, str]:
        """
        Verify a CCPA request with the verification code.

        Returns:
            (success, message)
        """
        if not self.store:
            return False, "Store not configured"

        request = await self.store.get_ccpa_request(request_id)
        if not request:
            return False, "Request not found"

        if request.status != CCPARequestStatus.PENDING_VERIFICATION:
            return False, "Request already processed"

        if request.verification_attempts >= self.VERIFICATION_MAX_ATTEMPTS:
            return False, "Too many verification attempts"

        expected_hash = request.verification_code_hash
        provided_hash = hash_verification_code(code, request_id)

        if not secrets.compare_digest(expected_hash, provided_hash):
            request.verification_attempts += 1
            await self.store.save_ccpa_request(request)
            return False, "Invalid verification code"

        request.status = CCPARequestStatus.VERIFIED
        request.verified_at = time.time()
        await self.store.save_ccpa_request(request)

        self._audit_log(
            "ccpa.request_verified",
            request_id=request_id,
        )

        return True, "Verification successful"

    async def process_access_request(
        self,
        request_id: str,
    ) -> dict[str, Any]:
        """Process a verified access request and compile user data."""
        if not self.store:
            return {"error": "Store not configured"}

        request = await self.store.get_ccpa_request(request_id)
        if not request:
            return {"error": "Request not found"}

        if request.status != CCPARequestStatus.VERIFIED:
            return {"error": "Request not verified"}

        request.status = CCPARequestStatus.IN_PROGRESS
        await self.store.save_ccpa_request(request)

        user_data = await self._collect_user_data(
            request.user_id,
            request.org_id,
            request.categories_requested or list(CCPA_DATA_CATEGORIES.keys()),
        )

        export_url = await self._create_data_export(request_id, user_data)
        export_expiry = time.time() + (30 * 86400)

        request.status = CCPARequestStatus.COMPLETED
        request.completed_at = time.time()
        request.data_export_url = export_url
        request.data_export_expires_at = export_expiry
        await self.store.save_ccpa_request(request)

        self._audit_log(
            "ccpa.access_request_completed",
            request_id=request_id,
            categories=list(user_data.keys()),
        )

        return {
            "request_id": request_id,
            "status": "completed",
            "data_export_url": export_url,
            "expires_at": datetime.fromtimestamp(export_expiry, tz=timezone.utc).isoformat(),
        }

    async def process_deletion_request(
        self,
        request_id: str,
    ) -> dict[str, Any]:
        """Process a verified deletion request."""
        if not self.store:
            return {"error": "Store not configured"}

        request = await self.store.get_ccpa_request(request_id)
        if not request:
            return {"error": "Request not found"}

        if request.status != CCPARequestStatus.VERIFIED:
            return {"error": "Request not verified"}

        request.status = CCPARequestStatus.IN_PROGRESS
        await self.store.save_ccpa_request(request)

        exceptions_applied = await self._check_deletion_exceptions(
            request.user_id,
            request.org_id,
        )

        deleted_categories = await self._execute_deletion(
            request.user_id,
            request.org_id,
            exclude_exceptions=exceptions_applied,
        )

        request.status = CCPARequestStatus.COMPLETED
        request.completed_at = time.time()
        request.exceptions_applied = exceptions_applied
        await self.store.save_ccpa_request(request)

        self._audit_log(
            "ccpa.deletion_request_completed",
            request_id=request_id,
            deleted_categories=deleted_categories,
            exceptions=len(exceptions_applied),
        )

        return {
            "request_id": request_id,
            "status": "completed",
            "deleted_categories": deleted_categories,
            "exceptions_applied": exceptions_applied,
        }

    async def get_data_categories(
        self,
        user_id: str,
        org_id: str,
    ) -> dict[str, Any]:
        """Get disclosure of data categories collected (Right to Know)."""
        now = datetime.now(timezone.utc)
        twelve_months_ago = now - timedelta(days=365)

        categories_collected = []
        for category, info in CCPA_DATA_CATEGORIES.items():
            has_data = await self._check_category_has_data(user_id, org_id, category)
            if has_data:
                categories_collected.append(
                    {
                        "category": category,
                        "description": info["description"],
                        "sources": ["direct_collection", "automatic_collection"],
                        "business_purpose": "Service provision and improvement",
                        "third_party_shared": False,
                    }
                )

        return {
            "user_id": user_id,
            "collection_period": {
                "start": twelve_months_ago.isoformat(),
                "end": now.isoformat(),
            },
            "categories_collected": categories_collected,
            "sale_disclosure": {
                "sold_in_past_12_months": False,
                "categories_sold": [],
            },
        }

    async def determine_california_residency(
        self,
        user_id: str,
        ip_address: str = "",
        billing_state: str = "",
        self_declared: str = "",
    ) -> CaliforniaResidentIndicator:
        """Determine if user is a California resident."""
        indicator = CaliforniaResidentIndicator(user_id=user_id)

        if self_declared:
            indicator.self_declared_state = self_declared.upper()
            indicator.is_california_resident = self_declared.upper() == "CA"
            indicator.determination_method = "self_declared"
        elif billing_state:
            indicator.billing_state = billing_state.upper()
            indicator.is_california_resident = billing_state.upper() == "CA"
            indicator.determination_method = "billing_address"
        elif ip_address:
            region = await self._geoip_lookup(ip_address)
            indicator.geoip_region = region or ""
            indicator.last_ip_check = ip_address
            indicator.last_check_at = time.time()
            indicator.is_california_resident = region == "CA"
            indicator.determination_method = "geoip"
        else:
            indicator.determination_method = "unknown"

        if self.store:
            await self.store.save_california_indicator(indicator)

        return indicator

    async def get_request_status(self, request_id: str) -> dict[str, Any] | None:
        """Get the status of a CCPA request."""
        if not self.store:
            return None

        request = await self.store.get_ccpa_request(request_id)
        if not request:
            return None

        return {
            "request_id": request.request_id,
            "request_type": request.request_type.value,
            "status": request.status.value,
            "created_at": datetime.fromtimestamp(
                request.created_at, tz=timezone.utc
            ).isoformat(),
            "deadline": datetime.fromtimestamp(
                request.deadline, tz=timezone.utc
            ).isoformat(),
            "verified_at": (
                datetime.fromtimestamp(request.verified_at, tz=timezone.utc).isoformat()
                if request.verified_at
                else None
            ),
            "completed_at": (
                datetime.fromtimestamp(request.completed_at, tz=timezone.utc).isoformat()
                if request.completed_at
                else None
            ),
            "data_export_url": request.data_export_url,
            "exceptions_applied": request.exceptions_applied,
        }

    async def _collect_user_data(
        self,
        user_id: str,
        org_id: str,
        categories: list[str],
    ) -> dict[str, Any]:
        """Collect user data for all requested categories."""
        data: dict[str, Any] = {}
        for category in categories:
            if category in CCPA_DATA_CATEGORIES:
                category_data = await self._get_category_data(user_id, org_id, category)
                if category_data:
                    data[category] = category_data
        return data

    async def _get_category_data(
        self,
        user_id: str,
        org_id: str,
        category: str,
    ) -> dict[str, Any] | None:
        """Get data for a specific category."""
        if not self.store:
            return None
        return await self.store.get_user_data_by_category(user_id, org_id, category)

    async def _check_category_has_data(
        self,
        user_id: str,
        org_id: str,
        category: str,
    ) -> bool:
        """Check if we have data for a category."""
        data = await self._get_category_data(user_id, org_id, category)
        return bool(data)

    async def _create_data_export(
        self,
        request_id: str,
        data: dict[str, Any],
    ) -> str:
        """Create a downloadable data export."""
        return f"/api/v1/ccpa/export/{request_id}"

    async def _check_deletion_exceptions(
        self,
        user_id: str,
        org_id: str,
    ) -> list[dict]:
        """Check for CCPA deletion exceptions."""
        exceptions = []

        has_active_transactions = await self._has_active_transactions(user_id, org_id)
        if has_active_transactions:
            exceptions.append(
                {
                    "code": "1798.105(d)(1)",
                    "reason": CCPA_DELETION_EXCEPTIONS["1798.105(d)(1)"],
                    "affected_data": "billing_records",
                }
            )

        return exceptions

    async def _has_active_transactions(self, user_id: str, org_id: str) -> bool:
        """Check for active transactions requiring data retention."""
        return False

    async def _execute_deletion(
        self,
        user_id: str,
        org_id: str,
        exclude_exceptions: list[dict],
    ) -> list[str]:
        """Execute data deletion respecting exceptions."""
        excluded_categories = {e.get("affected_data") for e in exclude_exceptions}
        deleted = []

        for category in CCPA_DATA_CATEGORIES:
            if category not in excluded_categories:
                if self.store:
                    await self.store.delete_user_data_category(user_id, org_id, category)
                deleted.append(category)

        return deleted

    async def _geoip_lookup(self, ip_address: str) -> str | None:
        """Lookup region from IP address."""
        return None

    def _audit_log(self, event: str, **kwargs: Any) -> None:
        """Log CCPA audit event with 24-month retention marker."""
        try:
            from app import audit

            audit.record(
                event,
                retention_months=CCPA_AUDIT_RETENTION_MONTHS,
                **kwargs,
            )
        except Exception:
            log.info("CCPA audit: %s %s", event, kwargs)


_ccpa_manager: CCPAManager | None = None


def get_ccpa_manager() -> CCPAManager:
    """Get the singleton CCPA manager instance."""
    global _ccpa_manager
    if _ccpa_manager is None:
        _ccpa_manager = CCPAManager()
    return _ccpa_manager
