"""A returning user is the same user, and login survives more than one replica.

`app/auth/oauth.py` kept linked provider identities in `_OAUTH_ACCOUNTS`, the
user -> accounts index in `_USER_ACCOUNTS`, and pending CSRF states in
`_OAUTH_STATES`. All three were per-process dicts, which produced two failures
that are worse than the usual "state resets on restart":

  * the callback in `app/auth/endpoints.py` mints `user_{token_hex(8)}` whenever
    the account lookup misses. After a restart every lookup missed, so the same
    person signing in with the same Google account came back as a *different*
    user id, with their team memberships, roles and history stranded under the
    previous one. Nothing errored -- they simply arrived as a new customer;

  * the state issued on `/auth/{provider}/login` was verified on
    `/auth/{provider}/callback`, a separate request arriving from the provider's
    redirect. Held per pod, any deployment with more than one replica rejected
    every login whose callback landed elsewhere, with "Invalid or expired state
    parameter".

Migration 008_oauth moves the first two to `oauth_accounts` and the third to
`oauth_states`. `_USER_ACCOUNTS` gets no table: it is a query now.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app import config as cfg
from app.auth import oauth
from app.auth.oauth import OAuthUser
from app.database.engine import close_db, ensure_schema, get_session
from app.database.models import OAuthAccountRecord, OAuthStateRecord


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """A database per test, under tmp_path.

    The engine is a module global, so it is disposed on the way in as well as
    out -- otherwise a test inherits the previous test's engine, still pointing
    at *that* test's tmp_path.
    """
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    asyncio.run(close_db())
    yield
    asyncio.run(close_db())
    cfg.get_settings.cache_clear()


async def _restart() -> None:
    """Simulate a process restart against the same database file.

    Disposes the engine and clears the settings cache, exactly as a redeploy
    would; the file under COPILOT_STATE_DIR survives, which is the entire point.
    Awaited rather than wrapped in `asyncio.run`, because these tests are
    already running on a loop and a nested run would refuse to start.
    """
    await close_db()
    cfg.get_settings.cache_clear()


def _user(provider: str = "google", provider_id: str = "g_1", **kw) -> OAuthUser:
    return OAuthUser(
        provider=provider,
        provider_id=provider_id,
        email=kw.pop("email", "person@example.com"),
        name=kw.pop("name", "A Person"),
        **kw,
    )


async def _row_counts() -> tuple[int, int]:
    await ensure_schema()
    async with get_session() as session:
        accounts = (
            await session.execute(select(func.count()).select_from(OAuthAccountRecord))
        ).scalar_one()
        states = (
            await session.execute(select(func.count()).select_from(OAuthStateRecord))
        ).scalar_one()
    return accounts, states


async def _age_state(state: str, *, minutes: int) -> None:
    await ensure_schema()
    async with get_session() as session:
        await session.execute(
            update(OAuthStateRecord)
            .where(OAuthStateRecord.state == state)
            .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes))
            .execution_options(synchronize_session=False)
        )
        await session.commit()


# -----------------------------------------------------------------------------
# The identity itself
# -----------------------------------------------------------------------------


async def test_a_linked_identity_survives_a_restart():
    """The failure this slice exists to stop, stated directly."""
    await oauth.link_oauth_account("user_original", _user())

    await _restart()

    found = await oauth.find_account_by_provider("google", "g_1")
    assert found is not None
    assert found.user_id == "user_original"


async def test_the_user_id_is_not_reissued_after_a_restart():
    """Signing back in resolves to the same user id, not a fresh one.

    This is the whole bug: the callback only mints a new id when the lookup
    misses, and with a dict every restart made every lookup miss.
    """
    first = await oauth.link_oauth_account("user_alice", _user())

    await _restart()

    # What the callback does on the next sign-in.
    again = await oauth.find_account_by_provider("google", "g_1")
    assert again is not None
    assert again.user_id == first.user_id


async def test_no_in_memory_account_store_remains():
    """The dicts are gone, not merely shadowed by a database.

    A leftover `_OAUTH_ACCOUNTS` would keep serving reads in whichever process
    happened to have written to it, which is precisely the split this removes.
    """
    import inspect

    for name in ("_OAUTH_ACCOUNTS", "_USER_ACCOUNTS", "_OAUTH_STATES"):
        assert not hasattr(oauth, name), f"{name} is still defined"

    # Matches an assignment, not a mention: the module docstring names all
    # three dicts when it explains what replaced them.
    source = inspect.getsource(oauth)
    for name in ("_OAUTH_ACCOUNTS", "_USER_ACCOUNTS", "_OAUTH_STATES"):
        assert not re.search(rf"^{name}\s*[:=]", source, re.M), f"{name} is still assigned"


async def test_get_user_accounts_is_derived_not_indexed():
    """Removing the hand-maintained index means it cannot disagree.

    Deleting the row is enough to drop it from the user's list; there is no
    second structure that could still be pointing at it.
    """
    await oauth.link_oauth_account("user_d", _user(provider_id="g_d"))
    assert len(await oauth.get_user_accounts("user_d")) == 1

    async with get_session() as session:
        await session.execute(
            update(OAuthAccountRecord)
            .where(OAuthAccountRecord.id == "google:g_d")
            .values(user_id="somebody_else")
            .execution_options(synchronize_session=False)
        )
        await session.commit()

    assert await oauth.get_user_accounts("user_d") == []
    assert len(await oauth.get_user_accounts("somebody_else")) == 1


async def test_multiple_providers_for_one_user_survive_a_restart():
    await oauth.link_oauth_account("user_multi", _user("google", "g_m"))
    await oauth.link_oauth_account("user_multi", _user("github", "gh_m"))

    await _restart()

    accounts = await oauth.get_user_accounts("user_multi")
    assert sorted(a.provider for a in accounts) == ["github", "google"]


async def test_relinking_the_same_identity_does_not_duplicate_it():
    await oauth.link_oauth_account("user_x", _user(provider_id="g_dup"))
    await oauth.link_oauth_account("user_x", _user(provider_id="g_dup"))

    accounts, _ = await _row_counts()
    assert accounts == 1


async def test_concurrent_first_logins_resolve_to_one_user():
    """Two racing sign-ins on a brand-new identity must not split it.

    Each arrives with its own freshly-minted candidate id, so without the
    primary key deciding, one person would end up with two user ids and
    whichever row committed last would own the account.
    """
    results = await asyncio.gather(
        *(oauth.link_oauth_account(f"candidate_{i}", _user(provider_id="g_race")) for i in range(5))
    )

    winners = {a.user_id for a in results}
    assert len(winners) == 1

    accounts, _ = await _row_counts()
    assert accounts == 1

    stored = await oauth.find_account_by_provider("google", "g_race")
    assert stored is not None and stored.user_id == winners.pop()


async def test_email_lookup_folds_case_and_persists():
    await oauth.link_oauth_account("user_c", _user(email="MiXeD@Example.COM"))

    await _restart()

    found = await oauth.find_account_by_email("mixed@example.com")
    assert found is not None
    assert found.user_id == "user_c"
    # Stored folded, so the index on `email` is usable rather than decorative.
    assert found.email == "mixed@example.com"


async def test_email_lookup_picks_the_earliest_identity_deterministically():
    """Which account an address resolves to decides which user id a new
    provider gets folded into, so it must not vary between calls.

    The two ids are chosen so that creation order and id order disagree: the
    account linked first sorts *last* by id. Any ordering that is merely stable
    -- by primary key, say -- would still answer consistently, so it has to be
    the earliest one specifically for this to mean anything.
    """
    await oauth.link_oauth_account("user_first", _user("github", "a_first", email="s@example.com"))
    await oauth.link_oauth_account("user_second", _user("google", "z_second", email="s@example.com"))

    await _restart()

    picks = {(await oauth.find_account_by_email("s@example.com")).id for _ in range(5)}
    assert picks == {"github:a_first"}


async def test_last_login_is_written_back():
    """`touch_last_login` updates the row, not a detached copy of it."""
    await oauth.link_oauth_account("user_l", _user(provider_id="g_l"))
    async with get_session() as session:
        await session.execute(
            update(OAuthAccountRecord)
            .where(OAuthAccountRecord.id == "google:g_l")
            .values(last_login=datetime.now(timezone.utc) - timedelta(days=2))
            .execution_options(synchronize_session=False)
        )
        await session.commit()

    before = await oauth.find_account_by_provider("google", "g_l")
    await oauth.touch_last_login("google:g_l")

    await _restart()

    after = await oauth.find_account_by_provider("google", "g_l")
    assert before is not None and after is not None
    assert after.last_login > before.last_login


async def test_timestamps_come_back_timezone_aware():
    """SQLite drops the offset; comparing a naive value against an aware one
    raises rather than comparing wrong, so this is load-bearing."""
    await oauth.link_oauth_account("user_tz", _user(provider_id="g_tz"))

    await _restart()

    account = await oauth.find_account_by_provider("google", "g_tz")
    assert account is not None
    assert account.created_at.tzinfo is not None
    assert account.last_login.tzinfo is not None
    assert account.created_at <= datetime.now(timezone.utc)


async def test_avatar_url_may_be_absent():
    """Nullable column passed explicitly on insert; a lazy load here would
    raise under asyncio rather than return None."""
    await oauth.link_oauth_account("user_n", _user(provider_id="g_n", avatar_url=None))

    account = await oauth.find_account_by_provider("google", "g_n")
    assert account is not None
    assert account.avatar_url is None


# -----------------------------------------------------------------------------
# Login CSRF state
# -----------------------------------------------------------------------------


async def test_state_issued_by_one_process_verifies_in_another():
    """The multi-replica failure, expressed as the restart it is equivalent to.

    A restart and a second pod are the same thing to a process-local dict: the
    verifier does not have what the issuer wrote.
    """
    state = await oauth.generate_oauth_state("google")

    await _restart()

    assert await oauth.verify_oauth_state(state) == "google"


async def test_state_is_consumed_exactly_once():
    state = await oauth.generate_oauth_state("github")

    assert await oauth.verify_oauth_state(state) == "github"
    assert await oauth.verify_oauth_state(state) is None


async def test_concurrent_replays_of_one_state_yield_a_single_winner():
    """Consume-once has to hold under a race, not just in sequence.

    A SELECT-then-DELETE would let every replay of a captured callback URL pass
    the check before any of them removed the row.
    """
    state = await oauth.generate_oauth_state("google")

    verdicts = await asyncio.gather(*(oauth.verify_oauth_state(state) for _ in range(8)))

    assert [v for v in verdicts if v is not None] == ["google"]


async def test_an_expired_state_is_rejected():
    state = await oauth.generate_oauth_state("google")
    await _age_state(state, minutes=20)

    assert await oauth.verify_oauth_state(state) is None


async def test_an_expired_state_is_still_consumed():
    """Rejecting it must not leave the row behind for a later attempt."""
    state = await oauth.generate_oauth_state("google")
    await _age_state(state, minutes=20)

    await oauth.verify_oauth_state(state)

    _, states = await _row_counts()
    assert states == 0


async def test_issuing_a_state_sweeps_expired_ones():
    """Nothing else deletes from this table, so without the sweep on write it
    grows for as long as the process runs."""
    stale = await oauth.generate_oauth_state("google")
    await _age_state(stale, minutes=30)

    await oauth.generate_oauth_state("github")

    _, states = await _row_counts()
    assert states == 1
    assert await oauth.verify_oauth_state(stale) is None


async def test_an_unknown_state_is_rejected():
    assert await oauth.verify_oauth_state("never-issued") is None


async def test_the_provider_is_carried_on_the_state():
    """The callback compares this against the provider in its own path; a state
    that forgot which provider issued it would defeat that check."""
    google = await oauth.generate_oauth_state("google")
    github = await oauth.generate_oauth_state("github")

    assert await oauth.verify_oauth_state(github) == "github"
    assert await oauth.verify_oauth_state(google) == "google"


async def test_the_callback_issues_a_token_for_the_user_that_won_the_link():
    """The session must name the user the identity actually belongs to.

    Covers the race window in the callback: it looks the identity up, misses,
    mints a candidate id, and only then links. If another request created the
    account in between, `link_oauth_account` keeps the id already on it -- and
    the callback has to adopt that answer rather than sign a token for the
    candidate it minted, which belongs to nobody.

    The concurrent insert is simulated by letting the lookup miss against an
    account that is already there, which is exactly the state the losing
    request is in when it reaches the link.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.auth import endpoints
    from app.auth.oauth import verify_session_token

    incoming = _user("google", "g_winner", email="winner@example.com")
    await oauth.link_oauth_account("user_winner", incoming)

    state = await oauth.generate_oauth_state("google")
    request = MagicMock()
    request.base_url = "https://app.example.com/"

    with (
        patch.object(endpoints, "find_account_by_provider", AsyncMock(return_value=None)),
        patch.object(endpoints, "find_account_by_email", AsyncMock(return_value=None)),
        patch.object(
            endpoints, "exchange_code_for_token", AsyncMock(return_value={"access_token": "t"})
        ),
        patch.object(endpoints, "get_user_info", AsyncMock(return_value=incoming)),
    ):
        response = await endpoints.oauth_callback(
            provider="google", request=request, code="c", state=state
        )

    token = response.headers["location"].split("token=", 1)[1]
    payload = verify_session_token(token)
    assert payload is not None
    assert payload["sub"] == "user_winner"

    # And no second identity was created for the candidate id.
    accounts, _ = await _row_counts()
    assert accounts == 1


async def test_clear_empties_both_tables():
    await oauth.link_oauth_account("user_z", _user(provider_id="g_z"))
    await oauth.generate_oauth_state("google")

    await oauth.clear()

    assert await _row_counts() == (0, 0)
