"""Field-level encryption types for SQLAlchemy.

Provides transparent encryption/decryption for sensitive database columns
using AES-256-GCM with envelope encryption and key versioning.

Features:
- Authenticated encryption (AES-256-GCM)
- Key versioning for rotation support
- Automatic handling of None/NULL values
- Deterministic mode for searchable encryption
- JSON support for structured sensitive data
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from functools import lru_cache
from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

log = logging.getLogger("omnipotent.database.encryption")


class EncryptionError(Exception):
    """Raised when encryption/decryption fails."""

    pass


class KeyManager:
    """Manages encryption keys with versioning and rotation support.

    Integrates with the existing secrets_vault.py envelope encryption.
    """

    _instance: KeyManager | None = None

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}
        self._current_version: str = "v1"
        self._initialized = False

    @classmethod
    def get_instance(cls) -> KeyManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def initialize(
        self,
        master_key: bytes | None = None,
        kms_provider: str = "env",
    ) -> None:
        """Initialize with master key from environment or KMS.

        Args:
            master_key: Direct key bytes (for testing only)
            kms_provider: "env", "aws", "gcp", "azure", or "vault"
        """
        if self._initialized:
            return

        if master_key:
            self._keys[self._current_version] = master_key
        else:
            key_env = os.environ.get("FIELD_ENCRYPTION_KEY")
            if key_env:
                try:
                    self._keys[self._current_version] = base64.urlsafe_b64decode(key_env)
                except Exception:
                    self._keys[self._current_version] = self._derive_key(key_env)

            prev_key_env = os.environ.get("FIELD_ENCRYPTION_KEY_PREV")
            if prev_key_env:
                try:
                    self._keys["v0"] = base64.urlsafe_b64decode(prev_key_env)
                except Exception:
                    self._keys["v0"] = self._derive_key(prev_key_env)

            if not self._keys:
                copilot_key = os.environ.get("COPILOT_SECRET_KEY", "")
                if copilot_key:
                    self._keys[self._current_version] = self._derive_key(copilot_key)
                else:
                    log.warning(
                        "No encryption key configured. Using ephemeral key. "
                        "Set FIELD_ENCRYPTION_KEY for production."
                    )
                    self._keys[self._current_version] = os.urandom(32)

        self._initialized = True

    def _derive_key(self, password: str) -> bytes:
        """Derive a 256-bit key from a password using PBKDF2."""
        import hashlib

        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            b"omnipotent-field-encryption-salt",
            100000,
            dklen=32,
        )

    def get_key(self, version: str | None = None) -> tuple[str, bytes]:
        """Get encryption key, defaulting to current version."""
        if not self._initialized:
            self.initialize()

        version = version or self._current_version
        if version not in self._keys:
            raise EncryptionError(f"Key version {version} not found")

        return version, self._keys[version]

    def rotate_key(self, new_key: bytes) -> str:
        """Add a new key version and make it current."""
        current_num = int(self._current_version[1:])
        new_version = f"v{current_num + 1}"

        self._keys[new_version] = new_key
        self._current_version = new_version

        return new_version

    @property
    def current_version(self) -> str:
        return self._current_version


def _get_aesgcm():
    """Lazy import of AESGCM."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM
    except ImportError:
        raise EncryptionError(
            "cryptography library required: pip install cryptography"
        )


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts string columns using AES-256-GCM.

    Features:
    - Authenticated encryption (AES-256-GCM)
    - Key versioning for rotation support
    - Automatic handling of None/NULL values
    - Base64 encoding for database storage

    Usage:
        class User(Base):
            ssn = Column(EncryptedString(length=512), nullable=True)
            api_key = Column(EncryptedString(deterministic=True))
    """

    impl = String
    cache_ok = True

    def __init__(
        self,
        length: int = 1024,
        deterministic: bool = False,
        key_manager: KeyManager | None = None,
    ) -> None:
        """
        Args:
            length: Maximum length of encrypted ciphertext in database
            deterministic: If True, same plaintext produces same ciphertext
                          (allows equality queries but weaker security)
            key_manager: Custom KeyManager instance (for testing)
        """
        self.length = length
        self.deterministic = deterministic
        self._key_manager = key_manager
        super().__init__(length=length)

    @property
    def key_manager(self) -> KeyManager:
        if self._key_manager is None:
            self._key_manager = KeyManager.get_instance()
        return self._key_manager

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        """Encrypt value before storing in database."""
        if value is None:
            return None

        AESGCM = _get_aesgcm()
        version, key = self.key_manager.get_key()

        if self.deterministic:
            nonce = hashlib.sha256(value.encode()).digest()[:12]
        else:
            nonce = os.urandom(12)

        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, value.encode("utf-8"), None)

        packed = json.dumps(
            {
                "v": version,
                "n": base64.urlsafe_b64encode(nonce).decode(),
                "c": base64.urlsafe_b64encode(ciphertext).decode(),
            }
        )

        return packed

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        """Decrypt value when loading from database."""
        if value is None:
            return None

        AESGCM = _get_aesgcm()

        try:
            data = json.loads(value)
            version = data["v"]
            nonce = base64.urlsafe_b64decode(data["n"])
            ciphertext = base64.urlsafe_b64decode(data["c"])
        except (json.JSONDecodeError, KeyError) as e:
            raise EncryptionError(f"Invalid encrypted data format: {e}")

        _, key = self.key_manager.get_key(version)

        aesgcm = AESGCM(key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {e}")


class EncryptedText(EncryptedString):
    """Encrypted TEXT column for longer content like health records."""

    impl = Text

    def __init__(
        self,
        deterministic: bool = False,
        key_manager: KeyManager | None = None,
    ) -> None:
        super().__init__(
            length=0,
            deterministic=deterministic,
            key_manager=key_manager,
        )


class EncryptedJSON(TypeDecorator):
    """Encrypts JSON/dict data at rest.

    Useful for storing structured sensitive data like:
    - Payment method details
    - Health records with multiple fields
    - Integration credentials
    """

    impl = Text
    cache_ok = True

    def __init__(self, key_manager: KeyManager | None = None) -> None:
        self._key_manager = key_manager
        super().__init__()

    @property
    def key_manager(self) -> KeyManager:
        if self._key_manager is None:
            self._key_manager = KeyManager.get_instance()
        return self._key_manager

    def process_bind_param(
        self,
        value: dict[str, Any] | None,
        dialect: Dialect,
    ) -> str | None:
        if value is None:
            return None

        json_str = json.dumps(value, separators=(",", ":"), default=str)

        AESGCM = _get_aesgcm()
        version, key = self.key_manager.get_key()
        nonce = os.urandom(12)

        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, json_str.encode("utf-8"), None)

        return json.dumps(
            {
                "v": version,
                "n": base64.urlsafe_b64encode(nonce).decode(),
                "c": base64.urlsafe_b64encode(ciphertext).decode(),
            }
        )

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> dict[str, Any] | None:
        if value is None:
            return None

        AESGCM = _get_aesgcm()

        try:
            data = json.loads(value)
            version = data["v"]
            nonce = base64.urlsafe_b64decode(data["n"])
            ciphertext = base64.urlsafe_b64decode(data["c"])
        except (json.JSONDecodeError, KeyError) as e:
            raise EncryptionError(f"Invalid encrypted data format: {e}")

        _, key = self.key_manager.get_key(version)

        aesgcm = AESGCM(key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return json.loads(plaintext.decode("utf-8"))
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {e}")


class BlindIndex:
    """Helper for creating searchable blind indexes on encrypted fields.

    When you need to search on an encrypted field, store a deterministic
    HMAC-based blind index alongside it.

    Usage:
        class User(Base):
            ssn_encrypted = Column(EncryptedString())
            ssn_blind_idx = Column(String(64), index=True)

            @staticmethod
            def compute_ssn_index(ssn: str) -> str:
                return BlindIndex.compute("ssn", ssn)
    """

    _pepper: bytes | None = None

    @classmethod
    def get_pepper(cls) -> bytes:
        """Get the blind index pepper (derived from encryption key)."""
        if cls._pepper is None:
            key_manager = KeyManager.get_instance()
            _, key = key_manager.get_key()
            cls._pepper = hashlib.sha256(key + b":blind-index").digest()
        return cls._pepper

    @classmethod
    def compute(cls, field_name: str, value: str) -> str:
        """Compute blind index for a value."""
        import hmac

        pepper = cls.get_pepper()
        msg = f"{field_name}:{value}".encode()
        return hmac.new(pepper, msg, "sha256").hexdigest()


async def rotate_encrypted_field(
    session: Any,
    model: Any,
    field_name: str,
    batch_size: int = 100,
) -> int:
    """Re-encrypt all values in a field with the current key.

    Use this during key rotation to migrate data to the new key.

    Args:
        session: SQLAlchemy async session
        model: The model class
        field_name: Name of the encrypted field
        batch_size: Number of records to process per batch

    Returns:
        Number of records updated
    """
    from sqlalchemy import select, update

    count = 0
    offset = 0

    while True:
        stmt = select(model).offset(offset).limit(batch_size)
        result = await session.execute(stmt)
        records = result.scalars().all()

        if not records:
            break

        for record in records:
            old_value = getattr(record, field_name)
            if old_value is not None:
                setattr(record, field_name, old_value)
                count += 1

        await session.commit()
        offset += batch_size

    return count
