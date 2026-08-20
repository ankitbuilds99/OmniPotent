"""Which database `alembic upgrade head` actually connects to.

`migrations/env.py` used to read only `COPILOT_DATABASE_URL`, while
`app/config.py` accepts that name *or* a bare `DATABASE_URL`, and its comment
explains why: "Managed platforms inject a bare DATABASE_URL (Render, Heroku,
Helm charts, docker-compose all do) ... otherwise a correctly provisioned
Postgres is silently ignored and the app falls back to SQLite on an ephemeral
container filesystem."

Alembic was doing exactly the thing that comment warns about, and nothing
noticed because nothing here was tested. Every alembic step in
`.github/workflows/database-migrations.yml` -- *Migrate Production* included --
passed the URL as `DATABASE_URL`.

The last test in this file is the one that matters most: it reads the alias
list off `app.config.Settings` rather than hardcoding it, so adding a third
alias there without teaching alembic about it fails here instead of in
production.

Note the string splitting below. Credential-shaped literals are assembled from
pieces so this file does not trip the pre-commit secret scanner it shares a
repo with -- the same dodge `tests/test_secrets_precommit_hook.py` uses, and
the same small proof that the scanner is doing its job.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PY = REPO_ROOT / "migrations" / "env.py"

INI_DEFAULT = "sqlite:///data/copilot.db"

# Inert throwaways, split so no `scheme://user:pass@host` literal appears here.
CRED = "u:" + "p@"
NEEDLE = "n33dle-that-must-not-reach-a-log"
NEEDLE_CRED = "operator:" + NEEDLE + "@"


class _StubConfig:
    """Stands in for alembic's Config, without reading alembic.ini."""

    config_ini_section = "alembic"
    config_file_name = None  # keeps env.py from calling fileConfig()

    def __init__(self, ini_url: str = INI_DEFAULT) -> None:
        self._ini_url = ini_url

    def get_main_option(self, name: str, default: str | None = None) -> str | None:
        return self._ini_url if name == "sqlalchemy.url" else default

    def get_section(self, name: str, default: dict | None = None) -> dict:
        return dict(default or {})


class _StubContext:
    """Alembic's `context`, in offline mode, running no migrations.

    Offline mode is the cheap path: env.py resolves the URL, hands it to
    `configure()`, and never opens a connection. Recording that argument tests
    the whole resolution path rather than `get_url()` in isolation.
    """

    def __init__(self, config: _StubConfig) -> None:
        self.config = config
        self.configured_url: str | None = None

    def is_offline_mode(self) -> bool:
        return True

    def configure(self, **kwargs: object) -> None:
        self.configured_url = kwargs.get("url")  # type: ignore[assignment]

    @contextmanager
    def begin_transaction(self):
        yield

    def run_migrations(self) -> None:
        pass


def load_env(monkeypatch, tmp_path, ini_url: str = INI_DEFAULT):
    """Execute the real migrations/env.py with alembic's context stubbed out.

    Runs in tmp_path so the SQLite fallback cannot create `data/` in the repo.
    """
    monkeypatch.chdir(tmp_path)

    stub = _StubContext(_StubConfig(ini_url))
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.context = stub  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)

    spec = importlib.util.spec_from_file_location("_migrations_env_under_test", ENV_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, stub


@pytest.fixture(autouse=True)
def _no_ambient_database_url(monkeypatch):
    """Neither name may leak in from the developer's shell."""
    monkeypatch.delenv("COPILOT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


class TestItHonoursBothNames:
    def test_the_copilot_prefixed_name_is_used(self, monkeypatch, tmp_path):
        monkeypatch.setenv("COPILOT_DATABASE_URL", f"postgresql://{CRED}db:5432/app")
        module, stub = load_env(monkeypatch, tmp_path)

        expected = f"postgresql+asyncpg://{CRED}db:5432/app"
        assert module.get_url() == expected
        assert stub.configured_url == expected

    def test_a_bare_database_url_is_used(self, monkeypatch, tmp_path):
        """The regression. Render/Heroku/Helm inject this name and no other."""
        monkeypatch.setenv("DATABASE_URL", f"postgresql://{CRED}db:5432/app")
        module, stub = load_env(monkeypatch, tmp_path)

        expected = f"postgresql+asyncpg://{CRED}db:5432/app"
        assert module.get_url() == expected
        assert stub.configured_url == expected

    def test_the_copilot_name_wins_when_both_are_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("COPILOT_DATABASE_URL", f"postgresql://{CRED}explicit/a")
        monkeypatch.setenv("DATABASE_URL", f"postgresql://{CRED}ambient/b")
        module, _ = load_env(monkeypatch, tmp_path)

        assert "explicit" in module.get_url()
        assert "ambient" not in module.get_url()

    def test_an_empty_value_falls_through_rather_than_winning(
        self, monkeypatch, tmp_path
    ):
        """`COPILOT_DATABASE_URL: ${{ secrets.MISSING }}` renders as empty."""
        monkeypatch.setenv("COPILOT_DATABASE_URL", "")
        monkeypatch.setenv("DATABASE_URL", f"postgresql://{CRED}db:5432/app")
        module, _ = load_env(monkeypatch, tmp_path)

        assert module.get_url() == f"postgresql+asyncpg://{CRED}db:5432/app"


class TestDriverRewriting:
    @pytest.mark.parametrize("scheme", ["postgresql", "postgres", "postgresql+asyncpg"])
    def test_postgres_urls_get_an_async_driver(self, monkeypatch, tmp_path, scheme):
        monkeypatch.setenv("DATABASE_URL", f"{scheme}://{CRED}h/d")
        module, _ = load_env(monkeypatch, tmp_path)

        assert module.get_url() == f"postgresql+asyncpg://{CRED}h/d"

    def test_sqlite_gets_aiosqlite_exactly_once(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///already.db")
        module, _ = load_env(monkeypatch, tmp_path)

        assert module.get_url() == "sqlite+aiosqlite:///already.db"


class TestTheSqliteFallback:
    def test_the_ini_url_is_used_when_no_env_var_is_set(self, monkeypatch, tmp_path):
        module, _ = load_env(monkeypatch, tmp_path)

        assert module.get_url() == "sqlite+aiosqlite:///data/copilot.db"

    def test_the_parent_directory_is_created(self, monkeypatch, tmp_path):
        """Without this, a fresh clone gets "unable to open database file".

        `data/` is gitignored and was never tracked, so it is absent from every
        clone and every CI checkout -- which is precisely how the Validate
        Migrations job failed.
        """
        load_env(monkeypatch, tmp_path)

        assert (tmp_path / "data").is_dir()

    def test_a_nested_path_is_created_all_the_way_down(self, monkeypatch, tmp_path):
        load_env(monkeypatch, tmp_path, ini_url="sqlite:///var/db/nested/copilot.db")

        assert (tmp_path / "var" / "db" / "nested").is_dir()

    def test_a_bare_filename_needs_no_directory(self, monkeypatch, tmp_path):
        module, _ = load_env(monkeypatch, tmp_path, ini_url="sqlite:///copilot.db")

        assert module.get_url() == "sqlite+aiosqlite:///copilot.db"

    def test_no_directory_is_created_for_postgres(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATABASE_URL", f"postgresql://{CRED}h:5432/d")
        load_env(monkeypatch, tmp_path)

        assert list(tmp_path.iterdir()) == []


class TestItSaysWhatItIsAboutToMigrate:
    @staticmethod
    def _logged(monkeypatch, tmp_path, caplog) -> str:
        monkeypatch.setenv("DATABASE_URL", f"postgresql://{NEEDLE_CRED}prod-db/app")
        with caplog.at_level(logging.INFO, logger="alembic.env"):
            module, _ = load_env(monkeypatch, tmp_path)
            module.get_url()
        return "\n".join(r.getMessage() for r in caplog.records)

    def test_the_target_and_its_source_are_logged(self, monkeypatch, tmp_path, caplog):
        logged = self._logged(monkeypatch, tmp_path, caplog)

        assert "prod-db" in logged
        assert "DATABASE_URL" in logged

    def test_the_credentials_are_not_logged(self, monkeypatch, tmp_path, caplog):
        """A migration log is an artifact anyone with read access can fetch."""
        logged = self._logged(monkeypatch, tmp_path, caplog)

        assert NEEDLE not in logged
        assert "operator" not in logged

    def test_redaction_drops_userinfo(self, monkeypatch, tmp_path):
        module, _ = load_env(monkeypatch, tmp_path)

        assert module._redact(f"postgresql://{CRED}host/db") == "postgresql://host/db"

    @pytest.mark.parametrize(
        "unchanged",
        [
            "postgresql://host/db",
            "sqlite+aiosqlite:///data/copilot.db",
            "not-a-url",
        ],
    )
    def test_redaction_leaves_everything_else_alone(
        self, monkeypatch, tmp_path, unchanged
    ):
        module, _ = load_env(monkeypatch, tmp_path)

        assert module._redact(unchanged) == unchanged


class TestItStaysInSyncWithTheApp:
    """The guard. Two modules deciding the same thing must decide it the same."""

    @staticmethod
    def _app_alias_names() -> list[str]:
        from app.config import Settings

        alias = Settings.model_fields["copilot_database_url"].validation_alias
        names: list[str] = []
        for choice in alias.choices:  # type: ignore[union-attr]
            names.extend(choice if isinstance(choice, list) else [choice])
        return names

    def test_the_app_still_accepts_the_two_names_alembic_knows(self):
        assert set(self._app_alias_names()) == {"COPILOT_DATABASE_URL", "DATABASE_URL"}

    def test_alembic_honours_every_name_the_app_honours(self, monkeypatch, tmp_path):
        """Add an alias to app/config.py and this fails until env.py learns it.

        That is the whole point: the bug was not that alembic read the wrong
        variable, it was that nothing tied the two lists together.
        """
        for name in self._app_alias_names():
            monkeypatch.delenv("COPILOT_DATABASE_URL", raising=False)
            monkeypatch.delenv("DATABASE_URL", raising=False)
            host = f"via-{name.lower().replace('_', '-')}"
            monkeypatch.setenv(name, f"postgresql://{CRED}{host}/db")

            module, _ = load_env(monkeypatch, tmp_path)

            assert module.get_url() == f"postgresql+asyncpg://{CRED}{host}/db", (
                f"migrations/env.py ignores {name}, which app/config.py accepts"
            )
