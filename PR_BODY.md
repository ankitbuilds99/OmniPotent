A 20-agent audit produced 77 candidate findings; adversarial re-triage confirmed **57** and refuted 20. This branch fixes all 57, in seven areas, one commit per area on top of the earlier phase-0 security work.

Nearly every finding is the same shape: **a control that looks like it works, or a number that looks like it was measured.** Not crashes — crashes get noticed. These are the ones that fail quietly, during an incident, while the screen says everything is fine.

## What changed, by area

| Area | Findings | The representative one |
|---|---|---|
| auth-tenancy | 8 | OAuth login was an identity check, not an authorization decision; identity was never persisted |
| billing-growth | 10 | The paid tier did not work end to end |
| incidents-oncall-slack | 10 | The paging path did not page |
| automation-runbooks-planning | 9 | Runbook steps reported work they did not do |
| knowledge-intelligence | 7 | The knowledge and intelligence layers answered questions they were never asked |
| governance-status-notifications | 4 | Notifications were not delivered; webhook SSRF was open |
| frontend | 9 | See below |

### Frontend (the last area)

- **Dashboard** — Acknowledge and Resolve were a 422 on *every* click. `user_id` was a required query parameter, the page sent none, and the handler was `if (res.ok)` with no else — so nothing appeared on screen and the escalation kept paging. Covering those routes for the first time exposed a defect behind them: resolution notes were assigned into `custom_fields` in place, an untracked mutation, so the note the responder typed was never written.
- **Analytics** — AI ROI, MTTA, uptime and error rate were arithmetic on constants, not readings. Removed.
- **SLOTracker** — the target was rendered as though it were the SLI, the error budget was hardcoded, and `no_data` displayed as `healthy`. An SLO with no data reported perfect health.
- **Billing** — "Manage Subscription" reloaded the page.
- **Incidents** — the list synthesised an investigation for every row: fabricated id, hardcoded `"running"`, empty agent list, root cause literally `"Investigation in progress..."`. A *finished* investigation rendered as one still running.
- **History** — the status filter and the pager never reached the API, which was called with a literal `?limit=50`. "Next" redrew the same fifty rows; selecting "Error" listed completed investigations.
- **Postmortem** — section Save and action-item status wrote to React state and stopped, because there was no endpoint, because there was no table. Adds `postmortems` (one row per incident) + store + GET/PATCH routes. A per-section regenerate preserves the other sections' edits and every action item's status; a full regenerate replaces the document.
- **KnowledgeBase** — a 401 rendered as "No articles found". An operator searching during an incident was told the runbook does not exist when their session had expired.
- **GlobalMap** — "Live metrics from AWS, Azure and GCP" were simulated from the time of day. Replaced with the one quantity a browser can measure: the HEAD round trip to each region's public endpoint, `null` and labelled unmeasured when the probe fails.

## Verification

- `ruff` clean; `mypy` clean over 405 files
- `pytest --cov=app --cov-fail-under=48` — **3373 passed, 57 skipped, 0 failed**, coverage **55.86%**; integration **10 passed, 20 skipped**
  <br>Run in a CI-matched venv (`uv sync --frozen`, Python 3.12, fastapi 0.138) with `REDIS_URL` exported, no `frontend/dist`, and no leftover sqlite files — the four conditions that separated a green local run from a red CI one.
- `tsc --noEmit` clean; `eslint` 0 errors
- `vitest` — **13 files, 78 tests passed**, coverage thresholds met
- `alembic upgrade head` verified against Postgres (head `019_postmortems`); `alembic check` reports no drift between migrations and models

## Note for review

`main` has been merged in — this branch is up to date with it, and there is nothing left to resolve. The merge was large (84 files, 282 hunks) and worth knowing the shape of before reading the diff.

The dominant cause was not the hardcoded-values sweep but PR #34 reviving four packages this branch had deleted as unreachable — `app/dashboard`, `app/cost_optimizer`, `app/dependency_graph`, `app/deduplication`. Those are restored here, along with main's router mounts. The ~20 files still unreachable on either side stay deleted. One deletion was reverted outright: `app/security/secrets.py` is reachable from `scripts/scan_staged_secrets.py`, which the pre-commit hook execs.

Two conflicts git could not surface as conflicts, because both sides only ever *added* files:

- **The migration chain had split into two heads** (`009_audit` and `019_postmortems`), so `alembic upgrade head` raised "Multiple head revisions are present" — the deploy path was broken on the merge result. Relinearised by repointing `002_oncall`'s `down_revision` at `009_audit`; main's chain is already released and possibly stamped in a live `alembic_version`, nothing from `002_oncall` onward has shipped. The four tables both sides created (`oauth_accounts`, `teams`, `team_members`, `team_invitations`) are now created once, downstream, by the revision whose version carries the constraints and the `org_id` FK.
- **`investigations.tokens_used` ended up with two accumulating writers** — this branch's history writer and main's dashboard writer — so a three-turn conversation reported five turns' worth of tokens. The dashboard writer now owns the column; it is the only one that runs on all five completion paths.

Also worth a look: main's six revisions declared 72 scalar `server_default=`s that their models declare Python-side, which `alembic check` reported as 71 columns of drift. Stripped, keeping every `server_default=sa.func.now()`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
