"""Tests for advanced secrets management."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.secrets_advanced import (
    AdvancedSecretsManager,
    AWSSecretsManagerProvider,
    CachedSecret,
    EnvSecretProvider,
    GCPSecretManagerProvider,
    HashiCorpVaultProvider,
    RotationPolicy,
    SecretAccessLog,
    SecretCache,
    SecretMetadata,
    SecretProvider,
    SecretRotationManager,
    SecretType,
    get_secret,
)


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("TEST_SECRET", "test-value-123")
    monkeypatch.setenv("ANOTHER_SECRET", "another-value")


@pytest.fixture
def sample_metadata():
    return SecretMetadata(
        name="test-secret",
        version="v1",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        secret_type=SecretType.API_KEY,
        provider=SecretProvider.ENV,
    )


class TestSecretMetadata:
    def test_metadata_creation(self):
        now = datetime.now(timezone.utc)
        metadata = SecretMetadata(
            name="api-key",
            version="v2",
            created_at=now,
            expires_at=now + timedelta(days=90),
            secret_type=SecretType.API_KEY,
            provider=SecretProvider.AWS_SECRETS_MANAGER,
            rotation_enabled=True,
            last_rotated=now - timedelta(days=30),
            next_rotation=now + timedelta(days=60),
        )

        assert metadata.name == "api-key"
        assert metadata.rotation_enabled is True
        assert metadata.last_rotated is not None


class TestCachedSecret:
    def test_not_expired(self, sample_metadata):
        cached = CachedSecret(
            value="secret-value",
            metadata=sample_metadata,
            cached_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        assert cached.is_expired() is False

    def test_expired(self, sample_metadata):
        cached = CachedSecret(
            value="secret-value",
            metadata=sample_metadata,
            cached_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )

        assert cached.is_expired() is True


class TestSecretAccessLog:
    def test_log_entry(self):
        log = SecretAccessLog(
            secret_name="db-password",
            accessor="user:admin",
            action="get",
            timestamp=datetime.now(timezone.utc),
            success=True,
            ip_address="192.168.1.1",
            user_agent="copilot/1.0",
        )

        assert log.secret_name == "db-password"
        assert log.success is True


class TestEnvSecretProvider:
    @pytest.mark.asyncio
    async def test_get_existing_secret(self, mock_env):
        provider = EnvSecretProvider()

        value = await provider.get_secret("TEST_SECRET")

        assert value == "test-value-123"

    @pytest.mark.asyncio
    async def test_get_nonexistent_secret(self):
        provider = EnvSecretProvider()

        value = await provider.get_secret("NONEXISTENT_SECRET_12345")

        assert value is None

    @pytest.mark.asyncio
    async def test_set_secret(self):
        provider = EnvSecretProvider()

        version = await provider.set_secret("NEW_SECRET", "new-value")

        assert version == "env-v1"
        assert os.environ.get("NEW_SECRET") == "new-value"

        del os.environ["NEW_SECRET"]

    @pytest.mark.asyncio
    async def test_rotate_secret(self, mock_env):
        provider = EnvSecretProvider()

        version = await provider.rotate_secret("TEST_SECRET", "rotated-value")

        assert version == "env-v1"
        assert os.environ.get("TEST_SECRET") == "rotated-value"

    @pytest.mark.asyncio
    async def test_list_versions(self, mock_env):
        provider = EnvSecretProvider()

        versions = await provider.list_versions("TEST_SECRET")

        assert versions == ["env-v1"]

    @pytest.mark.asyncio
    async def test_list_versions_nonexistent(self):
        provider = EnvSecretProvider()

        versions = await provider.list_versions("NONEXISTENT")

        assert versions == []

    @pytest.mark.asyncio
    async def test_health_check(self):
        provider = EnvSecretProvider()

        healthy = await provider.health_check()

        assert healthy is True


class TestSecretCache:
    @pytest.mark.asyncio
    async def test_cache_miss(self):
        cache = SecretCache()

        result = await cache.get("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_set_and_get(self, sample_metadata):
        cache = SecretCache(default_ttl=timedelta(minutes=5))

        await cache.set("my-secret", "secret-value", sample_metadata)
        result = await cache.get("my-secret")

        assert result is not None
        assert result.value == "secret-value"

    @pytest.mark.asyncio
    async def test_cache_expiration(self, sample_metadata):
        cache = SecretCache(default_ttl=timedelta(seconds=0))

        await cache.set(
            "expiring", "value", sample_metadata, ttl=timedelta(seconds=-1)
        )
        result = await cache.get("expiring")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_invalidation(self, sample_metadata):
        cache = SecretCache()

        await cache.set("to-invalidate", "value", sample_metadata)
        await cache.invalidate("to-invalidate")
        result = await cache.get("to-invalidate")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_clear(self, sample_metadata):
        cache = SecretCache()

        await cache.set("secret1", "value1", sample_metadata)
        await cache.set("secret2", "value2", sample_metadata)
        await cache.clear()

        assert await cache.get("secret1") is None
        assert await cache.get("secret2") is None


class TestRotationPolicy:
    def test_default_policy(self):
        policy = RotationPolicy(
            secret_type=SecretType.API_KEY,
            rotation_interval=timedelta(days=90),
        )

        assert policy.rotation_interval == timedelta(days=90)
        assert policy.overlap_period == timedelta(hours=1)
        assert policy.auto_rotate is True

    def test_custom_policy(self):
        policy = RotationPolicy(
            secret_type=SecretType.DATABASE_PASSWORD,
            rotation_interval=timedelta(days=30),
            overlap_period=timedelta(hours=2),
            notification_before=timedelta(days=14),
            auto_rotate=False,
        )

        assert policy.overlap_period == timedelta(hours=2)
        assert policy.auto_rotate is False


class TestSecretRotationManager:
    @pytest.mark.asyncio
    async def test_rotate_secret(self):
        mock_provider = AsyncMock()
        mock_provider.rotate_secret.return_value = "v2"

        manager = SecretRotationManager(mock_provider)

        version = await manager.rotate(
            name="api-key",
            secret_type=SecretType.API_KEY,
            generate_new=lambda: "new-key-value",
        )

        assert version == "v2"
        mock_provider.rotate_secret.assert_called_once()

    @pytest.mark.asyncio
    async def test_rotation_callback(self):
        mock_provider = AsyncMock()
        mock_provider.rotate_secret.return_value = "v2"

        manager = SecretRotationManager(mock_provider)

        callback_called = []

        def on_rotate(name: str, value: str):
            callback_called.append((name, value))

        manager.register_rotation_callback("api-key", on_rotate)

        await manager.rotate(
            name="api-key",
            secret_type=SecretType.API_KEY,
            generate_new=lambda: "new-value",
        )

        assert len(callback_called) == 1
        assert callback_called[0] == ("api-key", "new-value")

    @pytest.mark.asyncio
    async def test_async_rotation_callback(self):
        mock_provider = AsyncMock()
        mock_provider.rotate_secret.return_value = "v3"

        manager = SecretRotationManager(mock_provider)

        callback_results = []

        async def async_callback(name: str, value: str):
            callback_results.append((name, value))

        manager.register_rotation_callback("db-pass", async_callback)

        await manager.rotate(
            name="db-pass",
            secret_type=SecretType.DATABASE_PASSWORD,
            generate_new=lambda: "new-password",
        )

        assert len(callback_results) == 1


class TestAdvancedSecretsManager:
    @pytest.mark.asyncio
    async def test_get_with_cache(self, mock_env):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        value = await manager.get("TEST_SECRET", accessor="test-user")

        assert value == "test-value-123"

        cached = await manager._cache.get("TEST_SECRET")
        assert cached is not None

    @pytest.mark.asyncio
    async def test_get_with_default(self):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        value = await manager.get("NONEXISTENT", default="fallback")

        assert value == "fallback"

    @pytest.mark.asyncio
    async def test_get_bypass_cache(self, mock_env):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        await manager.get("TEST_SECRET")
        value = await manager.get("TEST_SECRET", bypass_cache=True)

        assert value == "test-value-123"

    @pytest.mark.asyncio
    async def test_set_invalidates_cache(self, mock_env):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        await manager.get("TEST_SECRET")
        await manager.set("TEST_SECRET", "new-value")

        cached = await manager._cache.get("TEST_SECRET")
        assert cached is None

    @pytest.mark.asyncio
    async def test_audit_logging(self, mock_env):
        manager = AdvancedSecretsManager(
            provider=SecretProvider.ENV, enable_audit=True
        )

        await manager.get("TEST_SECRET", accessor="user:alice")
        await manager.get("ANOTHER_SECRET", accessor="user:bob")

        logs = manager.get_audit_log(limit=10)

        assert len(logs) == 2
        assert logs[0].accessor == "user:alice"
        assert logs[1].accessor == "user:bob"

    @pytest.mark.asyncio
    async def test_audit_log_filter_by_secret(self, mock_env):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        await manager.get("TEST_SECRET")
        await manager.get("ANOTHER_SECRET")

        logs = manager.get_audit_log(secret_name="TEST_SECRET")

        assert len(logs) == 1
        assert logs[0].secret_name == "TEST_SECRET"

    @pytest.mark.asyncio
    async def test_health_check(self):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        health = await manager.health_check()

        assert health["provider"] is True
        assert health["cache"] is True

    @pytest.mark.asyncio
    async def test_rotate_secret(self, mock_env):
        manager = AdvancedSecretsManager(provider=SecretProvider.ENV)

        version = await manager.rotate(
            name="TEST_SECRET",
            secret_type=SecretType.API_KEY,
            generate_new=lambda: "rotated-secret-value",
            accessor="rotation-service",
        )

        assert version is not None

        logs = manager.get_audit_log(secret_name="TEST_SECRET")
        assert any(log.action == "rotate" for log in logs)


class TestSecretType:
    def test_all_types(self):
        assert SecretType.API_KEY.value == "api_key"
        assert SecretType.DATABASE_PASSWORD.value == "database_password"
        assert SecretType.ENCRYPTION_KEY.value == "encryption_key"
        assert SecretType.JWT_SECRET.value == "jwt_secret"
        assert SecretType.OAUTH_CLIENT_SECRET.value == "oauth_client_secret"


class TestSecretProvider:
    def test_all_providers(self):
        assert SecretProvider.ENV.value == "env"
        assert SecretProvider.AWS_SECRETS_MANAGER.value == "aws"
        assert SecretProvider.HASHICORP_VAULT.value == "vault"
        assert SecretProvider.GCP_SECRET_MANAGER.value == "gcp"
        assert SecretProvider.AZURE_KEY_VAULT.value == "azure"


class TestAWSSecretsManagerProvider:
    @pytest.mark.asyncio
    async def test_get_secret_success(self):
        provider = AWSSecretsManagerProvider(region="us-west-2", prefix="dev/")

        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": "aws-secret-value"
        }
        provider._client = mock_client

        with patch(
            "asyncio.to_thread", new_callable=AsyncMock
        ) as mock_to_thread:
            mock_to_thread.return_value = {"SecretString": "aws-secret-value"}
            value = await provider.get_secret("my-secret")

        assert value == "aws-secret-value"


class TestHashiCorpVaultProvider:
    def test_initialization(self):
        provider = HashiCorpVaultProvider(
            url="http://vault:8200",
            token="test-token",
            mount="secret",
            path="app",
        )

        assert provider._url == "http://vault:8200"
        assert provider._mount == "secret"
        assert provider._path == "app"


class TestGCPSecretManagerProvider:
    def test_initialization(self):
        provider = GCPSecretManagerProvider(project_id="my-project")

        assert provider._project == "my-project"


class TestGetSecretHelper:
    @pytest.mark.asyncio
    async def test_get_secret_function(self, mock_env):
        import app.secrets_advanced as module

        module._default_manager = None

        value = await get_secret("TEST_SECRET", default="fallback")

        assert value == "test-value-123"
