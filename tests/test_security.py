"""Tests for security module (API auth, secrets scanning, headers)."""

from datetime import datetime, timezone


class TestAPIKeyAuth:
    """Tests for API key authentication."""

    def test_create_api_key(self):
        """Test creating an API key."""
        from app.security.api_auth import create_api_key, KeyRole

        api_key, plaintext = create_api_key(
            org_id="org_test",
            name="Test Key",
            role=KeyRole.RESPONDER,
        )

        assert api_key.id.startswith("key_")
        assert api_key.org_id == "org_test"
        assert api_key.name == "Test Key"
        assert api_key.role == KeyRole.RESPONDER
        assert plaintext.startswith("dcp_")
        assert api_key.prefix == plaintext[:12]

    def test_validate_api_key(self):
        """Test validating an API key."""
        from app.security.api_auth import create_api_key, validate_api_key, KeyRole

        api_key, plaintext = create_api_key(
            org_id="org_validate_test",
            role=KeyRole.ADMIN,
        )

        validated = validate_api_key(plaintext)
        assert validated is not None
        assert validated.id == api_key.id
        assert validated.org_id == "org_validate_test"

    def test_validate_invalid_key(self):
        """Test validating an invalid key returns None."""
        from app.security.api_auth import validate_api_key

        assert validate_api_key("invalid_key") is None
        assert validate_api_key("dcp_invalid_random_key_123") is None
        assert validate_api_key("") is None

    def test_revoke_api_key(self):
        """Test revoking an API key."""
        from app.security.api_auth import (
            create_api_key,
            validate_api_key,
            revoke_api_key,
        )

        api_key, plaintext = create_api_key(org_id="org_revoke_test")

        assert validate_api_key(plaintext) is not None

        result = revoke_api_key(api_key.id, revoked_by="admin")
        assert result is True

        assert validate_api_key(plaintext) is None

    def test_rotate_api_key(self):
        """Test rotating an API key."""
        from app.security.api_auth import (
            create_api_key,
            rotate_api_key,
            validate_api_key,
            KeyStatus,
        )

        old_key, old_plaintext = create_api_key(org_id="org_rotate_test")

        result = rotate_api_key(old_key.id, grace_period_hours=24)
        assert result is not None

        new_key, new_plaintext = result

        assert new_key.id != old_key.id
        assert new_key.rotated_from == old_key.id
        assert old_key.status == KeyStatus.ROTATING
        assert validate_api_key(new_plaintext) is not None

    def test_key_with_expiry(self):
        """Test key expiration."""
        from app.security.api_auth import create_api_key

        api_key, _ = create_api_key(
            org_id="org_expiry_test",
            expires_in_days=30,
        )

        assert api_key.expires_at is not None
        assert api_key.expires_at > datetime.now(timezone.utc)
        assert api_key.is_valid()

    def test_key_with_scopes(self):
        """Test key scopes."""
        from app.security.api_auth import create_api_key, validate_api_key

        api_key, plaintext = create_api_key(
            org_id="org_scope_test",
            scopes=["read:investigations", "write:runbooks"],
        )

        assert api_key.has_scope("read:investigations")
        assert api_key.has_scope("write:runbooks")
        assert not api_key.has_scope("admin:billing")

        validated = validate_api_key(plaintext, required_scope="read:investigations")
        assert validated is not None

        validated = validate_api_key(plaintext, required_scope="admin:billing")
        assert validated is None

    def test_list_api_keys(self):
        """Test listing API keys."""
        from app.security.api_auth import create_api_key, list_api_keys

        org_id = "org_list_test"

        create_api_key(org_id=org_id, name="Key 1")
        create_api_key(org_id=org_id, name="Key 2")

        keys = list_api_keys(org_id)
        assert len(keys) >= 2

        org_keys = [k for k in keys if k.org_id == org_id]
        assert len(org_keys) >= 2


class TestSecretsScanning:
    """Tests for secrets detection."""

    def test_detect_aws_key(self):
        """Test detecting AWS access key."""
        from app.security.secrets import scan_for_secrets, SecretType

        content = 'aws_key = "AKIAIOSFODNN7EXAMPLA"'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        assert any(f.secret_type == SecretType.AWS_ACCESS_KEY for f in findings)

    def test_detect_github_token(self):
        """Test detecting GitHub token."""
        from app.security.secrets import scan_for_secrets, SecretType

        content = 'GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        assert any(f.secret_type == SecretType.GITHUB_TOKEN for f in findings)

    def test_detect_private_key(self):
        """Test detecting private key."""
        from app.security.secrets import scan_for_secrets, SecretType

        content = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"""

        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        assert any(f.secret_type == SecretType.PRIVATE_KEY for f in findings)

    def test_detect_database_url(self):
        """Test detecting database URL with credentials."""
        from app.security.secrets import scan_for_secrets, SecretType

        content = 'DATABASE_URL=postgres://user:password123@localhost:5432/db'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        assert any(f.secret_type == SecretType.DATABASE_URL for f in findings)

    def test_detect_stripe_key(self):
        """Test detecting Stripe live key."""
        from app.security.secrets import scan_for_secrets, SecretType

        content = 'stripe_key = "sk_live_' + '1' * 24 + '"'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        assert any(f.secret_type == SecretType.STRIPE_KEY for f in findings)

    def test_detect_anthropic_key(self):
        """Test detecting Anthropic API key."""
        from app.security.secrets import scan_for_secrets, SecretType

        content = 'ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz12345678'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        assert any(f.secret_type == SecretType.ANTHROPIC_KEY for f in findings)

    def test_no_false_positive_on_example(self):
        """Test that example/test values don't trigger."""
        from app.security.secrets import scan_for_secrets

        content = '# Example: GITHUB_TOKEN=ghp_example_token_for_docs'
        findings = scan_for_secrets(content)

        high_severity = [f for f in findings if f.severity in ("critical", "high")]
        assert len(high_severity) == 0

    def test_masked_preview(self):
        """Test that secret is masked in preview."""
        from app.security.secrets import scan_for_secrets

        content = 'api_key = "sk_live_' + '1' * 24 + '"'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        preview = findings[0]._masked_preview()
        assert "sk_l" in preview
        assert "..." in preview or "[" in preview

    def test_scanner_class(self):
        """Test SecretsScanner class."""
        from app.security.secrets import SecretsScanner

        scanner = SecretsScanner()

        content = """
        AWS_KEY = AKIAIOSFODNN7EXAMPLB
        DB_URL = postgres://admin:secretpassword@db.internal.com/app
        """

        findings = scanner.scan_content(content)
        assert len(findings) >= 2

    def test_finding_to_dict(self):
        """Test SecretFinding serialization."""
        from app.security.secrets import scan_for_secrets

        content = 'token = "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"'
        findings = scan_for_secrets(content)

        assert len(findings) >= 1
        data = findings[0].to_dict()

        assert "type" in data
        assert "severity" in data
        assert "line" in data
        assert "preview" in data

    def test_get_patterns(self):
        """Test getting all secret patterns."""
        from app.security.secrets import get_secret_patterns

        patterns = get_secret_patterns()
        assert len(patterns) > 10

        pattern_types = [p.secret_type.value for p in patterns]
        assert "aws_access_key" in pattern_types
        assert "github_token" in pattern_types


class TestSecurityHeaders:
    """Tests for security headers middleware."""

    def test_headers_middleware_init(self):
        """Test SecurityHeadersMiddleware initialization."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(MockApp(), enable_hsts=True)

        assert middleware.enable_hsts is True
        assert middleware.frame_options == "DENY"
        assert middleware.content_type_options == "nosniff"
        assert "default-src 'self'" in middleware.csp_policy

    def test_default_csp_policy(self):
        """Test default CSP policy contains required directives."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(MockApp())
        csp = middleware.csp_policy

        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "frame-ancestors 'none'" in csp

    def test_default_permissions_policy(self):
        """Test default permissions policy."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        middleware = SecurityHeadersMiddleware(MockApp())
        perms = middleware.permissions_policy

        assert "camera=()" in perms
        assert "microphone=()" in perms
        assert "geolocation=()" in perms

    def test_custom_csp(self):
        """Test custom CSP policy."""
        from app.security.headers import SecurityHeadersMiddleware

        class MockApp:
            pass

        custom_csp = "default-src 'none'; script-src 'self'"
        middleware = SecurityHeadersMiddleware(MockApp(), csp_policy=custom_csp)

        assert middleware.csp_policy == custom_csp


class TestKeyRoles:
    """Tests for API key role hierarchy."""

    def test_role_hierarchy(self):
        """Test role hierarchy for permissions."""
        from app.security.api_auth import KeyRole

        roles = [KeyRole.ADMIN, KeyRole.RESPONDER, KeyRole.CI_CD, KeyRole.VIEWER, KeyRole.READONLY]
        assert len(roles) == 5

    def test_key_status_transitions(self):
        """Test key status values."""
        from app.security.api_auth import KeyStatus

        assert KeyStatus.ACTIVE.value == "active"
        assert KeyStatus.REVOKED.value == "revoked"
        assert KeyStatus.EXPIRED.value == "expired"
        assert KeyStatus.ROTATING.value == "rotating"


class TestIsCommitSafe:
    """Tests for commit safety check."""

    def test_is_safe_to_commit_function(self):
        """Test is_safe_to_commit returns correct type."""
        from app.security.secrets import is_safe_to_commit

        is_safe, findings = is_safe_to_commit(".")

        assert isinstance(is_safe, bool)
        assert isinstance(findings, list)
