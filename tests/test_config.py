"""Settings validators: provider/env normalization and production fail-closed."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

# Every credential production fails closed without, so env-normalization tests
# can vary one thing at a time.
PROD = {
    "copilot_env": "production",
    "copilot_api_token": "x",
    "copilot_secret_key": "k",
    "copilot_checkpoint_db": "postgresql://db/omnipotent",
    "copilot_redis_url": "redis://redis:6379/0",
}


def test_provider_normalized_and_validated():
    assert Settings(copilot_provider="ANTHROPIC").copilot_provider == "anthropic"
    with pytest.raises(ValidationError):
        Settings(copilot_provider="bogus")


def test_env_normalization():
    assert Settings(**{**PROD, "copilot_env": "PROD"}).is_production is True
    assert Settings(**PROD).copilot_env == "production"
    assert Settings(copilot_env="").is_production is False
    assert Settings(copilot_env="development").is_production is False


def test_production_requires_api_token():
    # Fail closed: no unauthenticated API in production.
    with pytest.raises(ValidationError):
        Settings(**{**PROD, "copilot_api_token": ""})
    s = Settings(**{**PROD, "copilot_api_token": "secret"})
    assert s.is_production and s.copilot_api_token == "secret"


def test_production_requires_secret_key():
    # It signs session tokens; without it they'd be signed with a known constant.
    with pytest.raises(ValidationError):
        Settings(**{**PROD, "copilot_secret_key": ""})
    # Not required outside production.
    assert Settings(copilot_env="development", copilot_secret_key="").copilot_secret_key == ""


def test_demo_mode_off_by_default_and_refused_in_production():
    assert Settings().copilot_demo_mode is False
    with pytest.raises(ValidationError):
        Settings(**PROD, copilot_demo_mode=True)
    assert Settings(copilot_env="development", copilot_demo_mode=True).copilot_demo_mode is True


def test_production_requires_durable_postgres_checkpointer():
    with pytest.raises(ValidationError, match="COPILOT_CHECKPOINT_DB"):
        Settings(**{**PROD, "copilot_checkpoint_db": "./checkpoints.sqlite"})

    settings = Settings(**{**PROD, "copilot_checkpoint_db": "postgresql://db/omnipotent"})
    assert settings.copilot_checkpoint_db.startswith("postgresql://")


def test_production_requires_shared_redis():
    with pytest.raises(ValidationError, match="COPILOT_REDIS_URL"):
        Settings(**{**PROD, "copilot_redis_url": ""})
    settings = Settings(**PROD)
    assert settings.copilot_redis_url.startswith("redis://")


def test_non_production_may_use_sqlite_checkpointer():
    settings = Settings(copilot_env="staging", copilot_checkpoint_db="./checkpoints.sqlite")
    assert settings.copilot_checkpoint_db.endswith(".sqlite")


def test_database_and_redis_urls_accept_unprefixed_env(monkeypatch):
    # Managed platforms inject bare DATABASE_URL / REDIS_URL; both names work,
    # and the COPILOT_-prefixed one wins when both are present.
    monkeypatch.setenv("DATABASE_URL", "postgresql://host/db")
    monkeypatch.setenv("REDIS_URL", "redis://host:6379/0")
    s = Settings()
    assert s.copilot_database_url == "postgresql://host/db"
    assert s.copilot_redis_url == "redis://host:6379/0"

    monkeypatch.setenv("COPILOT_DATABASE_URL", "postgresql://prefixed/db")
    assert Settings().copilot_database_url == "postgresql://prefixed/db"


def test_state_dir_is_configurable_and_created(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path / "state"))
    s = Settings()
    assert s.state_dir == (tmp_path / "state").resolve()
    assert s.state_path("copilot.db") == (tmp_path / "state" / "copilot.db").resolve()
    assert s.state_dir.is_dir()  # state_path() created it

    # An explicit relative/absolute path from the operator is honoured as-is.
    assert Settings().state_path(str(tmp_path / "x.db")) == Path(tmp_path / "x.db").resolve()


def test_safety_limit_defaults():
    s = Settings(copilot_env="development")
    assert s.copilot_rate_limit_per_min > 0
    assert s.copilot_max_message_chars > 0
    assert s.copilot_max_body_bytes > 0
    assert s.copilot_max_sessions >= 0
    assert s.copilot_trust_proxy is False
