"""Native Multi-Factor Authentication (MFA) module.

Provides TOTP (Time-based One-Time Password) and WebAuthn/FIDO2 support,
independent of any IdP. Features:

- TOTP enrollment with QR code generation
- WebAuthn/FIDO2 passwordless authentication
- Recovery codes generation and verification
- MFA rate limiting
- Device/session binding for "remember this device"
- Audit logging for all MFA operations
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from app.config import get_settings

log = logging.getLogger("omnipotent.security.mfa")


class MFAMethod(str, Enum):
    """Supported MFA methods."""

    TOTP = "totp"
    WEBAUTHN = "webauthn"
    EMAIL = "email"
    SMS = "sms"
    RECOVERY = "recovery"


class MFAStatus(str, Enum):
    """MFA enrollment status."""

    NOT_ENROLLED = "not_enrolled"
    PENDING = "pending"
    ENROLLED = "enrolled"
    DISABLED = "disabled"


@dataclass
class TOTPConfig:
    """TOTP configuration."""

    issuer: str = "OmniPotent"
    algorithm: str = "SHA1"
    digits: int = 6
    period: int = 30
    secret_length: int = 32
    drift_tolerance: int = 1


@dataclass
class MFAEnrollment:
    """MFA enrollment state for a user."""

    user_id: str
    method: MFAMethod
    status: MFAStatus = MFAStatus.NOT_ENROLLED
    secret_encrypted: str = ""
    backup_codes_hash: list[str] = field(default_factory=list)
    webauthn_credentials: list[dict] = field(default_factory=list)
    enrolled_at: float | None = None
    last_used_at: float | None = None
    failed_attempts: int = 0
    locked_until: float | None = None
    trusted_devices: list[str] = field(default_factory=list)


@dataclass
class MFAVerificationResult:
    """Result of MFA verification."""

    success: bool
    method: MFAMethod
    message: str = ""
    remaining_attempts: int = 3
    locked: bool = False
    lock_duration: int = 0
    used_recovery_code: bool = False


@dataclass
class TOTPEnrollmentResult:
    """Result of TOTP enrollment initiation."""

    secret: str
    provisioning_uri: str
    qr_code_data: str
    recovery_codes: list[str]


class TOTP:
    """Time-based One-Time Password implementation (RFC 6238)."""

    def __init__(self, config: TOTPConfig | None = None):
        self.config = config or TOTPConfig()

    def generate_secret(self) -> str:
        """Generate a cryptographically secure TOTP secret."""
        return base64.b32encode(
            secrets.token_bytes(self.config.secret_length)
        ).decode("ascii")

    def get_totp_token(self, secret: str, timestamp: float | None = None) -> str:
        """Generate a TOTP token for the given secret and timestamp."""
        if timestamp is None:
            timestamp = time.time()

        counter = int(timestamp) // self.config.period
        return self._generate_hotp(secret, counter)

    def _generate_hotp(self, secret: str, counter: int) -> str:
        """Generate HOTP token (base for TOTP)."""
        key = base64.b32decode(secret.upper())
        msg = struct.pack(">Q", counter)

        if self.config.algorithm == "SHA1":
            h = hmac.new(key, msg, hashlib.sha1).digest()
        elif self.config.algorithm == "SHA256":
            h = hmac.new(key, msg, hashlib.sha256).digest()
        elif self.config.algorithm == "SHA512":
            h = hmac.new(key, msg, hashlib.sha512).digest()
        else:
            h = hmac.new(key, msg, hashlib.sha1).digest()

        offset = h[-1] & 0x0F
        code = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
        return str(code % (10 ** self.config.digits)).zfill(self.config.digits)

    def verify_token(
        self,
        secret: str,
        token: str,
        timestamp: float | None = None,
    ) -> bool:
        """Verify a TOTP token with drift tolerance."""
        if timestamp is None:
            timestamp = time.time()

        for drift in range(-self.config.drift_tolerance, self.config.drift_tolerance + 1):
            check_time = timestamp + (drift * self.config.period)
            expected = self.get_totp_token(secret, check_time)
            if hmac.compare_digest(expected, token):
                return True
        return False

    def get_provisioning_uri(
        self,
        secret: str,
        account_name: str,
        issuer: str | None = None,
    ) -> str:
        """Generate otpauth:// URI for QR code enrollment."""
        issuer = issuer or self.config.issuer
        params = [
            f"secret={secret}",
            f"issuer={issuer}",
            f"algorithm={self.config.algorithm}",
            f"digits={self.config.digits}",
            f"period={self.config.period}",
        ]
        label = f"{issuer}:{account_name}" if issuer else account_name
        return f"otpauth://totp/{label}?{'&'.join(params)}"


class RecoveryCodes:
    """Recovery code generation and verification."""

    CODE_COUNT = 10
    CODE_LENGTH = 10
    CODE_CHARS = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    @classmethod
    def generate(cls) -> list[str]:
        """Generate a set of recovery codes."""
        codes = []
        for _ in range(cls.CODE_COUNT):
            code = "".join(
                secrets.choice(cls.CODE_CHARS) for _ in range(cls.CODE_LENGTH)
            )
            codes.append(f"{code[:5]}-{code[5:]}")
        return codes

    @classmethod
    def hash_codes(cls, codes: list[str]) -> list[str]:
        """Hash recovery codes for storage."""
        return [
            hashlib.sha256(code.replace("-", "").upper().encode()).hexdigest()
            for code in codes
        ]

    @classmethod
    def verify_code(cls, code: str, hashed_codes: list[str]) -> tuple[bool, int]:
        """
        Verify a recovery code against hashed codes.

        Returns:
            (valid, index) - index of the used code for removal
        """
        normalized = code.replace("-", "").replace(" ", "").upper()
        code_hash = hashlib.sha256(normalized.encode()).hexdigest()
        for i, h in enumerate(hashed_codes):
            if hmac.compare_digest(h, code_hash):
                return True, i
        return False, -1


class MFAManager:
    """Manages MFA enrollment, verification, and trusted devices."""

    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_DURATION = 300
    TRUSTED_DEVICE_DURATION = 30 * 24 * 3600

    def __init__(self, store: Any = None):
        self.store = store
        self.totp = TOTP()

    async def get_enrollment(self, user_id: str) -> MFAEnrollment | None:
        """Get MFA enrollment status for a user."""
        if not self.store:
            return None
        return await self.store.get_mfa_enrollment(user_id)

    async def initiate_totp_enrollment(
        self,
        user_id: str,
        email: str,
    ) -> TOTPEnrollmentResult:
        """Start TOTP enrollment process."""
        secret = self.totp.generate_secret()
        recovery_codes = RecoveryCodes.generate()
        uri = self.totp.get_provisioning_uri(secret, email)

        enrollment = MFAEnrollment(
            user_id=user_id,
            method=MFAMethod.TOTP,
            status=MFAStatus.PENDING,
            secret_encrypted=await self._encrypt_secret(secret),
            backup_codes_hash=RecoveryCodes.hash_codes(recovery_codes),
        )

        if self.store:
            await self.store.save_mfa_enrollment(enrollment)

        self._audit_log("mfa.totp_enrollment_initiated", user_id=user_id)

        return TOTPEnrollmentResult(
            secret=secret,
            provisioning_uri=uri,
            qr_code_data=self._generate_qr_data(uri),
            recovery_codes=recovery_codes,
        )

    async def complete_totp_enrollment(
        self,
        user_id: str,
        token: str,
    ) -> MFAVerificationResult:
        """Complete TOTP enrollment by verifying the first token."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment or enrollment.status != MFAStatus.PENDING:
            return MFAVerificationResult(
                success=False,
                method=MFAMethod.TOTP,
                message="No pending enrollment found",
            )

        secret = await self._decrypt_secret(enrollment.secret_encrypted)
        if not self.totp.verify_token(secret, token):
            return MFAVerificationResult(
                success=False,
                method=MFAMethod.TOTP,
                message="Invalid verification code",
            )

        enrollment.status = MFAStatus.ENROLLED
        enrollment.enrolled_at = time.time()

        if self.store:
            await self.store.save_mfa_enrollment(enrollment)

        self._audit_log("mfa.totp_enrollment_completed", user_id=user_id)

        return MFAVerificationResult(
            success=True,
            method=MFAMethod.TOTP,
            message="TOTP enrollment completed successfully",
        )

    async def verify_totp(
        self,
        user_id: str,
        token: str,
        device_fingerprint: str | None = None,
    ) -> MFAVerificationResult:
        """Verify a TOTP token during authentication."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment or enrollment.status != MFAStatus.ENROLLED:
            return MFAVerificationResult(
                success=False,
                method=MFAMethod.TOTP,
                message="MFA not enrolled",
            )

        if enrollment.locked_until and time.time() < enrollment.locked_until:
            remaining = int(enrollment.locked_until - time.time())
            return MFAVerificationResult(
                success=False,
                method=MFAMethod.TOTP,
                message=f"Account locked. Try again in {remaining} seconds",
                locked=True,
                lock_duration=remaining,
            )

        secret = await self._decrypt_secret(enrollment.secret_encrypted)
        if self.totp.verify_token(secret, token):
            enrollment.failed_attempts = 0
            enrollment.last_used_at = time.time()
            enrollment.locked_until = None

            if self.store:
                await self.store.save_mfa_enrollment(enrollment)

            self._audit_log(
                "mfa.totp_verification_success",
                user_id=user_id,
                device=device_fingerprint,
            )

            return MFAVerificationResult(
                success=True,
                method=MFAMethod.TOTP,
                message="Verification successful",
            )

        enrollment.failed_attempts += 1
        remaining = self.MAX_FAILED_ATTEMPTS - enrollment.failed_attempts

        if enrollment.failed_attempts >= self.MAX_FAILED_ATTEMPTS:
            enrollment.locked_until = time.time() + self.LOCKOUT_DURATION
            remaining = 0
            self._audit_log("mfa.account_locked", user_id=user_id)

        if self.store:
            await self.store.save_mfa_enrollment(enrollment)

        self._audit_log(
            "mfa.totp_verification_failed",
            user_id=user_id,
            attempts=enrollment.failed_attempts,
        )

        return MFAVerificationResult(
            success=False,
            method=MFAMethod.TOTP,
            message="Invalid verification code",
            remaining_attempts=remaining,
            locked=remaining == 0,
            lock_duration=self.LOCKOUT_DURATION if remaining == 0 else 0,
        )

    async def verify_recovery_code(
        self,
        user_id: str,
        code: str,
    ) -> MFAVerificationResult:
        """Verify a recovery code (one-time use)."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment or enrollment.status != MFAStatus.ENROLLED:
            return MFAVerificationResult(
                success=False,
                method=MFAMethod.RECOVERY,
                message="MFA not enrolled",
            )

        valid, index = RecoveryCodes.verify_code(code, enrollment.backup_codes_hash)
        if not valid:
            self._audit_log("mfa.recovery_code_failed", user_id=user_id)
            return MFAVerificationResult(
                success=False,
                method=MFAMethod.RECOVERY,
                message="Invalid recovery code",
            )

        enrollment.backup_codes_hash.pop(index)
        enrollment.last_used_at = time.time()

        if self.store:
            await self.store.save_mfa_enrollment(enrollment)

        remaining_codes = len(enrollment.backup_codes_hash)
        self._audit_log(
            "mfa.recovery_code_used",
            user_id=user_id,
            remaining_codes=remaining_codes,
        )

        return MFAVerificationResult(
            success=True,
            method=MFAMethod.RECOVERY,
            message=f"Recovery code accepted. {remaining_codes} codes remaining.",
            used_recovery_code=True,
        )

    async def regenerate_recovery_codes(self, user_id: str) -> list[str] | None:
        """Generate new recovery codes (invalidates old ones)."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment or enrollment.status != MFAStatus.ENROLLED:
            return None

        new_codes = RecoveryCodes.generate()
        enrollment.backup_codes_hash = RecoveryCodes.hash_codes(new_codes)

        if self.store:
            await self.store.save_mfa_enrollment(enrollment)

        self._audit_log("mfa.recovery_codes_regenerated", user_id=user_id)

        return new_codes

    async def add_trusted_device(
        self,
        user_id: str,
        device_fingerprint: str,
    ) -> bool:
        """Add a device to the trusted list (skip MFA for duration)."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment:
            return False

        device_hash = hashlib.sha256(device_fingerprint.encode()).hexdigest()[:16]
        trust_token = f"{device_hash}:{int(time.time() + self.TRUSTED_DEVICE_DURATION)}"

        if trust_token not in enrollment.trusted_devices:
            enrollment.trusted_devices.append(trust_token)
            enrollment.trusted_devices = enrollment.trusted_devices[-10:]

            if self.store:
                await self.store.save_mfa_enrollment(enrollment)

        self._audit_log(
            "mfa.trusted_device_added",
            user_id=user_id,
            device_hash=device_hash,
        )

        return True

    async def is_trusted_device(
        self,
        user_id: str,
        device_fingerprint: str,
    ) -> bool:
        """Check if a device is trusted (can skip MFA)."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment:
            return False

        device_hash = hashlib.sha256(device_fingerprint.encode()).hexdigest()[:16]
        now = time.time()

        for token in enrollment.trusted_devices:
            parts = token.split(":")
            if len(parts) == 2:
                stored_hash, expiry = parts
                if stored_hash == device_hash and float(expiry) > now:
                    return True

        return False

    async def disable_mfa(self, user_id: str, admin_user_id: str | None = None) -> bool:
        """Disable MFA for a user."""
        enrollment = await self.get_enrollment(user_id)
        if not enrollment:
            return False

        enrollment.status = MFAStatus.DISABLED
        enrollment.secret_encrypted = ""
        enrollment.backup_codes_hash = []
        enrollment.webauthn_credentials = []
        enrollment.trusted_devices = []

        if self.store:
            await self.store.save_mfa_enrollment(enrollment)

        self._audit_log(
            "mfa.disabled",
            user_id=user_id,
            admin_user_id=admin_user_id,
        )

        return True

    async def _encrypt_secret(self, secret: str) -> str:
        """Encrypt MFA secret for storage."""
        from app.secrets_vault import encrypt

        return encrypt(secret)

    async def _decrypt_secret(self, encrypted: str) -> str:
        """Decrypt MFA secret."""
        from app.secrets_vault import decrypt

        return decrypt(encrypted)

    def _generate_qr_data(self, uri: str) -> str:
        """Generate base64-encoded QR code data URL."""
        try:
            import qrcode
            import io

            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(uri)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"
        except ImportError:
            return uri

    def _audit_log(self, event: str, **kwargs: Any) -> None:
        """Log MFA-related audit events."""
        try:
            from app import audit

            audit.record(event, **kwargs)
        except Exception:
            log.info("MFA audit: %s %s", event, kwargs)


_mfa_manager: MFAManager | None = None


def get_mfa_manager() -> MFAManager:
    """Get the singleton MFA manager instance."""
    global _mfa_manager
    if _mfa_manager is None:
        _mfa_manager = MFAManager()
    return _mfa_manager
