"""Comprehensive tests for the immutable audit log system.

Tests cover:
- Audit entry creation and hashing
- Hash chain integrity verification
- Merkle tree computation
- Signature creation and verification
- SIEM streaming
- Database persistence
- Redis caching
- Archive segment operations
- Compliance export
- Search functionality
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.audit.immutable_log import (
    AuditAction,
    AuditEntry,
    AuditSeverity,
    ArchiveMetadata,
    ImmutableAuditLog,
    MerkleNode,
    VerificationResult,
    VerificationStatus,
    create_immutable_audit_log,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# -----------------------------------------------------------------------------
# AuditEntry Tests
# -----------------------------------------------------------------------------


class TestAuditEntry:
    """Tests for the AuditEntry dataclass."""

    def test_to_dict_includes_all_fields(self):
        """AuditEntry.to_dict() includes all required fields."""
        entry = AuditEntry(
            id="audit_test123",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            org_id="org_acme",
            actor_id="user_123",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="service",
            resource_id="svc_payment",
            severity=AuditSeverity.INFO,
            description="Created payment service",
            details={"version": "1.0.0"},
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            request_id="req_abc",
            previous_hash="0" * 64,
            entry_hash="a" * 64,
            signature="sig_xyz",
        )

        result = entry.to_dict()

        assert result["id"] == "audit_test123"
        assert result["sequence_number"] == 1
        assert result["timestamp"] == "2024-01-15T12:00:00+00:00"
        assert result["org_id"] == "org_acme"
        assert result["actor_id"] == "user_123"
        assert result["action"] == "create"
        assert result["resource_type"] == "service"
        assert result["severity"] == "info"
        assert result["details"] == {"version": "1.0.0"}
        assert result["ip_address"] == "192.168.1.1"
        assert result["signature"] == "sig_xyz"

    def test_canonical_string_is_deterministic(self):
        """canonical_string produces consistent output for hashing."""
        entry = AuditEntry(
            id="audit_test",
            sequence_number=5,
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            org_id="org_acme",
            actor_id="user_123",
            actor_type="user",
            action=AuditAction.UPDATE,
            resource_type="config",
            resource_id="cfg_123",
            severity=AuditSeverity.WARNING,
            description="Config updated",
            details={"key": "value", "a": 1},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="",
        )

        canonical1 = entry.canonical_string
        canonical2 = entry.canonical_string

        assert canonical1 == canonical2
        assert "|" in canonical1  # Fields are joined by pipe
        assert "5" in canonical1  # Sequence number
        assert "update" in canonical1  # Action value
        assert "org_acme" in canonical1

    def test_canonical_string_sorts_details_keys(self):
        """Details dict is serialized with sorted keys for determinism."""
        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            org_id="org",
            actor_id="user",
            actor_type="user",
            action=AuditAction.READ,
            resource_type="resource",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={"zebra": 1, "alpha": 2},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="",
        )

        canonical = entry.canonical_string
        # JSON should have "alpha" before "zebra" due to sort_keys=True
        assert '"alpha":2' in canonical
        assert canonical.index('"alpha"') < canonical.index('"zebra"')


# -----------------------------------------------------------------------------
# MerkleNode Tests
# -----------------------------------------------------------------------------


class TestMerkleNode:
    """Tests for the MerkleNode dataclass."""

    def test_to_dict_with_children(self):
        """MerkleNode.to_dict() includes child hashes."""
        left = MerkleNode(hash_value="left_hash", entry_id="entry_1")
        right = MerkleNode(hash_value="right_hash", entry_id="entry_2")
        parent = MerkleNode(hash_value="parent_hash", left=left, right=right)

        result = parent.to_dict()

        assert result["hash"] == "parent_hash"
        assert result["left"] == "left_hash"
        assert result["right"] == "right_hash"
        assert result["entry_id"] is None

    def test_to_dict_leaf_node(self):
        """MerkleNode.to_dict() handles leaf nodes without children."""
        leaf = MerkleNode(hash_value="leaf_hash", entry_id="entry_123")

        result = leaf.to_dict()

        assert result["hash"] == "leaf_hash"
        assert result["left"] is None
        assert result["right"] is None
        assert result["entry_id"] == "entry_123"


# -----------------------------------------------------------------------------
# VerificationResult Tests
# -----------------------------------------------------------------------------


class TestVerificationResult:
    """Tests for the VerificationResult dataclass."""

    def test_to_dict(self):
        """VerificationResult.to_dict() serializes correctly."""
        result = VerificationResult(
            status=VerificationStatus.VALID,
            entries_verified=100,
            entries_invalid=0,
            first_invalid_sequence=None,
            merkle_root="root_hash_abc",
            verification_time_ms=42,
            details=["All entries verified"],
        )

        d = result.to_dict()

        assert d["status"] == "valid"
        assert d["entries_verified"] == 100
        assert d["entries_invalid"] == 0
        assert d["first_invalid_sequence"] is None
        assert d["merkle_root"] == "root_hash_abc"
        assert d["verification_time_ms"] == 42
        assert d["details"] == ["All entries verified"]


# -----------------------------------------------------------------------------
# ArchiveMetadata Tests
# -----------------------------------------------------------------------------


class TestArchiveMetadata:
    """Tests for the ArchiveMetadata dataclass."""

    def test_to_dict(self):
        """ArchiveMetadata.to_dict() serializes all fields."""
        metadata = ArchiveMetadata(
            archive_id="archive_abc123",
            org_id="org_acme",
            start_sequence=1,
            end_sequence=1000,
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 31, tzinfo=timezone.utc),
            entry_count=1000,
            merkle_root="root_hash",
            storage_location="s3://bucket/path",
            storage_provider="s3",
            compressed_size_bytes=102400,
            retention_until=datetime(2031, 1, 1, tzinfo=timezone.utc),
            is_locked=True,
        )

        d = metadata.to_dict()

        assert d["archive_id"] == "archive_abc123"
        assert d["org_id"] == "org_acme"
        assert d["start_sequence"] == 1
        assert d["end_sequence"] == 1000
        assert d["entry_count"] == 1000
        assert d["storage_provider"] == "s3"
        assert d["is_locked"] is True


# -----------------------------------------------------------------------------
# ImmutableAuditLog Tests
# -----------------------------------------------------------------------------


class TestImmutableAuditLog:
    """Tests for the ImmutableAuditLog class."""

    def test_init_sets_defaults(self):
        """ImmutableAuditLog initializes with correct defaults."""
        log = ImmutableAuditLog(org_id="org_test")

        assert log.org_id == "org_test"
        assert log._sequence_number == 0
        assert log._latest_hash == ImmutableAuditLog.GENESIS_HASH
        assert log._entries == []
        assert log._siem_endpoints == []

    def test_init_with_signing_key(self):
        """ImmutableAuditLog can be initialized with a signing key."""
        log = ImmutableAuditLog(org_id="org_test", signing_key="secret_key")

        assert log.signing_key == "secret_key"

    def test_init_with_signing_key_from_env(self, monkeypatch):
        """ImmutableAuditLog reads signing key from environment."""
        monkeypatch.setenv("AUDIT_SIGNING_KEY", "env_secret")

        log = ImmutableAuditLog(org_id="org_test")

        assert log.signing_key == "env_secret"

    def test_compute_hash_produces_sha256(self):
        """_compute_hash returns SHA-256 hex digest."""
        log = ImmutableAuditLog(org_id="org_test")
        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            org_id="org_test",
            actor_id="user",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="resource",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="",
        )

        hash_result = log._compute_hash(entry)

        assert len(hash_result) == 64  # SHA-256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in hash_result)

    def test_compute_hash_is_deterministic(self):
        """Same entry produces same hash."""
        log = ImmutableAuditLog(org_id="org_test")
        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            org_id="org_test",
            actor_id="user",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="resource",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="",
        )

        hash1 = log._compute_hash(entry)
        hash2 = log._compute_hash(entry)

        assert hash1 == hash2

    def test_sign_entry_without_key_returns_none(self):
        """_sign_entry returns None when no signing key is set."""
        log = ImmutableAuditLog(org_id="org_test")
        log.signing_key = None

        result = log._sign_entry("some_hash")

        assert result is None

    def test_sign_entry_produces_hmac(self):
        """_sign_entry produces HMAC-SHA256 signature."""
        log = ImmutableAuditLog(org_id="org_test", signing_key="secret")

        signature = log._sign_entry("entry_hash_123")

        assert len(signature) == 64  # HMAC-SHA256 hex = 64 chars
        assert all(c in "0123456789abcdef" for c in signature)

    def test_verify_signature_valid(self):
        """_verify_signature returns True for valid signature."""
        log = ImmutableAuditLog(org_id="org_test", signing_key="secret")
        entry_hash = "entry_hash_123"
        signature = log._sign_entry(entry_hash)

        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            org_id="org_test",
            actor_id="user",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="resource",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash=entry_hash,
            signature=signature,
        )

        assert log._verify_signature(entry) is True

    def test_verify_signature_invalid(self):
        """_verify_signature returns False for tampered signature."""
        log = ImmutableAuditLog(org_id="org_test", signing_key="secret")

        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            org_id="org_test",
            actor_id="user",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="resource",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="entry_hash_123",
            signature="tampered_signature_000000000000000000000000000000",
        )

        assert log._verify_signature(entry) is False

    def test_verify_signature_no_key_returns_true(self):
        """_verify_signature returns True when no key is configured."""
        log = ImmutableAuditLog(org_id="org_test")
        log.signing_key = None

        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            org_id="org_test",
            actor_id="user",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="resource",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="hash",
            signature=None,
        )

        assert log._verify_signature(entry) is True

    def test_add_siem_endpoint(self):
        """add_siem_endpoint adds unique endpoints."""
        log = ImmutableAuditLog(org_id="org_test")

        log.add_siem_endpoint("https://siem1.example.com/ingest")
        log.add_siem_endpoint("https://siem2.example.com/ingest")
        log.add_siem_endpoint("https://siem1.example.com/ingest")  # Duplicate

        assert len(log._siem_endpoints) == 2

    def test_get_stats(self):
        """get_stats returns audit log statistics."""
        log = ImmutableAuditLog(org_id="org_test")
        log._sequence_number = 100
        log._latest_hash = "a" * 64
        log.add_siem_endpoint("https://siem.example.com")

        stats = log.get_stats()

        assert stats["org_id"] == "org_test"
        assert stats["total_entries"] == 100
        assert "..." in stats["latest_hash"]  # Truncated
        assert stats["siem_endpoints"] == 1
        assert stats["in_memory_entries"] == 0


class TestImmutableAuditLogAsync:
    """Async tests for ImmutableAuditLog."""

    def test_log_creates_entry_with_hash_chain(self):
        """log() creates an entry linked to the previous hash."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            entry = await log.log(
                action=AuditAction.CREATE,
                resource_type="service",
                resource_id="svc_123",
                description="Created service",
                actor_id="user_abc",
            )

            assert entry.sequence_number == 1
            assert entry.previous_hash == ImmutableAuditLog.GENESIS_HASH
            assert len(entry.entry_hash) == 64
            assert entry.org_id == "org_test"
            assert entry.actor_id == "user_abc"
            assert entry.action == AuditAction.CREATE

        _run(scenario())

    def test_log_increments_sequence_number(self):
        """Sequential log() calls increment sequence numbers."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            e1 = await log.log(
                action=AuditAction.CREATE,
                resource_type="service",
                resource_id="svc_1",
                description="First",
                actor_id="user",
            )
            e2 = await log.log(
                action=AuditAction.UPDATE,
                resource_type="service",
                resource_id="svc_1",
                description="Second",
                actor_id="user",
            )
            e3 = await log.log(
                action=AuditAction.DELETE,
                resource_type="service",
                resource_id="svc_1",
                description="Third",
                actor_id="user",
            )

            assert e1.sequence_number == 1
            assert e2.sequence_number == 2
            assert e3.sequence_number == 3
            assert e2.previous_hash == e1.entry_hash
            assert e3.previous_hash == e2.entry_hash

        _run(scenario())

    def test_log_with_all_optional_fields(self):
        """log() accepts all optional parameters."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            entry = await log.log(
                action=AuditAction.LOGIN,
                resource_type="session",
                resource_id="sess_123",
                description="User logged in",
                actor_id="user_abc",
                actor_type="service",
                severity=AuditSeverity.WARNING,
                details={"method": "oauth", "provider": "google"},
                ip_address="10.0.0.1",
                user_agent="Custom-Agent/1.0",
                request_id="req_xyz",
            )

            assert entry.actor_type == "service"
            assert entry.severity == AuditSeverity.WARNING
            assert entry.details == {"method": "oauth", "provider": "google"}
            assert entry.ip_address == "10.0.0.1"
            assert entry.user_agent == "Custom-Agent/1.0"
            assert entry.request_id == "req_xyz"

        _run(scenario())

    def test_log_with_signing_key_creates_signature(self):
        """log() creates signature when signing key is set."""
        log = ImmutableAuditLog(org_id="org_test", signing_key="secret_key")

        async def scenario():
            entry = await log.log(
                action=AuditAction.APPROVE,
                resource_type="approval",
                resource_id="apr_123",
                description="Approved action",
                actor_id="admin",
            )

            assert entry.signature is not None
            assert len(entry.signature) == 64

        _run(scenario())

    def test_log_persists_to_database(self):
        """log() persists entry to database when configured."""
        mock_db = AsyncMock()
        log = ImmutableAuditLog(org_id="org_test", db_client=mock_db)

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="resource",
                resource_id="r_123",
                description="Test",
                actor_id="user",
            )

            mock_db.execute.assert_called_once()
            call_args = mock_db.execute.call_args
            # First arg is the SQL query
            assert "INSERT INTO audit_log_immutable" in call_args[0][0]

        _run(scenario())

    def test_log_caches_to_redis(self):
        """log() caches entry to Redis when configured."""
        mock_redis = AsyncMock()
        log = ImmutableAuditLog(org_id="org_test", redis_client=mock_redis)

        async def scenario():
            await log.log(
                action=AuditAction.READ,
                resource_type="resource",
                resource_id="r_123",
                description="Test",
                actor_id="user",
            )

            assert mock_redis.setex.called
            assert mock_redis.lpush.called
            assert mock_redis.ltrim.called

        _run(scenario())

    def test_log_streams_to_siem(self):
        """log() streams to configured SIEM endpoints."""
        log = ImmutableAuditLog(org_id="org_test")
        log.add_siem_endpoint("https://siem.example.com/ingest")

        async def scenario():
            with patch.object(log, "_get_http_client") as mock_client_factory:
                mock_client = AsyncMock()
                mock_client_factory.return_value = mock_client

                await log.log(
                    action=AuditAction.EXECUTE,
                    resource_type="command",
                    resource_id="cmd_123",
                    description="Executed command",
                    actor_id="system",
                )

                mock_client.post.assert_called_once()
                call_args = mock_client.post.call_args
                assert call_args[0][0] == "https://siem.example.com/ingest"

        _run(scenario())

    def test_initialize_loads_from_database(self):
        """initialize() loads state from database."""
        mock_db = AsyncMock()
        mock_db.fetchrow.side_effect = [
            {"seq": 42},  # Latest sequence
            {"entry_hash": "abc123" + "0" * 58},  # Latest hash
        ]
        log = ImmutableAuditLog(org_id="org_test", db_client=mock_db)

        async def scenario():
            await log.initialize()

            assert log._sequence_number == 42
            assert log._latest_hash == "abc123" + "0" * 58

        _run(scenario())

    def test_initialize_handles_empty_database(self):
        """initialize() handles empty database gracefully."""
        mock_db = AsyncMock()
        mock_db.fetchrow.side_effect = [None, None]
        log = ImmutableAuditLog(org_id="org_test", db_client=mock_db)

        async def scenario():
            await log.initialize()

            assert log._sequence_number == 0
            assert log._latest_hash == ImmutableAuditLog.GENESIS_HASH

        _run(scenario())

    def test_close_closes_http_client(self):
        """close() properly closes HTTP client."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            # Initialize the HTTP client
            mock_client = AsyncMock()
            log._http_client = mock_client

            await log.close()

            # The mock was captured before close() set _http_client to None
            mock_client.aclose.assert_called_once()
            assert log._http_client is None

        _run(scenario())


class TestVerifyIntegrity:
    """Tests for hash chain and Merkle tree verification."""

    def test_verify_integrity_empty_log_returns_missing(self):
        """verify_integrity on empty log returns MISSING status."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            result = await log.verify_integrity()

            assert result.status == VerificationStatus.MISSING
            assert result.entries_verified == 0
            assert "No entries found" in result.details[0]

        _run(scenario())

    def test_verify_integrity_valid_chain(self):
        """verify_integrity returns VALID for correct hash chain."""
        log = ImmutableAuditLog(org_id="org_test", signing_key="secret")

        async def scenario():
            for i in range(5):
                await log.log(
                    action=AuditAction.CREATE,
                    resource_type="test",
                    resource_id=f"test_{i}",
                    description=f"Entry {i}",
                    actor_id="user",
                )

            result = await log.verify_integrity()

            assert result.status == VerificationStatus.VALID
            assert result.entries_verified == 5
            assert result.entries_invalid == 0
            assert result.merkle_root is not None

        _run(scenario())

    def test_verify_integrity_detects_tampered_hash(self):
        """verify_integrity detects tampered entry hash."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="test_1",
                description="Entry 1",
                actor_id="user",
            )
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="test_2",
                description="Entry 2",
                actor_id="user",
            )

            # Tamper with the first entry's hash
            log._entries[0].entry_hash = "tampered_hash" + "0" * 50

            result = await log.verify_integrity()

            assert result.status == VerificationStatus.INVALID
            assert result.entries_invalid > 0

        _run(scenario())

    def test_verify_integrity_with_sequence_range(self):
        """verify_integrity can verify a specific sequence range."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            for i in range(10):
                await log.log(
                    action=AuditAction.CREATE,
                    resource_type="test",
                    resource_id=f"test_{i}",
                    description=f"Entry {i}",
                    actor_id="user",
                )

            result = await log.verify_integrity(start_sequence=3, end_sequence=7)

            assert result.entries_verified == 5

        _run(scenario())

    def test_compute_merkle_root_single_entry(self):
        """_compute_merkle_root handles single entry."""
        log = ImmutableAuditLog(org_id="org_test")
        entry = AuditEntry(
            id="audit_test",
            sequence_number=1,
            timestamp=datetime.now(timezone.utc),
            org_id="org_test",
            actor_id="user",
            actor_type="user",
            action=AuditAction.CREATE,
            resource_type="test",
            resource_id="id",
            severity=AuditSeverity.INFO,
            description="test",
            details={},
            ip_address=None,
            user_agent=None,
            request_id=None,
            previous_hash="0" * 64,
            entry_hash="a" * 64,
        )

        root = log._compute_merkle_root([entry])

        assert root == "a" * 64  # Single entry's hash is the root

    def test_compute_merkle_root_multiple_entries(self):
        """_compute_merkle_root computes correct root for multiple entries."""
        log = ImmutableAuditLog(org_id="org_test")
        entries = []
        for i in range(4):
            entry = AuditEntry(
                id=f"audit_{i}",
                sequence_number=i + 1,
                timestamp=datetime.now(timezone.utc),
                org_id="org_test",
                actor_id="user",
                actor_type="user",
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id=f"id_{i}",
                severity=AuditSeverity.INFO,
                description="test",
                details={},
                ip_address=None,
                user_agent=None,
                request_id=None,
                previous_hash="0" * 64,
                entry_hash=f"{i}" * 64,
            )
            entries.append(entry)

        root = log._compute_merkle_root(entries)

        assert len(root) == 64
        assert root != entries[0].entry_hash  # Root is computed from children

    def test_compute_merkle_root_empty_returns_genesis(self):
        """_compute_merkle_root returns GENESIS_HASH for empty list."""
        log = ImmutableAuditLog(org_id="org_test")

        root = log._compute_merkle_root([])

        assert root == ImmutableAuditLog.GENESIS_HASH


class TestArchiveSegment:
    """Tests for archive segment operations."""

    def test_archive_segment_empty_range_returns_none(self):
        """archive_segment returns None for empty range."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            result = await log.archive_segment(1, 100)

            assert result is None

        _run(scenario())

    def test_archive_segment_creates_metadata(self):
        """archive_segment creates proper metadata without S3."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            # Create some entries
            for i in range(5):
                await log.log(
                    action=AuditAction.CREATE,
                    resource_type="test",
                    resource_id=f"test_{i}",
                    description=f"Entry {i}",
                    actor_id="user",
                )

            result = await log.archive_segment(1, 5)

            assert result is not None
            assert result.org_id == "org_test"
            assert result.start_sequence == 1
            assert result.end_sequence == 5
            assert result.entry_count == 5
            assert result.is_locked is True

        _run(scenario())

    def test_archive_segment_uploads_to_s3(self):
        """archive_segment uploads to S3 when configured."""
        mock_s3 = MagicMock()
        log = ImmutableAuditLog(org_id="org_test", s3_client=mock_s3)

        async def scenario():
            for i in range(3):
                await log.log(
                    action=AuditAction.CREATE,
                    resource_type="test",
                    resource_id=f"test_{i}",
                    description=f"Entry {i}",
                    actor_id="user",
                )

            with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
                await log.archive_segment(1, 3)

                mock_thread.assert_called_once()

        _run(scenario())


class TestSearch:
    """Tests for audit log search functionality."""

    def test_search_by_actor_id(self):
        """search() filters by actor_id."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="t1",
                description="By user1",
                actor_id="user1",
            )
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="t2",
                description="By user2",
                actor_id="user2",
            )
            await log.log(
                action=AuditAction.UPDATE,
                resource_type="test",
                resource_id="t1",
                description="By user1 again",
                actor_id="user1",
            )

            results = await log.search(actor_id="user1")

            assert len(results) == 2
            assert all(e.actor_id == "user1" for e in results)

        _run(scenario())

    def test_search_by_action(self):
        """search() filters by action type."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="t1",
                description="Created",
                actor_id="user",
            )
            await log.log(
                action=AuditAction.DELETE,
                resource_type="test",
                resource_id="t1",
                description="Deleted",
                actor_id="user",
            )

            results = await log.search(action=AuditAction.DELETE)

            assert len(results) == 1
            assert results[0].action == AuditAction.DELETE

        _run(scenario())

    def test_search_by_resource_type(self):
        """search() filters by resource_type."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="service",
                resource_id="s1",
                description="Service",
                actor_id="user",
            )
            await log.log(
                action=AuditAction.CREATE,
                resource_type="config",
                resource_id="c1",
                description="Config",
                actor_id="user",
            )

            results = await log.search(resource_type="config")

            assert len(results) == 1
            assert results[0].resource_type == "config"

        _run(scenario())

    def test_search_by_severity(self):
        """search() filters by severity."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="t1",
                description="Info",
                actor_id="user",
                severity=AuditSeverity.INFO,
            )
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="t2",
                description="Error",
                actor_id="user",
                severity=AuditSeverity.ERROR,
            )

            results = await log.search(severity=AuditSeverity.ERROR)

            assert len(results) == 1
            assert results[0].severity == AuditSeverity.ERROR

        _run(scenario())

    def test_search_respects_limit(self):
        """search() respects the limit parameter."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            for i in range(10):
                await log.log(
                    action=AuditAction.CREATE,
                    resource_type="test",
                    resource_id=f"t{i}",
                    description=f"Entry {i}",
                    actor_id="user",
                )

            results = await log.search(limit=5)

            assert len(results) == 5

        _run(scenario())

    def test_search_combined_filters(self):
        """search() combines multiple filters."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            await log.log(
                action=AuditAction.CREATE,
                resource_type="service",
                resource_id="s1",
                description="Create service",
                actor_id="admin",
            )
            await log.log(
                action=AuditAction.UPDATE,
                resource_type="service",
                resource_id="s1",
                description="Update service",
                actor_id="admin",
            )
            await log.log(
                action=AuditAction.CREATE,
                resource_type="config",
                resource_id="c1",
                description="Create config",
                actor_id="admin",
            )

            results = await log.search(
                actor_id="admin",
                action=AuditAction.CREATE,
                resource_type="service",
            )

            assert len(results) == 1
            assert results[0].description == "Create service"

        _run(scenario())


class TestExportForCompliance:
    """Tests for compliance export functionality."""

    def test_export_for_compliance_includes_metadata(self):
        """export_for_compliance includes proper metadata."""
        log = ImmutableAuditLog(org_id="org_test")

        async def scenario():
            now = datetime.now(timezone.utc)
            await log.log(
                action=AuditAction.CREATE,
                resource_type="test",
                resource_id="t1",
                description="Test",
                actor_id="user",
            )

            start = now - timedelta(hours=1)
            end = now + timedelta(hours=1)
            result = await log.export_for_compliance(start, end)

            assert "export_metadata" in result
            assert result["export_metadata"]["org_id"] == "org_test"
            assert result["export_metadata"]["entry_count"] == 1
            assert "entries" in result
            assert "verification" in result

        _run(scenario())


class TestRowToEntry:
    """Tests for database row to entry conversion."""

    def test_row_to_entry_converts_correctly(self):
        """_row_to_entry properly converts database row."""
        log = ImmutableAuditLog(org_id="org_test")
        row = {
            "id": "audit_123",
            "sequence_number": 42,
            "timestamp": datetime(2024, 1, 15, tzinfo=timezone.utc),
            "org_id": "org_test",
            "actor_id": "user_abc",
            "actor_type": "user",
            "action": "create",
            "resource_type": "service",
            "resource_id": "svc_123",
            "severity": "warning",
            "description": "Test description",
            "details": '{"key": "value"}',
            "ip_address": "10.0.0.1",
            "user_agent": "Agent/1.0",
            "request_id": "req_xyz",
            "previous_hash": "0" * 64,
            "entry_hash": "a" * 64,
            "signature": "sig_abc",
        }

        entry = log._row_to_entry(row)

        assert entry.id == "audit_123"
        assert entry.sequence_number == 42
        assert entry.action == AuditAction.CREATE
        assert entry.severity == AuditSeverity.WARNING
        assert entry.details == {"key": "value"}

    def test_row_to_entry_handles_dict_details(self):
        """_row_to_entry handles details already as dict."""
        log = ImmutableAuditLog(org_id="org_test")
        row = {
            "id": "audit_123",
            "sequence_number": 1,
            "timestamp": datetime.now(timezone.utc),
            "org_id": "org_test",
            "actor_id": "user",
            "actor_type": "user",
            "action": "read",
            "resource_type": "resource",
            "resource_id": "id",
            "severity": "info",
            "description": "Test",
            "details": {"already": "dict"},  # Already a dict, not JSON string
            "ip_address": None,
            "user_agent": None,
            "request_id": None,
            "previous_hash": "0" * 64,
            "entry_hash": "a" * 64,
            "signature": None,
        }

        entry = log._row_to_entry(row)

        assert entry.details == {"already": "dict"}


class TestFactoryFunction:
    """Tests for the create_immutable_audit_log factory function."""

    def test_create_immutable_audit_log_basic(self):
        """create_immutable_audit_log creates log with basic config."""
        log = create_immutable_audit_log(org_id="org_factory")

        assert isinstance(log, ImmutableAuditLog)
        assert log.org_id == "org_factory"

    def test_create_immutable_audit_log_with_all_clients(self):
        """create_immutable_audit_log accepts all client parameters."""
        mock_db = MagicMock()
        mock_redis = MagicMock()
        mock_s3 = MagicMock()

        log = create_immutable_audit_log(
            org_id="org_full",
            signing_key="secret",
            db_client=mock_db,
            redis_client=mock_redis,
            s3_client=mock_s3,
        )

        assert log.signing_key == "secret"
        assert log.db == mock_db
        assert log.redis == mock_redis
        assert log.s3 == mock_s3


class TestEnums:
    """Tests for audit-related enums."""

    def test_audit_action_values(self):
        """AuditAction enum has expected values."""
        assert AuditAction.CREATE.value == "create"
        assert AuditAction.READ.value == "read"
        assert AuditAction.UPDATE.value == "update"
        assert AuditAction.DELETE.value == "delete"
        assert AuditAction.LOGIN.value == "login"
        assert AuditAction.LOGOUT.value == "logout"

    def test_audit_severity_values(self):
        """AuditSeverity enum has expected values."""
        assert AuditSeverity.INFO.value == "info"
        assert AuditSeverity.WARNING.value == "warning"
        assert AuditSeverity.ERROR.value == "error"
        assert AuditSeverity.CRITICAL.value == "critical"

    def test_verification_status_values(self):
        """VerificationStatus enum has expected values."""
        assert VerificationStatus.VALID.value == "valid"
        assert VerificationStatus.INVALID.value == "invalid"
        assert VerificationStatus.MISSING.value == "missing"
        assert VerificationStatus.PENDING.value == "pending"
