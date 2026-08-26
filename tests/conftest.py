"""Suite-wide isolation from an ambient Redis.

CI exports `REDIS_URL` for the whole job (the Postgres+Redis service container),
and `Settings.copilot_redis_url` accepts that bare name as an alias. That is
right for a managed platform and wrong for a test run: it silently swaps two
pieces of global state out from under every test.

The one that bites is the per-IP rate limiter. `api._reset_limiter()` drops the
cached limiter and clears the in-process dict, which is a complete reset of the
in-memory backend — but the Redis backend's counters live in `rl:<ip>:<bucket>`
keys on the server, so rebuilding the limiter object reconnects to the same
counts. Every request in the suite arrives from the same TestClient host inside
the same 60-second bucket, so a few hundred POSTs in, unrelated tests start
getting 429s: 47 failures here, 39 in CI, and a different set each run depending
on which tests happen to land in which minute.

Tests that want Redis set the variable themselves with monkeypatch, which still
works — this fixture only drops what leaked in from the environment.

`tests/integration` is excluded: those tests are the ones meant to run against
the real service container, and their conftest configures them for it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_INTEGRATION = Path(__file__).parent / "integration"


@pytest.fixture(autouse=True)
def _no_ambient_redis(request, monkeypatch):
    if _INTEGRATION in Path(str(request.node.fspath)).parents:
        return

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("COPILOT_REDIS_URL", raising=False)

    # Both are cached: the settings object memoises the URL, and the limiter
    # memoises the backend built from it. Cleared on the way in and out so a
    # test that opts into Redis does not hand its client to the next one.
    _clear_caches()
    yield
    _clear_caches()


def _clear_caches() -> None:
    import app.config as cfg

    # Fetched rather than called directly: a test may have replaced either name
    # with a stub, and this fixture tears down before monkeypatch puts the real
    # one back. Nothing to clear in that case — the stub is not the cache.
    clear = getattr(cfg.get_settings, "cache_clear", None)
    if clear is not None:
        clear()

    # Only if it is already imported — a unit test that never touches the API
    # should not pay for importing it.
    api = sys.modules.get("app.api.main")
    reset = getattr(api, "_reset_limiter", None) if api is not None else None
    if reset is not None:
        reset()
