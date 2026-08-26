"""Persist teams, memberships and invitations (now a no-op link in the chain)

Revision ID: 007_teams
Revises: 006_waitlist
Create Date: 2026-08-20

Moves the team workspace out of process memory:

  app/teams/store.py  TeamStore._teams       -> teams
  app/teams/store.py  TeamStore._members     -> team_members
  app/teams/store.py  TeamStore._invitations -> team_invitations

The store's own docstring said "swap for database in production"; its router was
wired into the API at app/api/main.py regardless. So a team created on one pod
did not exist on any other, and every team, membership and pending invitation
was destroyed on each restart, deploy and scale-down. An invitation is a promise
made to somebody outside the system, who has already been handed a link — that
link simply stopped resolving.

The DDL that used to live here has been removed. The other branch created the
same three tables in revision 013_teams, in the shape that
app/database/team_models.py declares, and that branch's ORM models are the ones
the application now imports; two `create_table` calls for one table is a hard
failure on the second, and the wrong shape is a drift failure. 013_teams sits
downstream of this revision after the merge, so `upgrade head` still ends with
teams, team_members and team_invitations present — do not "restore" them here.

The revision itself stays, empty: `008_oauth` names it as its down_revision and
it may already be stamped in a deployed `alembic_version`, so removing the file
would strand those databases with an id alembic cannot resolve.

Neither of the two constraints this revision introduced is lost by deferring to
013_teams; both are new behaviour rather than a like-for-like move from the
dicts, and both are present there:

  A slug identifies one team — `uq_team_org_slug` on (org_id, slug). The dict
  version de-duplicated auto-generated slugs by scanning, but accepted a
  caller-supplied slug verbatim, so two teams could hold the same one;
  get_team_by_slug then returned whichever the scan reached first and the loser
  was unreachable by its own URL.

  team_members (team_id, user_id) is UNIQUE — `uq_team_member`. "Already a
  member" was a read-then-write check that two concurrent invitation accepts
  both passed.

013_teams additionally carries `org_id` with an FK to organizations on all three
tables, which this revision had no column for at all.

`_user_teams` gets no table. It was a hand-maintained index from user to team
ids, updated at four call sites; here the same question is a query against
team_members, which cannot disagree with the memberships it is derived from.

No data migration: the state being moved was destroyed on every restart, so
there is nothing anywhere to carry forward.
"""

from typing import Sequence, Union

revision: str = "007_teams"
down_revision: Union[str, None] = "006_waitlist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema change; see the module docstring."""


def downgrade() -> None:
    """No schema change; see the module docstring."""
