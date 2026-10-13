import pytest

import app.redis_sessions as sessions


def test_development_may_use_no_redis_fallback(monkeypatch):
    class DevelopmentSettings:
        copilot_redis_url = ""
        is_production = False

    monkeypatch.setattr(sessions, "get_settings", lambda: DevelopmentSettings())
    sessions._redis_client = None
    assert sessions.get_redis_client() is None


def test_production_refuses_missing_redis(monkeypatch):
    class ProductionSettings:
        copilot_redis_url = ""
        is_production = True

    monkeypatch.setattr(sessions, "get_settings", lambda: ProductionSettings())
    sessions._redis_client = None
    with pytest.raises(sessions.RedisUnavailableError, match="COPILOT_REDIS_URL"):
        sessions.get_redis_client()
