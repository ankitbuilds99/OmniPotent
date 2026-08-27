# Frontend feature audit — what works and what does not

Second-pass audit of the whole `frontend/` surface. Every page, hook, component and
API wrapper was walked feature by feature, each feature traced from the click that
triggers it through to the backend route and handler that answers it.

Commit audited: `4355b0e` (branch `fix/frontend-feature-audit`).

---

## 1. Verdict

**745 distinct user-facing features examined across 31 slices.**

| Status | Count | Meaning |
|---|---:|---|
| **Works** | **401** | Does what the UI says, end to end, against the real backend. |
| **Partial** | **146** | The happy path works; a specific input, failure mode or scope is wrong. |
| **Broken** | **130** | Does not work, or asserts something false. |
| **Cosmetic-only** | **66** | Renders, but is inert / dead code / unreachable. |
| Unverifiable | 2 | Needs a live third-party account to settle. |

By severity across the 196 broken + cosmetic findings: **19 critical, 75 high, 55 medium, 47 low.**

The short version: **the investigation console — the actual product — mostly works.**
The RCA report card, the approval gates, the agent activity trace, the MCP/model
sidebar, the runbook and incident and SLO and service-map read paths, notifications,
postmortems, and the whole `api.ts` request layer are in good shape.

What is broken clusters in four places:

1. **The two streaming transports both have a fatal defect** — SSE never parses a
   single frame, and WebSocket (the default) never records the investigation.
2. **Authentication is not actually enforced or reported honestly** — a wrong password
   signs you in, protected routes render for anonymous visitors, and Supabase tokens
   the backend cannot verify are treated as valid credentials.
3. **Billing has three field-name mismatches**, one of which crashes the whole app.
4. **The landing page states a large number of things that are not true**, including
   compliance certifications and a claim that directly contradicts what the product does.

---

## 2. Method, and two caveats you should know

31 auditor agents each took one slice of the frontend, enumerated *every* feature in it
(working ones included — not just defects), and cited a file:line for the frontend call
plus the backend route and handler for each one. Every defect claim was then handed to
an independent skeptic agent whose job was to *refute* it. **83 auditor claims were
downgraded or overturned by that pass** — including two that had been filed as critical
(the Dashboard data load and the marketplace GitHub connect, both of which turned out to
be correct on the shipped single-tenant default and are now listed as partial).

**Caveat 1 — 20 findings are unverified.** The skeptic assigned to the `landing-copy`
slice (Navbar, HeroSection, Pricing, Documentation, Blog, Footer) stalled on all six
attempts, so its 20 defect claims carry no refutation pass. They are marked
*(unverified)* below. The other 176 defect findings were all verified; several were
reproduced by running code against the project's own venv.

**Caveat 2 — the call-site index was incomplete.** The pre-computed list of frontend
request targets missed `api.ts` and `api/billing.ts`, so slices that leaned on it may
have under-counted call sites in those two files. The `api-layer` and `billing` slices
read the files directly and are not affected.

---

## 3. Critical — fix these first

### 3.1 SSE streaming has never worked in this build

`frontend/src/api.ts:69` splits the response buffer on `"\n\n"`. `sse_starlette` 3.4.5
(the installed version) emits `\r\n\r\n` — `DEFAULT_SEPARATOR = "\r\n"` at
`sse_starlette/sse.py:260`, and `app/api/main.py:2502` returns `EventSourceResponse(gen())`
with no `sep=` override. `"\r\n\r\n"` contains no `"\n\n"` substring, so the buffer never
splits, `frames.pop()` removes the only element, and the loop body never executes.
**`onEvent` is called zero times for an entire stream.**

The skeptic reproduced this against the project venv rather than reasoning about it.

Two features die: `chatStream` (the Console's SSE path, and the Onboarding wizard's
"Run the demo investigation") and `approveStream` (Approve/Reject when WebSocket is
unavailable — `app/api/main.py:2610`, same missing `sep=`). In the approve case the
backend *does* execute the approval, so the write is committed and the user is told
nothing.

Fix: split on `/\r\n\r\n|\n\n/` in `api.ts:69`, or pass `sep="\n"` on both
`EventSourceResponse` calls. Do both.

### 3.2 WebSocket is the default transport and it never writes history

`useCopilot.ts:45-53` defaults `copilot-websocket-enabled` to true, so a normal Console
conversation goes over `/ws/chat/{thread_id}`. The `investigations` row is written only
inside `_stream_chat_turn` (`app/api/main.py:2352`, `:2365`), which is reachable only
from `POST /chat/stream`. `app/api/websocket.py` never calls `investigation_history.record`
or `_record_dashboard_history_from_stream` — the skeptic grepped for a second writer and
found none.

Consequence: **run an investigation, then open History or the Dashboard, and it is not
there.** `GET /api/investigations` and `GET /api/dashboard/metrics` correctly report
zero. Combined with 3.1 this means investigation history is currently unreachable by
*either* transport.

### 3.3 The keepalive pong overwrites the answer with an error

`useWebSocket.ts:183-186` replies to the server's `ping` with `{type:"pong"}`.
`app/api/websocket.py:404-430` dispatches only `ping`, `chat` and `approval`; `pong`
falls to the `else` at `:425` and the server returns
`{"type":"error","message":"Unknown message type: pong"}`. Because `useCopilot.ts:213`
sets `currentAssistantId.current` and never clears it on completion, that error is
applied to the last completed turn — **30 seconds after every investigation finishes,
the answer is replaced by "Unknown message type: pong".**

Fix: add a `pong` no-op case server-side (one line), and clear `currentAssistantId` on
`done`.

### 3.4 A wrong password signs you in

`useSupabaseAuth.ts:132-139` — `signInWithEmail` destructures `{error: err}` from
supabase-js and does `if (err) setError(err)`. It never throws. `Login.tsx:156-158` is
`await signInWithEmail(email, password); onSuccess();` — unconditional. So a wrong
password, a nonexistent account, or a rate-limited attempt all call `onSuccess()` and
navigate into the app. `signUp` has the same non-throwing contract, so a failed signup
prints the green *"Check your email to confirm your account!"* banner for a mail that
was never sent.

Compare `resetPassword` at `useSupabaseAuth.ts:156-172`, which was deliberately written
to throw. The two sign-in functions just missed it.

### 3.5 Supabase tokens are not credentials this backend accepts

`adoptSession` mirrors the Supabase access token into the shared token store
(`useSupabaseAuth.ts:54-58` → `authToken.ts:36-38` → `apiClient.ts:26-29`), so every
subsequent API call carries it. But in the single-tenant branch — the shipped default,
`app/config.py:294` — the only JWT verifier tried is `verify_session_token`, which is
`jwt.decode(token, COPILOT_SECRET_KEY, algorithms=['HS256'])` with a required `sub`
claim. A Supabase RS256 token fails that. `render.yaml` sets `COPILOT_API_TOKEN` and
never sets `COPILOT_MULTI_TENANT`, so this is what a deployed instance does.

Related: `adoptSession(null)` on session expiry clears React state but leaves the dead
JWT in `localStorage.session_token`, and `getAuthToken()` prefers `session_token` over
`api_key` — so a stale session **shadows a working API key** and cannot be cleared from
the UI.

### 3.6 Billing: one field-name mismatch blanks the entire app

`PaymentMethodCard.tsx:20` reads `method.brand.toLowerCase()`. The backend sends
`card_brand` (`app/billing/models.py:577-588`). `undefined.toLowerCase()` throws during
render, the root `ErrorBoundary` catches it, and **the whole application is replaced by
the error screen** the moment a user with a saved card opens Billing → Settings.

Two more in the same family:
- **Checkout sends `email: undefined`** (`Billing.tsx:116-122`), so every org's billing
  row is created with an empty `stripe_customer` — checkout 400s.
- **Stripe's `success_url` is `/billing?success=true`** (`Pricing.tsx:424-431`), but the
  page is mounted at `/settings/billing` and the FastAPI SPA fallback refuses to serve
  `/billing`. After a customer actually pays, they land on a raw JSON 404. *(unverified)*
- **Invoice History** reads `invoice.number` / `invoice.amount_due`, neither of which
  the backend sends.

### 3.7 The "Public Status Page" 401s for the public

`App.tsx:147` routes `/status` with no auth guard and `StatusPage.tsx:2` calls itself a
public status page, but `/api/status` is in neither `_OPEN_PATHS` nor `_PUBLIC_PATHS`,
so `require_auth` (`main.py:1093`) rejects anonymous callers. **Runtime-proven:** with
`COPILOT_API_TOKEN` set, `GET /api/status` returns 401; with a Bearer header it returns
200. The email-verification link in the subscribe flow has the identical problem — the
subscriber is anonymous by definition, so clicking it always yields
`{"detail":"Missing or invalid API token."}`.

Worse, before data arrives `status === null` renders a green **"All Systems Operational"**
banner that no response produced — so during a real outage the status page asserts
everything is fine.

### 3.8 Runbook step types do not round-trip

`RunbookBuilder.tsx:537-539` declares `STEP_TYPES = {command, approval, condition, notification, wait, script, ai_action}`.
The backend enum (`app/runbooks/models.py:22-31`) is `{COMMAND, KUBERNETES, TERRAFORM, HTTP, SLACK, WAIT, CONDITION}`. Four of the frontend's seven do not exist server-side and four of the backend's seven are not in the frontend's set. Result:

- **Load any existing runbook and press Save** — every `kubernetes`, `slack`, `http` and
  `terraform` step is silently coerced to `command` and written back that way (`:561`, `:598`).
  Verified: this is data loss on an untouched round-trip.
- **Add Step → Run Script / AI Action / Request Approval / Send Notification** — all four
  templates 422 the entire save. Verified by executing `StepType('script')` etc., which
  raises `ValueError`.

Also in this file: the Wait step writes `duration_seconds` while the executor reads
`seconds` and falls back to a hardcoded 10, and **Save Draft creates the runbook with
`enabled=True`** — live and triggerable by the alert dispatcher — while the header shows
a grey "draft" pill whose tooltip says triggers will not fire.

### 3.9 On-call schedules are created empty with no way to fill them

`OnCall.tsx:918-928` posts `{team_id, name, timezone, rotation_type, layer}` and omits
`participants`, which `CreateScheduleRequest` accepts. `POST /oncall/teams/{id}/members`
and `POST /oncall/schedules/{id}/generate-shifts` both exist and appear nowhere in the
page. So a schedule created here has zero participants and zero shifts: the week grid
shows "—" in all seven cells forever, the Team Members card is permanently empty, and
the Create Override "Who will cover?" dropdown has no options.

### 3.10 The landing page asserts things that are false

`SecuritySection.tsx:83` renders **"No customer data stored on our servers"**. The
product persists the raw investigation prompt and the full RCA report server-side in the
`investigations` table (`app/database/models.py:136-200`) and serves them over
`GET /api/investigations`. The page states the opposite as fact.

Alongside it: **SOC 2 Type II, ISO 27001, GDPR and HIPAA badges** with no attestation
anywhere in the repo (the string "ISO 27001" does not appear in the backend at all), and
a **"SAML SSO & RBAC"** feature badge — RBAC is real, SAML does not exist.

These are legally consequential claims produced entirely by client-side literals. Fix by
deletion, not by wiring.

### 3.11 GlobalMap latency probes are blocked by the app's own CSP

`GlobalMap.tsx:154-163` fetches six third-party hosts to measure per-region latency.
`app/security/headers.py:66` sets `connect-src 'self' https://api.anthropic.com https://api.openai.com`.
CSP governs `fetch()` regardless of `mode:'no-cors'`, so all 12 probes are blocked in
production and the displayed latencies are whatever the fallback produces.

---

## 4. Root-cause clusters

Most of the 196 defects are instances of six recurring mistakes. Fixing the pattern is
cheaper than fixing the instances.

**A. Response field names read without checking the serializer (≈25 findings).**
`method.brand` vs `card_brand`, `invoice.number` vs `number`, `alert.source` never
serialized, `investigation.status`/`investigation.agents` always undefined,
`detail.incident.has_investigation` typed `boolean` but never sent. TypeScript declared
all of these as present, which is exactly why nobody caught them — the interfaces were
written from intent, not from the handler.

**B. Displayed facts with no request behind them (≈40 findings).**
"9 MCP Servers" (really 11), "48 AI Tools" (really 64), "99.9% Uptime SLA", "70% MTTR
Reduction", "15min Avg Resolution", Live Logs, CPU 68% / Memory 72%, Infrastructure
Health per-service bars, "24 Running containers", blog bylines and dates, "thousands of
engineering teams", the Role Permissions grid, the "6-Agent System" claims, "5x Faster
MTTR". Some sit behind dead code, most do not.

**C. Non-OK responses silently swallowed (≈20 findings).**
`Dashboard.tsx:587-597` has three `if (ok)` with no `else`; on-call escalation fetches,
analytics alerts fetch, marketplace status fetch, statuspage background refresh, and the
`/me` hook all do the same. The uniform symptom is a page that renders a confident empty
state during an outage: "0 connected", "No escalation policies", "All Clear", "All
Systems Operational".

**D. Optimistic local state that never reconciles (≈12 findings).**
Saved queries, notification toggles, knowledge-base stars, thumbs up/down, approval
cards. The UI commits, the write fails, nothing tells the user, and a reload reveals it.

**E. Only ever reading index 0 of a list (4 findings).**
`teams[0]` on the Team page, `teams[0]` on On-Call, `runbooks[0]` in the Runbook Builder
(which also makes the New-Runbook path unreachable, since five built-ins are seeded on
first GET), and the first escalation policy only. In each case the endpoint returns the
full list and the UI silently drops the rest with no switcher and no count.

**F. Dead code presented as product (≈20 findings).**
`components/Landing.tsx` (535 lines, superseded by `CinematicLanding`), all of
`FeedbackButtons.tsx` including `InlineFeedback`, both exports of `PromptSuggestions.tsx`
(the Trending / For You / Recent tabs), `Hero3D.tsx`, `FeatureGate.tsx`, `getThemeColors`,
three of five `config.ts` exports, and 11 of 21 `api/billing.ts` exports.

---

## 5. Suggested fix order

| # | Fix | Why here |
|---|---|---|
| 1 | SSE `\r\n\r\n` split (§3.1) | One line. Restores two whole features, one of which currently commits writes silently. |
| 2 | `pong` handler + clear `currentAssistantId` (§3.3) | One line each. Stops every answer being destroyed 30s later. |
| 3 | `PaymentMethodCard` `card_brand` (§3.6) | One line. Stops the app-wide crash. |
| 4 | Make `signInWithEmail` / `signUp` throw (§3.4) | Four lines. This is an auth bypass in behaviour if not in effect. |
| 5 | Record investigations from the WS path (§3.2) | Restores History + Dashboard analytics for the default transport. |
| 6 | Delete the false security/compliance claims (§3.10) | Deletion only; the legal exposure is the reason it ranks above larger bugs. |
| 7 | Reconcile runbook `STEP_TYPES` with `StepType` (§3.8) | Stops silent data loss on save. |
| 8 | Add `/api/status` + subscribe-verify to `_PUBLIC_PATHS` (§3.7) | Makes the public status page public. |
| 9 | Checkout `email` + `success_url` → `/settings/billing` (§3.6) | Unblocks paid conversion. |
| 10 | Auth-gate protected routes in `App.tsx` | Removes the whole class of "confident empty state while 401ing". |
| 11 | On-call participants + `generate-shifts` (§3.9) | Makes the on-call page usable at all. |
| 12 | Error branches on the ≈20 swallowed fetches (cluster C) | Mechanical, high value, easy to review. |
| 13 | Delete the dead components (cluster F) | Shrinks the audit surface for next time. |

---

## 6. Broken and cosmetic-only — the full list (196)

`CRIT`/`HIGH`/`MED`/`LOW` is severity. **broken** = does not do what it says; 
**cosmetic** = renders but is inert, unreachable or dead code. 
Items marked *(unverified)* are from the one slice whose refutation pass stalled.


### knowledge (frontend/src/pages/KnowledgeBase.tsx)

- **`MED` broken — Author shown on cards and in the viewer**  
  Every article created through this UI is stored with the literal author string "You", so the card shows "You" to every member of the org including people who did not write it. There is no request that produced a real identity — the page never reads the signed-in user (no /auth/me call anywhere in this file). Seeded docs show real authors ("Platform Team", app/knowledge_base/seeds.py:40), which makes the fake ones loo…

- **`MED` broken — Clearing the Service field while editing an article**  
  An explicit null is indistinguishable from an omitted field, so the store skips the assignment and keeps the old service. The PUT still returns 200 {"message":"Document updated"}, the editor closes as if it saved, and only the re-read at KnowledgeBase.tsx:728-736 makes the old service silently reappear in the Linked Service block. There is no way to unlink a service from the UI. (Editing service to a different non-em…


### runbooks - pages/RunbookBuilder.tsx

- **`CRIT` broken — Step type round-trip (load an existing runbook, press Save)**  
  `kubernetes`, `slack`, `http` and `terraform` are all valid server step types and none of them are in the frontend's STEP_TYPES set, so fromDetail coerces them to "command" and toPayload writes `step_type: "command"` back on the PUT. The seeded runbook the page loads first (`restart-service`, app/runbooks/models.py:217-260, ordered by created_at at store.py:282) is 2 slack steps + 2 kubernetes steps — all four are co…

- **`CRIT` broken — Add Step -> Run Script / AI Action / Request Approval / Send Notification**  
  Four of the seven step templates name types the backend enum does not contain. Adding one and saving returns `Step N: unknown step_type 'script'; expected one of ['command','condition','http','kubernetes','slack','terraform','wait']`. The whole runbook fails to save, not just the offending step — so one AI Action tile click makes every other edit on the page unsavable. Their editors (approvers list :274-296, notifica…

- **`HIGH` broken — Wait step "Duration (seconds)" field**  
  The editor writes the key `duration_seconds`; the executor reads the key `seconds` and falls back to a hardcoded 10. A wait configured as 120 seconds sleeps for 10 and reports "Waited 10 seconds". The StepCard summary (:142) prints `${step.config.duration_seconds || 30}s`, so the card confidently displays 120s for a step that will wait 10s — and displays "30s" for a freshly added wait step whose config is `{}` and wh…

- **`HIGH` broken — Condition step On Success / On Failure branching**  
  The two branching inputs and the green/red Success/Failure summary on the card (:170-181) write into local state that toPayload never serialises, so they vanish on the next load and read back as "End". Separately the Condition step type has no executor branch at all — app/runbooks/executor.py:277-292 dispatches only COMMAND, KUBERNETES, SLACK, WAIT, HTTP and falls through to `_refuse("Step type 'condition' has no exe…

- **`HIGH` broken — draft / enabled status badge**  
  Save Draft on a new runbook creates it server-side with enabled=True, i.e. live and triggerable by the alert dispatcher, while the header keeps showing the grey "draft" pill and a tooltip that explicitly states triggers will not fire it. The response body carries the true value and the code throws it away in favour of the local optimistic object. Publish only appears to work on this path by coincidence — the server w…

- **`HIGH` broken — Runbook selection / creating a new runbook**  
  The page always edits index 0 of the list and offers no way to choose another. Because the store seeds five built-ins on the very first GET, `runbooks.length > 0` is effectively always true, so the `EmptyRunbook()`/`isNew=true`/POST-create branch (:659-666) is unreachable in normal operation: the builder can neither create a runbook nor edit any of the other four. Route DELETE /api/runbooks/{id} (/tmp/routes.txt:159)…

- **`MED` broken — AI Action step editor and its "6-agent system" claim**  
  A hardcoded capability claim for a step type the backend does not model and the executor cannot run. No request produces this sentence and nothing in the runbook subsystem consults any agent system. The step itself cannot even be saved (422 from _build_steps).

- **`MED` broken — Approval step "Timeout (minutes)" default of 5**  
  A displayed default that is not stored and would be ignored if it were — approval gates in this backend wait indefinitely (store.set_execution_status parks the row in WAITING_APPROVAL, store.py:686-736, with no expiry). Moot in practice because approval steps cannot be saved at all.

- **`MED` cosmetic — Runbook description / service / execution_mode**  
  Three fields the server stores and acts on (service scopes which alerts match, store.py:293-297; execution_mode decides whether a run needs sign-off, store.py:827-831) are carried through the round-trip correctly but are not editable and not displayed. A runbook created here is permanently service="*" / requires_approval with an empty description.

- **`LOW` cosmetic — Variables panel (add/edit/remove variables)**  
  Genuinely writes nowhere, but the panel says so in plain text at :431-433 ("Not saved with the runbook yet — these apply to this editing session only"), so it does not misrepresent itself. Listed for completeness rather than as a lie.

- **`LOW` cosmetic — "Failed to load runbook" + Retry button**  
  Unreachable dead code — `runbook` is never null once loading is false, so the error state and its Retry button can never render. A genuine backend outage instead silently presents an empty "New Runbook" as if it were real, with only a console.error (:668) to distinguish it.


### dashboard (frontend/src/pages/Dashboard.tsx)

- **`HIGH` broken — Alert "Source" column / Recent Alerts sub-line**  
  The OncallAlert row does have a `source` column (set at app/oncall/api_router.py:928 from CreateAlertRequest.source), but the response model never serializes it. `alert.source` is therefore always undefined on the client, so the Source column and the overview sub-line print the literal "System" for every alert regardless of whether it came from Datadog, Prometheus or a manual POST. `team_id` (Dashboard.tsx:23) is lik…

- **`HIGH` broken — "Last updated: HH:MM:SS" freshness stamp**  
  The timestamp is a client-side `new Date()` that advances on every poll whether or not a single request succeeded. With all five endpoints returning 401/500, the header still reads a time seconds old while the numbers below it are stale or empty. It is a freshness claim no response produced.

- **`MED` cosmetic — Metrics tab**  
  The tab is a permanent static placeholder. Even on a deployment with PROMETHEUS_URL set — where /api/knowledge-graph/service-metrics returns real requests_per_sec / error_rate / latency_p99 per service, and the page is already fetching it every 30 seconds — the tab still shows "Connect to Datadog, Prometheus, or CloudWatch". Datadog and CloudWatch are not offered by this endpoint at all; only Prometheus is (app/knowl…

- **`MED` cosmetic — "View All Rules" button (Alerts tab header)**  
  Styled as an active blue link. Clicking it does nothing at all — no navigation, no request, no state change.

- **`MED` cosmetic — "Investigate" button (per alert row)**  
  Renders on every row next to two buttons that do work, so it reads as functional. It is inert.


### oncall - frontend/src/pages/OnCall.tsx

- **`CRIT` broken — Week calendar roster after creating a schedule here**  
  A schedule created through this page has zero rotation participants and zero shift rows. GET /oncall/schedules/{id}/shifts therefore returns [] forever, the grid shows "—" in all seven cells with the message "No shifts generated for this week.", and "Who's On-Call Now" shows "No active shifts". There is no button anywhere in the page to add participants or to trigger generate-shifts, so this state is permanent — the …

- **`CRIT` broken — Team Members panel / Override person dropdown**  
  `participants` is only ever populated from oncall_team_members rows, and this page never calls the endpoint that creates them. For any team created via the "New Schedule" modal the card permanently reads "No team members found" and the override dropdown contains only the "Select a person..." placeholder, which makes the Create Override flow impossible to complete.

- **`HIGH` broken — Week calendar vs. overrides**  
  Overrides live in a separate table and are applied only by get_current_oncall. The grid renders raw rotation shifts, so immediately after a successful override the "Who's On-Call Now" card names the covering person while the grid directly beneath it still names the original person for those same days. The page states two contradictory facts about who is paged.

- **`MED` broken — Escalation policy step numbers**  
  Every persisted step_number starts at 0, and `Number(0 ?? …)` keeps the 0. The numbered circles read 0, 1, 2 instead of 1, 2, 3, so the first escalation step of every policy displays as step "0".

- **`MED` broken — Create Override submit button (in modal)**  
  With any field empty the button is fully enabled, renders in the active accent colour, and clicking it does nothing at all: no request, no message, no modal close. The user has no way to tell the click registered.

- **`MED` cosmetic — Call / Slack contact buttons on the on-call person**  
  Nothing in the response carries a phone number or Slack id and toUser never sets those keys, so both guards are always false and the buttons never render. Even if they did render they have no handler, so they would be inert.

- **`MED` cosmetic — Escalation policy "Edit" button**  
  Styled as an accent-coloured action link and does nothing on click. There is also no PUT/PATCH escalation-policy route in the backend, so it could not be wired up as-is.

- **`LOW` cosmetic — rotation_type parsed but never displayed**  
  The rotation type is fetched from team_schedules (app/oncall/schedule_service.py:463) and normalised into three cases, then never rendered. Harmless dead state, but it means the page never tells the user whether the rotation is daily or weekly.


### team

- **`HIGH` cosmetic — Role Permissions legend (Owner/Admin/Member/Viewer capability grid)**  
  The grid asserts as fact: Owner = "Full access, billing", Admin = "Manage team, settings", Member = "Run investigations", Viewer = "View only". None of it is fetched, and two of the four claims are false. TeamRole is referenced nowhere outside app/teams/, so a team role gates only the /api/teams/* routes themselves. Real gating for running investigations and billing comes from the org role via require_perm (app/api/m…

- **`HIGH` broken — "Invitations for you" panel and Accept button**  
  Both routes exist and the response shape is read correctly (Team.tsx:175-176 reads body.team.name, which app/teams/router.py:508-514 does return). But _get_user_from_header yields email="" for any caller presenting COPILOT_API_TOKEN or a dcp_ tenant API key (app/teams/router.py:62/64). The pending list then queries on "" and returns [] (store.py:778), so the panel never renders; and accept hits `if not caller_email .…

- **`HIGH` broken — Member roster rows (name, email, avatar) under API-token auth**  
  The owner membership row is persisted with an empty email. The roster then renders an empty avatar circle ((member.name || member.email).charAt(0) on "" is ""), an empty bold name line, and an empty email line — a visually blank member row. Separately, every browser sharing COPILOT_API_TOKEN resolves to the same principal id "shared-api-token", so all users see and mutate one shared team as one identity.

- **`MED` cosmetic — Member display name and avatar image**  
  TeamMemberResponse declares name and avatar_url (app/teams/models.py:121-122) but they default to None and no handler ever sets them, so the API always sends null for both. The avatar <img> branch at Team.tsx:587 is unreachable dead code and member.name at :606 always falls through to the email. This is masked by the test suite, whose fixture invents name:'Owner'/'Bob' and avatar_url (pages/Team.test.tsx:33,44) — val…

- **`LOW` cosmetic — Validation-error messages on invite (422 handling)**  
  On a 422 the local errorDetail helper falls through its string check and returns the generic "Failed to send invitation (HTTP 422)", hiding which field was rejected — precisely the failure that most needs explaining. The shared describeFailure in apiClient.ts handles both shapes correctly; Team.tsx reimplements a strictly weaker version.

- **`LOW` cosmetic — Team name, member_count, joined_at and last_active are fetched but never rendered**  
  The heading is the constant string "Team Members" (Team.tsx:414), so the page never tells you which team you are looking at — which compounds the teams[0] problem above. member_count from the server is discarded in favour of members.length (:538), and the joined/last-active timestamps the backend already returns are dropped on the floor. Nothing is fabricated here; the data is simply unused.

- **`LOW` cosmetic — Team rename / delete**  
  Working backend endpoints for renaming and deleting a team have no UI on the only team-management page, so a team's name is fixed at creation and a team can never be removed from the product surface.


### slos - frontend/src/pages/SLOTracker.tsx (SLO Tracker page, mounted at /slo)

- **`HIGH` broken — Create SLO — "1 Day" window option**  
  Selecting "1 Day" sends window="rolling_1d". SLOWindow("rolling_1d") raises ValueError, so the handler raises HTTPException(400, "Invalid window: rolling_1d") on every attempt. The SLO is never created, and because the modal only closes on success (SLOTracker.tsx:827) the form just sits there. "30 Days" and "7 Days" map correctly to rolling_30d/rolling_7d and do work.

- **`HIGH` broken — Create SLO — failure feedback**  
  Two compounding problems. (1) The modal has no error region of its own, so the only feedback paints underneath the modal's 50%-black overlay at the top of the page, dimmed and un-clickable (the Dismiss button at SLOTracker.tsx:884 cannot be reached while the modal is open). (2) The 30-second poll calls setError(null) at line 720, so whatever error did appear silently vanishes within 30 seconds while the modal is stil…

- **`MED` broken — Active Alerts (card badge + detail-panel section)**  
  The SLOAlert interface (SLOTracker.tsx:13-19, with burn_rate / budget_low / budget_exhausted types and triggered_at timestamps) describes a capability no request populates. The "N active alerts" footer and the entire Active Alerts panel section are unreachable dead UI: an SLO whose burn-rate endpoint reports is_alerting=true, severity=critical (app/slo/tracker.py:620-623) still shows zero alerts on its card and no Ac…

- **`MED` cosmetic — Delete an SLO**  
  There is no delete control on the page. Combined with the blank-field defaulting above, an accidentally created "New SLO" for service "unknown" is permanent from the UI's point of view and keeps occupying a card and a summary count.


### incidents (frontend/src/pages/Incidents.tsx)

- **`HIGH` broken — AI investigation status chip on the detail card**  
  `investigation.status` is always `undefined`. The chip renders as an empty blue pill with no text for every investigation, running or finished. The page's own test only passes because the fixture invents the field (pages/Incidents.test.tsx:42 `status: 'completed'`).

- **`HIGH` broken — Root-cause panel / 'still running' explanation**  
  A running investigation displays the flat assertion 'The investigation finished without identifying a root cause.' It has not finished. `root_cause` is null until app/incidents/store.py:436 runs at completion, so this wrong sentence is what every in-flight investigation shows, on an incident page, to whoever is on call.

- **`HIGH` broken — Timeline panel**  
  Every real event the server records is invisible here: 'Acknowledged by X' (store.py:336-341), 'Resolved by X' (store.py:360-363), status changes (store.py:241-248), investigation_completed/failed (store.py:464-475), and free-text responder notes added via Slack /incident update (store.py:295-322). Acknowledge an incident in this very UI and the Timeline card below does not change.

- **`HIGH` broken — Start an investigation**  
  The page is titled 'Incident management with 6-agent investigation and safety gates' (Incidents.tsx:2) but has no way to start one. An investigation only ever appears if an alert webhook auto-investigated the incident (app/webhooks/router.py:210-225). An incident you declare through the Declare Incident modal can never get an AI investigation from this UI.

- **`MED` broken — Per-agent status grid (6-agent investigation)**  
  `investigation.agents` is permanently `{}`. The entire agent grid, AgentIcon (:163-166), AgentStatusIndicator (:168-173) and the progress/findings tooltip (:354-356) are unreachable code. The page banner (:2) advertises '6-agent investigation'; zero agent tiles can ever appear.

- **`MED` broken — Timeline entry 'AI investigation started · N agents'**  
  Always prints '0 agents' next to 'AI investigation started', for every investigation ever run.

- **`LOW` broken — 'The investigation did not recommend any actions' panel**  
  The condition can never be true, so this explanatory panel never renders. The operator just sees an investigation card with no Recommended Actions section and no explanation of why.


### analytics (frontend/src/pages/Analytics.tsx)

- **`HIGH` broken — Team Performance leaderboard (ranks, MTTR, incident/escalation counts)**  
  Every per-team number on the panel is a client-side literal, not a response field. All teams therefore display "0m MTTR" and "0 incidents | 0 escalations", and `sorted` at :259 sorts on `a.mttr - b.mttr` where every value is 0 — a stable no-op — so the gold/silver/bronze badges at :276-280 are assigned purely by the order SQLAlchemy happened to return rows in. A user reads "Team Platform is #1 by MTTR" when nothing w…

- **`HIGH` broken — Alert contribution to all metric cards and the Incident Trends chart**  
  Verified empirically: a `DateTime(timezone=True)` column on sqlite+aiosqlite round-trips to a naive datetime, and Pydantic emits it as `"2026-08-27T04:43:06.949453"` with no offset. Per the ES spec, `new Date()` parses an offset-less date-time form as LOCAL time, so Analytics.tsx:436 shifts every alert by the browser's UTC offset while incidents (correctly UTC-aware) are unshifted — two conventions in one dataset. Fo…


### servicemap

- **`HIGH` broken — Map canvas viewport for large graphs**  
  The layout stacks 5 nodes per row at 150px vertical pitch and the canvas is `overflow-hidden` with an absolutely-positioned child that contributes no intrinsic height — so there is no scrollbar. 26 services at one depth level is 6 rows, y = 110 + 5*150 = 860, already below an ~836px canvas. At the backend's default limit of 200 services the bottom row sits at y=5960px and is unreachable by any interaction the page of…

- **`LOW` cosmetic — Node type icon (Server / Database / Cache / Queue / Cloud)**  
  The icon is a label rendered as fact that no response field produced — it is guessed from substrings in the name. A service named `user-db-api` gets a Database icon, `aws-billing-service` gets an external Cloud icon, and a genuine Postgres registered as `primary-store` gets a generic Server icon. Nothing in the backend distinguishes these; every node returned is a Service.

- **`LOW` cosmetic — Detail panel slide-out/in animation**  
  The enter animation plays. The `exit` variant is dead code — without an `AnimatePresence` ancestor framer-motion unmounts immediately, so the panel vanishes rather than sliding out. Purely visual.


### notifications - pages/NotificationPreferences.tsx

- **`MED` broken — Config save silently reverts every other unsaved edit**  
  toggleChannel (:421-427) and toggleRuleChannel (:429-442) are local-only until Save Changes. Concretely: enable SMS, uncheck `incident_created via Email`, then paste a Slack webhook in Configure and Save. The PUT body contains only the slack entry, so save_preferences (app/notifications/preferences.py:335-368) persists slack alone; get_preferences returns SMS still disabled and the email rule still on; line 453 overw…

- **`MED` broken — Save-failure banner drops the field-level 422 details**  
  Empty time → prefs.quiet_hours.start = "" → PUT body `quiet_hours.start: ""` → the `_hhmm` validator (app/notifications/router.py:90-99) raises because "".split(":") has length 1 (verified) → 422. errorDetail matches `message` and shows: "Save failed — Request validation failed. Check the details for specific errors." The `details` array — the only thing that names quiet_hours.start — is never rendered, and no field …

- **`LOW` cosmetic — Event names shown as raw identifiers**  
  Not a correctness bug (the value is real and comes from the response), but the page's most prominent labels are snake_case internal ids while the readable text sits underneath in secondary colour.

- **`LOW` cosmetic — Configure modal keyboard dismissal**  
  Escape does nothing and focus is not moved into or trapped within the dialog, so keyboard and screen-reader users cannot close it the expected way.


### statuspage - frontend/src/pages/StatusPage.tsx (public status page)

- **`CRIT` broken — Anonymous access to the "Public Status Page"**  
  In any production deployment the token is mandatory, so GET /api/status returns 401 for a logged-out visitor. res.ok is false -> throw -> catch at StatusPage.tsx:120-123 -> the `error && !status` branch replaces the whole page with the red X screen "Unable to load status. Please try again." (StatusPage.tsx:231-247). The page only works for someone already signed in to the console, defeating its stated purpose. It app…

- **`CRIT` broken — Email verification link (completion of the subscribe flow)**  
  The subscriber is anonymous by definition (they typed an email into a public form), so clicking the link yields {"detail":"Missing or invalid API token."} with HTTP 401, never {"status":"verified"}. Verification therefore never happens, and api_router.py:318-327 states only verified subscriptions are ever notified - so the Subscribe form at StatusPage.tsx:330-355 can never produce a working subscription in production…

- **`HIGH` broken — Overall status banner before data arrives**  
  With status === null the page renders the full layout and a green banner asserting "All Systems Operational" that no response produced. During a real outage - exactly when people load a status page - the visitor is told everything is fine until the fetch resolves, and on a slow link that is seconds. The same paint also shows an empty Services list and the "No incidents in the last 7 days" empty state (StatusPage.tsx:…

- **`HIGH` broken — Silent failure of background refreshes**  
  Once one load has succeeded `status` is non-null forever, so every later failure is invisible: the page keeps showing the last-known snapshot with no staleness warning and no error. A user watching during an incident sees a frozen "All Systems Operational" that has not refreshed in hours. The Last-updated clock stops advancing, but nothing says why.

- **`LOW` cosmetic — Component grouping returned but never rendered**  
  The backend does the work of grouping components (store.py:73-79 assigns Core / Infrastructure / Services) and the page discards it, rendering one flat list. Not a wrong fact, just dead response data.


### billing (frontend/src/pages/Billing.tsx + components/billing/*, api/billing.ts)

- **`CRIT` broken — Payment Methods list (Settings tab)**  
  The backend sends `card_brand`, the component reads `method.brand`. `undefined.toLowerCase()` throws a TypeError during render, which the top-level ErrorBoundary catches and replaces the entire application with "Something went wrong". `last4`, `exp_month`, `exp_year` and `id` are equally absent (backend: card_last4/card_exp_month/card_exp_year/payment_method_id), so even without the throw every field would be blank. …

- **`CRIT` broken — Upgrade / Get Started Free button (Plans tab)**  
  Billing.tsx:118 passes `undefined` as the third argument (email), so JSON.stringify omits it and Pydantic receives None. Every org's billing row is created on first touch with an empty stripe_customer_id, so the very first checkout for any org hits the `Email required to create customer` branch, which billing_router.py:249 maps to 400 and the page shows "Email required to create customer (HTTP 400)" in the red banner…

- **`HIGH` broken — Invoice History list (Invoices tab)**  
  Four of the rendered fields are read under names the backend never sends. `invoice.number` -> undefined, so the invoice label renders empty. `invoice.amount_due` -> undefined, and formatCurrency does `undefined/100` -> NaN -> Intl renders the literal string "$NaN" as the invoice amount. `invoice.invoice_pdf` -> undefined so the Download-PDF link never renders. `invoice.id` -> undefined so every row shares key=undefin…

- **`HIGH` broken — "Alerts Processed" usage bar (Overview + Usage tabs)**  
  The bar always shows "0 / 100", "0.0% used", "100 remaining" no matter how many alerts the deployment has processed. The number is served by a real endpoint, but no writer for the counter exists anywhere in the product, so the displayed fact is structurally always zero.

- **`HIGH` broken — "Team Members" usage bar (Overview tab)**  
  Renders "0 / 1" (or 0 / max_users) with "0.0% used" for every org, forever, including orgs with a full team. The seat count shown is not connected to any user table; the summary column it reads is never populated.

- **`HIGH` broken — Usage History charts: API Calls & Postmortems Generated**  
  Both charts draw a flat line at zero (or "No data available" when the org has no summary row at all) regardless of real activity. The "Alerts Processed" chart at Billing.tsx:456-461 has the same problem. Only the AI Investigations chart is fed by a live writer.


### postmortem (frontend/src/pages/Postmortem.tsx)

- **`MED` broken — Unsaved section edits vs. any other action**  
  The in-progress text is component-local and never lifted. Switching to the Timeline or Actions tab unmounts SectionEditor and the typed text is gone; returning re-mounts it seeded from `section.content` (the last saved value). Separately, any successful PATCH elsewhere — publishing, adding an action item, flipping an item's status — runs applyDocument, which force-closes every open editor. No confirmation, no warning…


### marketplace - pages/IntegrationMarketplace.tsx + lib/integrationSpecs.ts

- **`MED` broken — "Saved as DD_API_KEY, DD_APP_KEY, DD_SITE" confirmation text**  
  Leave Site blank on Datadog and the success screen states "Saved as DD_API_KEY, DD_APP_KEY, DD_SITE". Only two requests were issued; DD_SITE was never sent. Worse on the "Replace credentials" path: `values` is initialised to `{}` (line 425) with no prefill, so a user re-entering credentials and leaving Site blank silently keeps the *previous* DD_SITE in the store — POST /admin/integrations is an INSERT OR REPLACE per…

- **`MED` broken — GET /integrations/status failure handling**  
  A 401/500 on GET /integrations/status silently produces `live === false` for every catalog entry, so genuinely-connected connectors render as "Available" and the header asserts "0 connected" as fact. No banner, no console warning, nothing distinguishes it from a real all-disconnected state. This is the one fetch whose failure directly changes a truth claim on screen, and it is the only one of the three with no error …

- **`LOW` cosmetic — "Stored as <ENV_NAME>" footer and per-field helper text**  
  Correct for the four connectors that go through /admin/integrations. False for GitHub: the modal says "Stored as GITHUB_TOKEN" / "Stored as GITHUB_REPO" (integrationSpecs.ts:170-184 + IntegrationMarketplace.tsx:537-540) but that flow POSTs /github/connect, which stores nothing under those names — app/api/main.py:1842 writes the in-memory `_ov` dict, not the integration_secrets table. Also note the footer lists the na…

- **`LOW` cosmetic — Refresh button**  
  The data does refresh. There is zero feedback that anything happened: no spinner (the one at :813-817 is gated on `loading`, which is false after first load), no disabled state, no toast. Rapid clicks fire overlapping request triples whose responses can land out of order.

- **`LOW` cosmetic — Card logo images**  
  Cards degrade cleanly on a dead logo URL, and several in the catalog look dead (the GCP entry at :183 is a favicon URL with an obviously fabricated 40-char path segment, and the Bitbucket/Confluence Atlassian `jcr:` UUIDs at :160 and :229 are the kind that rot). The modal has no such guard, so the same URL renders the browser's broken-image glyph next to "Connect Bitbucket".


### apidocs-profile (frontend/src/pages/ApiDocs.tsx, frontend/src/pages/Profile.tsx)

- **`HIGH` broken — Account identity load / Guest Mode panel**  
  Three wrong things render at once for a genuinely authenticated user: the amber banner at pages/Profile.tsx:170-186 ("Could not read your account from the server: Invalid or expired token (HTTP 401)"), the heading "Not signed in" with a "?" avatar (pages/Profile.tsx:213,221), and the "Guest Mode" panel at pages/Profile.tsx:366-386 saying "You're using OmniPotent without authentication". This is the documented single-…

- **`LOW` cosmetic — Account-error banner / stale data on a failed refetch**  
  On a second, failing load the banner text at pages/Profile.tsx:182-184 asserts "Anything it alone knows — your role in particular — is not shown below", yet the Role (pages/Profile.tsx:273) and Organization (pages/Profile.tsx:286) rows still render the previous response's values. The page contradicts itself and keeps presenting stale server-derived permissions as current.


### auth-pages (pages/Login.tsx, pages/AuthCallback.tsx, pages/AcceptInvite.tsx)

- **`CRIT` broken — Sign in with email + password (Supabase configured)**  
  A wrong password makes Supabase return `{error}`, the hook swallows it into React state, `signInWithEmail` resolves normally, so Login unconditionally calls `onSuccess()` -> App.tsx:41-52 `afterLogin` navigates to /console. No session token is ever written (authToken.ts:41), so the user is sitting in the console fully unauthenticated and the error message they should have seen is unmounted by the navigation. The same…

- **`HIGH` broken — Create account (signup) — success message**  
  Any signup failure (email already registered, weak password, rate limit) is swallowed by the hook, so the green "Check your email to confirm your account!" banner is printed for a mail that was never sent. Because `error` is `localError || authError?.message` (Login.tsx:59) and the two banners are independent (Login.tsx:441-470), the user is shown a green success and a red error simultaneously. Note Login.tsx:163-170…

- **`HIGH` broken — Sign in / Create account on a deployment without Supabase**  
  With no Supabase config, the form labelled "Sign in to OmniPotent" / "Start your free trial" performs zero verification and zero account creation: it writes `{email,name}` to localStorage under `demo_user` and navigates to the console. No credential is minted, so authToken.getAuthToken() stays empty and every subsequent API call goes out with no Authorization header — 401 on any deployment with COPILOT_API_TOKEN or C…

- **`HIGH` broken — "Try Demo (No Login Required)" button + "Full access to all features with sample data"**  
  The button sets no auth token, so the console it navigates to is anonymous. The claim "Full access to all features with sample data" is a displayed fact no response produces: the only demo data path in the repo is the server-side env var COPILOT_DEMO_MODE (app/config.py:184, app/coordinator/handlers/remediation.py:49), which a browser click cannot set and which app/config.py:625-627 forbids in production. On a real d…

- **`HIGH` broken — OAuth login completing an invitation (AuthCallback -> pending invite)**  
  The entire pending-invitation handoff is bypassed for the OAuth path (the only sign-in path that actually produces a credential on a real deployment): after GET /auth/{provider}/callback (/tmp/routes.txt:236) redirects to /auth/callback#token=…, the user lands on the console and POST /api/teams/invitations/{id}/accept is never issued. The invitation stays pending until it expires, and the stale `pending_invitation` k…

- **`MED` broken — Invitation acceptance for a caller authenticated by API key / shared token**  
  Any credential that is not a JWT carries no email, so the accept always returns 403 "This invitation was issued to a different email address." — which AcceptInvite.tsx:62-68 faithfully renders. The message is factually wrong for that user (their address may match perfectly; the credential simply has no email claim), and there is no way out of it from this page.

- **`MED` broken — Invitation sign-in loop (token-less login paths)**  
  AcceptInvite re-runs, `getAuthToken()` is still empty, so it re-stores the pending key and shows "Sign in to join" again — an unbreakable loop with no error and no way to redeem the invitation. Only the Supabase or backend-OAuth paths break the cycle.

- **`MED` broken — OAuth failure/denial message on /auth/callback**  
  The `error` query parameter the backend deliberately forwards is never read. On any deployment that uses backend OAuth rather than Supabase, every cancelled or failed OAuth login reports the unrelated and untrue message "Supabase is not configured" for 2 seconds and then bounces to /login, hiding the real provider error.

- **`LOW` cosmetic — Terms of Service / Privacy Policy links**  
  Both links navigate nowhere (they just append '#' to the URL). The sentence above them, "By continuing, you agree to our Terms of Service and Privacy Policy", references documents the user cannot read.


### history

- **`HIGH` broken — "Continue" button on an expanded investigation**  
  The URL is pushed and the Console mounts, but useCopilot ignores `?thread=` entirely and restores whatever conversation is in localStorage. The user clicks "Continue" on a three-week-old investigation and silently lands in their current/last chat thread, with the old thread never resumed. Nothing signals the failure.

- **`HIGH` broken — Filter-this-page search box**  
  Any row whose `query` is NULL (see the approval-flow finding below — that path only ever runs the analytics writer, which does not set `query`) makes `inv.query.toLowerCase()` throw a TypeError inside render. There is a root ErrorBoundary (frontend/src/main.tsx:11), so the entire app is replaced by the error screen the instant the user types one letter. The list itself renders fine until then, so the crash looks unre…

- **`HIGH` broken — Investigation row title / summary / report for approval-gated runs**  
  The comment at app/api/main.py:2269 claims it "upserts onto the row the original /chat turn created", but no such row exists when the first turn ended in awaiting_approval. The resulting history entry has a blank headline, no summary, Latency "-" (latency_ms is never written by the analytics writer), and an empty `{}` report. Every human-in-the-loop investigation — the product's flagship flow — is recorded as an anon…

- **`HIGH` broken — Supabase data source for the history list**  
  With Supabase configured, the page bypasses the backend endpoint entirely and reads Supabase directly. `auth.jwt() ->> 'org_id'` is NULL for every Supabase-issued token this app produces, so the policy predicate is NULL for every row and the select returns zero rows — the page shows "No investigations yet" while real history sits in the backend's own database. If the backend's DATABASE_URL does point at that same Pos…


### queries (SavedQueries.tsx, PromptSuggestions.tsx, useSavedQueries.ts)

- **`HIGH` broken — Saved Queries — server persistence (whole feature)**  
  Saved queries are per-browser-profile only. They vanish on cache clear, incognito, or a different device, and are not associated with the logged-in account. The panel's title bar and empty state ("Click the bookmark icon when composing to save", SavedQueries.tsx:141) present it as an account feature with no indication it is device-local.

- **`HIGH` broken — Delete a saved query (trash icon)**  
  The delete visibly removes the row and does persist — until the next save from the composer, which writes Composer's stale [A,B,C] array back over localStorage. Concretely: delete B → close → type a new query → click save → reopen panel → B is back. The same stale array also loses any usedCount increments the panel recorded.

- **`HIGH` broken — Clear All button**  
  Clears the list in the modal and in localStorage, but the very next composer save rewrites all up-to-20 previously-cleared queries back into localStorage. Additionally there is no confirmation dialog on a destructive bulk delete, and Composer.tsx:79 `title={`Saved queries (${queries.length})`}` still reports the pre-clear count until a full page reload.

- **`HIGH` broken — Modal card background / secondary text color**  
  `var(--surface)` with no fallback is an invalid value, so the `background-color` declaration on the modal card is dropped and the card renders fully transparent over the 50%-black scrim — the page content behind it shows through the query list. Every `var(--text-2)` is likewise dropped, so all "secondary" text (close X, empty-state copy, "Used Nx", the footer count) inherits `--text` from body (index.css:197-200) and…

- **`HIGH` broken — PromptSuggestions "Trending" tab**  
  A tab labelled "Trending" with a TrendingUp icon (:198) implies these are the queries currently popular across the org. They are three invented constants, and they name services (`checkout-service`, `payment flow`) that no request confirmed exist in this deployment. This is a displayed fact no response produced.

- **`MED` broken — "Used {n}x" counter on each saved query**  
  The label reads as "this investigation has been run N times" but counts only local save-clicks and panel-click-to-fill actions. A brand-new query that has never been sent immediately displays "Used 1x". Clicking a row to fill the textarea bumps it to "Used 2x" even if the user then deletes the text and never sends. Queries the user actually runs dozens of times from the composer never increment at all.

- **`MED` broken — Delete / Edit icon buttons on touch devices**  
  The only affordance for edit and delete is a hover-revealed control group. With no pointer hover, the buttons are invisible; and tapping the row to "reveal" them fires the row's own onClick, which calls handleSelect → onClose and dismisses the entire modal. Delete and rename are therefore unreachable on touch. Clear All (:256) is the only remaining destructive control.

- **`MED` broken — localStorage write failure handling**  
  This is the classic optimistic-local-state defect: React state is updated and the UI shows the query as saved even when the persistence write threw. The user sees the item in the list and the count go up, reloads, and everything is gone with no error ever surfaced.

- **`MED` broken — PromptSuggestions component (whole component)**  
  Both exports (`PromptSuggestions` and `InlineSuggestions`) are dead code. Every feature below it — the four tabs, the AI/context suggestions, the recent-query list — is unreachable by any user. It has been superseded by the simpler inline button list in Console.tsx.

- **`MED` broken — PromptSuggestions "Recent" tab**  
  The Recent tab is fed by a prop with an empty-array default and no wiring to the backend history that already exists. Worse, the tab itself is only added when `recentSuggestions.length > 0` (:199-201), so `activeTab` can never be `"recent"` while the list is empty — which makes the entire "No recent queries yet / Your investigation history will appear here" empty state at :259-265 unreachable dead code.

- **`MED` broken — PromptSuggestions "For You" (AI) tab**  
  Labelled "For You" with a Sparkles icon and per-item descriptions "Based on your current context" / "Active alert detected" / "Recent incident", implying live signal from alerts and incidents. There is no request behind any of it — the strings are template-interpolated from a prop nobody passes, so the tab never renders at all. Live data for this does exist server-side (`GET /oncall/alerts`, `GET /api/incidents` in /…

- **`LOW` broken — Corrupt-storage handling on load**  
  A non-array value parses successfully, skips the catch, and then throws `queries.filter is not a function` during render of `useSavedQueries`. Because the hook is mounted by Composer (Composer.tsx:18), which is always rendered inside Console (components/Console.tsx:224,255), that throw takes down the entire chat console, not just the modal.

- **`LOW` cosmetic — PromptSuggestions quick-start cards + hover styling**  
  The click handler itself is correctly wired and would hand the literal text to the parent's onSelect — but the component is never mounted, so it is unreachable. The card descriptions ("See recent deployments and config changes", "Query times, connections, locks") are static copy, not capability discovery. Separately, the inline `backgroundColor` on line 130 has higher specificity than the Tailwind `group-hover:bg-blu…

- **`LOW` broken — InlineSuggestions chips**  
  Unreachable dead code, and one of the four chips ships an un-substituted placeholder: clicking "Why is X slow?" would send the literal token X to the investigation backend rather than a service name. There is no templating or service-picker behind it.


### admin-config (ModelConfig, AdminPanel, SourceConnect, GithubConnect)

- **`MED` cosmetic — Admin console Organization card**  
  org.name, org.plan, org.members, org.active_api_keys and org.integrations are all real counts from the tenant store — no fabricated numbers. The defect is the diagnosis: /admin/org is gated by require_perm("view"), which every role including viewer satisfies (app/tenancy/models.py:18), so a permission failure is nearly impossible here. The failures that DO happen — 404 "org not found" (app/api/main.py:3111), 400 "Adm…

- **`MED` broken — Auto-reconnect error handling**  
  The comment says "Clear invalid credentials so user knows to re-enter", but the catch fires on ANY failure — backend not yet up, a 502 because GitHub was briefly unreachable, or a 403 because the signed-in role is not admin — and permanently deletes the saved token from localStorage without setError() and without any visible message. The user reloads once at the wrong moment and their saved token is gone with no expl…

- **`LOW` cosmetic — Admin console modal focus trap**  
  The comment claims a focus trap; the effect implements only Escape-to-close and body scroll lock. modalRef is attached to the dialog div and never read by anything. Focus is never moved into the dialog on open and never restored on close, and Tab escapes to the page behind the aria-modal overlay.


### shell-ui (Sidebar, ThemePicker, SignupModal, ErrorBoundary, Skeleton)

- **`HIGH` broken — Signup stores the key but a stale session_token silently shadows it**  
  getAuthToken() prefers `session_token` over `api_key`. useSupabaseAuth only clears session_token in the explicit signOut path; on a SIGNED_OUT/expiry event it calls adoptSession(null), which skips setSessionToken entirely, leaving a dead JWT in localStorage. Console shows the "Sign up" button precisely in that state (Console.tsx:82, isLoggedIn = !!user). The user signs up, SignupModal writes the fresh dcp_ key, but e…

- **`LOW` cosmetic — Signup modal focus trap (claimed, not implemented)**  
  Tab walks straight out of the dialog into the top bar and sidebar behind it while aria-modal="true" tells assistive tech nothing else is reachable. Also, Enter submits only from the email input (line 98); pressing Enter in the Organization name field does nothing.


### useWebSocket - real-time chat transport (frontend/src/hooks/useWebSocket.ts)

- **`CRIT` broken — Server heartbeat / pong reply (keeps the connection alive)**  
  Every 30 seconds the client politely pongs and the server answers with an error event that the UI renders as the assistant's turn. Because useCopilot.ts:213/303 sets `currentAssistantId.current` and only clears it in newConversation (:358), the target is the last assistant turn — so a completed, correct answer is silently replaced by the literal text "Unknown message type: pong" 30 seconds after it arrives. Mid-inves…

- **`HIGH` broken — Session resumption after a dropped connection**  
  The feature advertised at useWebSocket.ts:7 ("Session resumption after reconnection") never fires. After any reconnect the server mints a fresh session id and every trace line, approval request and final answer emitted while the client was away is permanently lost — the user sees the investigation resume from nothing.

- **`HIGH` broken — Detecting that the server has WebSocket disabled (the `available` flag)**  
  `available` (documented at :57 as "Whether WebSocket is available (server supports it)") is a hardcoded `true` that no server response can ever change. Consequences: useCopilot.ts:170-174 (`if (!wsServerAvailable) setWsAvailable(false)`) never fires, and instead of bailing out immediately the hook treats the 1006 as a transient drop and burns 5 backoff retries (~31s at 1/2/4/8/16s plus jitter, :113-118, :211-218) aga…

- **`HIGH` broken — Automatic fallback to SSE when WebSocket fails**  
  The message is queued and then orphaned. The assistant turn stays `status: "thinking"` and `busy` stays true forever — spinner with no error, no retry, no SSE. This is reachable whenever the socket dies after `sendViaWs` returns, and also on the load-time race where `wsAvailable` is still `null` (useCopilot.ts:80, probe at :86-92) so `wsAvailable !== false` passes and WebSocket is chosen on a deployment where it cann…

- **`MED` cosmetic — WebSocket on/off toggle and connection-status indicator**  
  There is no rendered connection indicator and no control to disable WebSocket. The `copilot-websocket-enabled` localStorage key (useCopilot.ts:13, 45-62) defaults to true and nothing ever writes it, so a user hitting the pong bug or a hung socket has no way to switch to SSE. All of the hook's status/availability plumbing is unreachable from the UI.


### useCopilot - the copilot conversation hook (frontend/src/hooks/useCopilot.ts) and its only consumer, components/Console.tsx

- **`CRIT` broken — Send a question (WebSocket transport — the default) when the socket cannot be established**  
  If connect() never reaches "connected", the queued message is never sent, `busy` stays true forever, and the assistant turn stays `status: "thinking"` — Message.tsx:27,35 renders that as a permanent spinner and Console.tsx:155 `disabled = busy || awaitingApproval` keeps the Composer disabled. There is no timeout, no error event, and no fallback to SSE for the in-flight message, despite the hook's own docstring (useCo…

- **`CRIT` broken — Investigation history / dashboard analytics for copilot conversations**  
  Because useCopilot picks WebSocket first and copilot_websocket_enabled defaults to True (app/config.py:480), the normal Console conversation goes over /ws/chat/{thread_id}, which never inserts the investigations row. The History page and GET /api/dashboard/metrics therefore report zero investigations for work the user actually ran and was billed for (_meter_and_spend IS called at app/api/websocket.py:504). The same c…

- **`HIGH` broken — Stop button during a WebSocket investigation**  
  Two separate failures. (1) The assistant turn keeps `status: "thinking"`, so ActivityTimeline.tsx:50-56 shows the "working…" spinner forever even though the Composer re-enables. (2) The investigation is not actually stopped — closing the client socket does not cancel the server generator, so the run completes and app/api/websocket.py:504 `await _meter_and_spend(ev.get("tokens_used", 0))` still charges the user's quot…

- **`HIGH` broken — Stop button while the WebSocket is still connecting**  
  The cancelled question is sent to the server anyway a moment later, and its trace/done events are applied via handleWsMessage (useCopilot.ts:144-152) into the turn the user just stopped — so a stopped investigation restarts itself and quietly fills in an answer while `busy` is already false. stop() never clears wsMessageQueue.current.

- **`MED` cosmetic — WebSocket toggle, connection-status indicator and availability flag**  
  toggleWebSocket() and the saveWsPreference/loadWsPreference localStorage machinery (useCopilot.ts:44-62, 361-368) are unreachable: nothing ever calls them, so `copilot-websocket-enabled` is never written and useWs is permanently the `true` default. The user has no way to opt out of the broken WebSocket transport and no indicator that a connection is failing. Separately, useCopilot.ts:384 returns `wsAvailable ?? false…

- **`MED` broken — WebSocket session resumption after a dropped connection**  
  The feature advertised at useWebSocket.ts:7 ("Session resumption after reconnection") cannot fire: the reconnecting client sends no session_id, and even if it did the server buffered nothing for it. Every trace/approval/done event emitted while the client was away is lost, so a copilot turn that survives a brief network blip comes back with a truncated trace or hangs in "thinking" forever with no done event.


### console (Console.tsx, Composer.tsx, Message.tsx)

- **`HIGH` broken — Stop button (cancel an in-flight investigation)**  
  WebSocket is the default transport (useCopilot.ts:45-53 loadWsPreference defaults true; useCopilot.ts:266 shouldUseWs). Clicking Stop over WS never sets the turn's status away from "thinking", so ActivityTimeline keeps spinning forever (Message.tsx:27,35) and the persisted turn is only downgraded to an error on the next page load (useCopilot.ts:30-34). The SSE path handles this correctly (useCopilot.ts:188-195 rewrit…

- **`HIGH` broken — Investigation is recorded to history / dashboard analytics**  
  On a default install every console investigation goes over the WebSocket and therefore never lands in the investigations table. GET /api/investigations and GET /api/dashboard/metrics then legitimately return empty/zero, so History reads as "you have never run an investigation" immediately after you ran one. The console itself gives no indication that its transport determines whether the run is persisted.

- **`MED` broken — WebSocket session resumption after a drop**  
  The reconnect always dials /ws/chat/{thread_id} with no session_id, so manager.get_pending is never consulted and every event buffered by manager._queue_missed (app/api/websocket.py:175-196) during the outage is discarded. The turn that was in flight stays at status "thinking" with a partial trace and never receives its done event. The server-side buffering machinery is correct; the client simply never asks for it.

- **`LOW` cosmetic — Avatar / Profile button**  
  Navigation works (App.tsx:98 onProfile -> navigate('/profile')). The rendered letter is real when /me succeeds, but hooks/useMe.ts:6-12 falls back to a hardcoded {authenticated: true, role: "owner", label: "Operator"} on any error, so a 401 or a down backend still paints an "O" avatar that asserts an authenticated owner identity no response produced.

- **`LOW` cosmetic — Saved queries panel (history icon)**  
  Selection populates the textarea correctly. Two real defects: (1) the panel mounts its own useSavedQueries instance, so deleting or Clear-All inside it does not update the Composer's copy — the tooltip at Composer.tsx:79 keeps showing the pre-deletion count until the Composer remounts; (2) handleSelectSaved (Composer.tsx:41-48) calls autoGrow(ref.current) in the same tick as setValue, so scrollHeight is measured agai…


### rca-feedback (FeedbackButtons.tsx, RcaReportCard.tsx, ApprovalCard.tsx, ActivityTimeline.tsx)

- **`HIGH` cosmetic — FeedbackButtons — whole component (thumbs up/down + modal)**  
  The entire file is unreachable. It is never rendered anywhere in the app. The live feedback control is a *different*, separately-implemented pair of buttons in components/Message.tsx:79-109, which calls Console.tsx:246 sendFeedback -> hooks/useCopilot.ts:333-340 -> api.ts:131-138 `post("/feedback", {thread_id, rating, comment, question})`. So this file is a duplicate dead implementation that a reader (or a future dev…

- **`HIGH` broken — FeedbackButtons — issue-category chips (Incorrect / Missing / Not relevant / Hard to understand / Too slow / Other)**  
  The six categories are invented UI taxonomy with no server-side representation. Even if the component were wired to api.ts:131 submitFeedback(), that function's signature is (threadId, rating, comment, question) — there is nowhere to put `categories`, and the JSONL record written by app/feedback.py:37-43 has no such key. Every category the user selects is silently discarded.

- **`MED` broken — FeedbackButtons — "Thanks for your feedback!" confirmation**  
  A success confirmation that no response produced. `onFeedback` is an optional prop (:7) — when it is undefined (which is always, since the component has no callers) the click does literally nothing except swap the UI to a green check + "Thanks for your feedback!". This is the classic fabricated-success pattern: the UI asserts a fact ("your feedback was recorded") that no request ever established.

- **`MED` cosmetic — FeedbackButtons — "Additional details" comment textarea + Submit Feedback button**  
  The comment is collected into React state and handed to an optional callback that no caller supplies. Nothing is persisted. Note the shape mismatch too: this component emits `{helpful: boolean, comment?, categories?}` (:11-15) while the backend and api.ts expect `rating: "up"|"down"` (app/api/main.py:1288) — so even a naive wiring would need a translation layer that does not exist.

- **`MED` broken — FeedbackButtons — `responseId` per-response keying**  
  There is no per-response/per-turn feedback identity anywhere in the backend. `responseId` has nowhere to go. The live path confirms this: useCopilot.ts:335 sends the *current* `threadId`, so rating an older turn in a multi-turn conversation records feedback against the whole thread, not that turn.

- **`MED` cosmetic — FeedbackButtons — InlineFeedback (hover thumbs for chat messages)**  
  Second dead feedback widget in the same file. Same fabricated-confirmation pattern as the main component.

- **`MED` broken — ActivityTimeline — emoji→SVG icon mapping (report step leaks a raw emoji)**  
  The file's own header comment (ActivityTimeline.tsx:1-4) states the contract: "we map that emoji to an SVG icon and strip it from the text (no emoji-as-icon)". That contract is violated on every single completed investigation, because the report node runs unconditionally at the end of every run (app/graph/builder.py:163, and :186 `g.add_edge("report", "verify")`). The user sees a raw 📝 glyph inline in the text plus a…

- **`LOW` cosmetic — ApprovalCard — `disabled` prop (buttons/input lockout)**  
  The prop is dead — `disabled` is always `undefined`, so the buttons are never actually disabled and there is no in-flight visual feedback on the card itself. The un-persisted, un-guarded control means a rapid double-submit sends a second POST /approve/stream, which the backend answers with `{"type":"error", ... "no investigation awaiting approval for thread ..."}` (app/api/main.py:2545-2556). In practice the card unm…


### auth-hooks (frontend/src/hooks/useSupabaseAuth.ts, frontend/src/lib/supabase.ts, frontend/src/lib/authToken.ts)

- **`CRIT` broken — Supabase session becomes the app's API credential**  
  In the single-tenant branch (app/api/main.py:655-671) the only JWT verifier tried is `verify_session_token`, which is `jwt.decode(token, COPILOT_SECRET_KEY, algorithms=['HS256'])` plus a required `payload['type'] == 'session'` claim (app/auth/oauth.py:481-483). A Supabase access token is signed with the Supabase project key and carries no `type: session` claim, so it fails. `app/tenancy/supabase_auth.verify_jwt` — th…

- **`CRIT` broken — Email/password sign-in**  
  `signInWithEmail` never throws: supabase-js returns `{data, error}` for bad credentials, and line 138 does `if (err) setError(err)` and returns normally. Login.tsx:157-158 is `await signInWithEmail(email, password); onSuccess()` with no error check, so onSuccess() (App.tsx `afterLogin` -> navigate('/console')) fires on a wrong password too. The user is navigated into the console with no token stored, and the `error` …

- **`HIGH` broken — Sign-up ("Create account")**  
  Same non-throwing contract. `signUp` swallows the failure into `setError` (line 153), so Login.tsx:161 unconditionally sets `success = 'Check your email to confirm your account!'` and flips to login mode. On a duplicate email, a rejected password, or a null client (line 142 `if (!client) return`, i.e. no request issued at all), the user is told mail is on its way that was never sent. Because Login.tsx renders the err…

- **`HIGH` broken — Signed-in / signed-out UI state (TopBar Logout, Profile, Sign up buttons)**  
  `user` is populated exclusively from `supabase.auth`. On the shipped default deployment `GET /frontend-config` returns `{"supabase_url":"","supabase_anon_key":"","api_url":""}` (app/api/main.py:1688-1693) unless SUPABASE_URL is set, so Supabase is unconfigured and `user` is permanently null. A user genuinely authenticated by the backend OAuth session token — which the app stores under the same `session_token` key (fr…

- **`HIGH` broken — Stale-session eviction when a Supabase session ends or expires**  
  `adoptSession(null)` clears React `user`/`session` but leaves the dead JWT in localStorage under `session_token`. `getAuthToken()` (authToken.ts:36-38) keeps returning it, so Login.tsx:98 `if (getAuthToken()) { onSuccess(); return null }` bounces the user straight to /console every time they try to reach the login form — while every API request carries the expired token and 401s. There is no code path that evicts a c…

- **`LOW` cosmetic — `api_url` field returned by /frontend-config**  
  The third field of the response is read by nothing. The endpoint's stated purpose (app/api/main.py:1658-1660, "runtime injection of environment variables that weren't available at Docker build time") is therefore only half-implemented: Supabase creds are injectable at runtime, the API origin is not.

- **`LOW` cosmetic — `supabase` module-level export**  
  A permanently-null binding that looks like a live client. Harmless today only because nothing imports it — History.tsx:67 and AuthCallback.tsx:56 both correctly use `await initSupabase()` instead. A future import of `{ supabase }` would fail with no error message.

- **`LOW` cosmetic — `getSupabase()` / `isSupabaseConfigured()` / `isAuthenticated()`**  
  Dead surface. `isSupabaseConfigured()` is additionally a trap: unlike its async sibling at supabase.ts:95-98 it does not await the runtime config fetch, so it returns false on any Docker build until the fetch lands. `isAuthenticated()` is the predicate Console.tsx:183 should be using instead of `!!user` — the fix for the top-bar bug is already written and unwired.

- **`LOW` broken — Test coverage for the auth hook and Supabase client**  
  The two files carrying every defect above — the non-throwing sign-in/sign-up contract, the missing null-session token clear, the swallowed config fetch — have zero direct tests, while the one file that is fully tested (authToken.ts) is the one file with no correctness defects. Because Login.test.tsx mocks `useSupabaseAuth`, the "wrong password navigates to the console" path is invisible to the suite.


### onboarding - frontend/src/components/OnboardingWizard.tsx (5-step setup wizard mounted at /onboarding, App.tsx:83-89)

- **`LOW` cosmetic — Playground cancel / abort**  
  The AbortError branch is unreachable dead code. Worse, because nothing aborts on unmount, clicking a completed step in the progress bar mid-run leaves the SSE fetch streaming into an unmounted component, holding a backend agent turn and its MCP subprocesses open for the rest of the run.

- **`LOW` cosmetic — Integration status load failure state**  
  A permanent failure renders as a permanent in-progress state: four tiles stuck on "Checking…" and a footer that says it is still reading, forever, with no retry control. The red error banner at :380-384 is the only signal that it is not still loading, and there is no way to re-trigger loadStatus other than leaving and re-entering the step.

- **`LOW` cosmetic — Runbooks knowledge card**  
  The write is real and durable, and the agent can see it — the runbooks MCP server reads the same app.runbooks.store (app/mcp/servers/runbooks/server.py:91-150). But the card's promise, "Write a procedure the AI can follow during an incident" (:717), overstates what it creates: a runbook with zero steps, which lists as step_count 0 and executes as a no-op. Only the free-text description is available to the agent.

- **`LOW` cosmetic — Welcome-step feature cards ("5x Faster MTTR", "Safety Gates", "6-Agent System")**  
  Marketing copy rendered as fact on the first screen a new user sees. "5x Faster MTTR" is a quantified performance claim with no measurement behind it anywhere in the repo — notably, the deployment has a real MTTR figure available at GET /api/dashboard/metrics that nothing here consults. Not a wiring bug, but it is a number presented as a fact that no response produced.


### landing-copy (Navbar, HeroSection, Pricing, Documentation, Blog, Footer, legacy Landing.tsx)

- **`CRIT` broken *(unverified)* — "Start Free Trial" on a paid plan → Stripe return URL**  
  After a customer actually pays, Stripe redirects the browser to https://host/billing?success=true, which the FastAPI SPA mount deliberately refuses to serve — the user lands on a raw JSON 404 page with no way back and no confirmation their subscription started. The cancel path 404s identically.

- **`HIGH` broken *(unverified)* — Hero stat: "9 MCP Servers"**  
  The headline capability number is a stale literal that undercounts the real catalog by 2. No request produces it; the endpoint that would (GET /config) is never called from this component.

- **`HIGH` broken *(unverified)* — Hero stat: "48 AI Tools"**  
  Advertised tool count is wrong by 16 against the catalog the backend actually serves at GET /config. Purely a literal — nothing computes it.

- **`HIGH` broken *(unverified)* — Hero stat: "99.9% Uptime SLA"**  
  An availability commitment is stated as fact with zero backing data. TestClient GET /api/status on this checkout currently returns overall_status "major_outage", so the marketing number and the live status surface disagree outright.

- **`HIGH` broken *(unverified)* — CLI command reference (copilot deploy / logs / status)**  
  Three of the four documented commands do not exist. `copilot deploy --env production` does not deploy anything — the string "deploy --env production" is sent to the LLM as an incident question. Same for `logs` and `status`. Each is presented in a copyable code block as a working command.

- **`HIGH` broken *(unverified)* — "Try Demo - No Login" button**  
  The button writes an identity object no code ever reads, then drops an unauthenticated visitor on a dashboard whose endpoints all require auth. Verified with COPILOT_API_TOKEN=secret123: GET /config, /metrics and /billing/plans all return 401 {"detail":"Missing or invalid API token."} — and render.yaml:34 auto-generates COPILOT_API_TOKEN in production, so this is the default deployment.

- **`HIGH` broken *(unverified)* — Entire components/Landing.tsx page**  
  535 lines of landing page — including the only component in this slice that actually fetches a backend endpoint (ErrorChart → getMetrics → GET /metrics, Landing.tsx:84-95) and the only one reading GET /config (line 155-158) — are dead code. Nothing in it renders for any user.

- **`HIGH` broken *(unverified)* — Checkout failure handling on paid plans**  
  Every checkout failure is swallowed into the console and the user is silently navigated to /login as if that were the intended flow. The backend produces a specific, human-readable reason ("Payments are not available…") that the UI never shows. The section renders no error state at all.

- **`MED` broken *(unverified)* — "Most Popular" badge**  
  The response field exists and is read, then overwritten with a client-side guess. The badge is displayed on Team while the backend designates Growth — a displayed fact that directly contradicts the response it was derived from.

- **`MED` broken *(unverified)* — Documentation config table: COPILOT_PROVIDER default**  
  The stated default is wrong (groq vs anthropic) and the enumerated set omits gemini and deepseek. A user following this table configures the wrong provider. The other three rows (DD_API_KEY, GITHUB_TOKEN, SLACK_BOT_TOKEN) are all real names present in app/config.py and .env.example.

- **`MED` broken *(unverified)* — Blog posts (4 articles with dates, authors, read times)**  
  An entire content section presented as a publication — with bylines, publication dates and read times — that no request produces and no CMS backs. The dates are ~2 years stale relative to today.

- **`MED` broken *(unverified)* — Blog claim: "From 4 Hours to 4 Minutes… reduced MTTR by 98%"**  
  A quantified performance claim with no computation behind it, on a page that has a real MTTR endpoint available.

- **`MED` broken *(unverified)* — Footer claim: "Join thousands of engineering teams using AI to ship faster"**  
  A social-proof number stated as fact with no request behind it.

- **`LOW` cosmetic *(unverified)* — Hero integration logo strip (8 tools)**  
  Tiles render a cursor and a hover animation, implying they are clickable, but nothing happens. The list is a literal rather than the server's integration set, and 2 of 8 entries have no matching integration in /integrations/status or MCP_CATALOG.

- **`LOW` cosmetic *(unverified)* — Blog "Subscribe via Email" / newsletter CTA**  
  Presented as a subscribe control but it only opens the visitor's mail client; no address is captured anywhere. The purpose-built, auth-exempt waitlist endpoint sits unused.


### api-layer (frontend/src/api.ts) - the typed client for chat/SSE, config, admin/tenancy, health, integrations, incidents and on-call

- **`CRIT` broken — Streamed investigation (chatStream / SSE)**  
  `\r\n\r\n` contains no `\n\n` substring, so the buffer never splits, `frames.pop()` removes the single element, and the for-loop body never runs. `onEvent` is called zero times for the whole stream; when the reader reports done the loop breaks and the entire buffered payload is discarded. In OnboardingWizard the demo ends with an empty step list, `answer === null` and `error === null` (no failure is shown either). In…

- **`CRIT` broken — Approve / Reject a pending write action over SSE (approveStream)**  
  The POST is real and the backend does execute the approval (app/api/main.py:2558 `audit.record("approval.decided", ...)` and `session.resume_stream(...)` run server-side), so the action is committed — but the frontend receives no `done`/`approval`/`error` event. useCopilot.ts:289 only restores `awaiting_approval` in the `catch`, and there is no throw here, so the turn is left permanently in `thinking` with no continu…

- **`LOW` cosmetic — getIncident() detail typing claims fields the backend never sends**  
  `detail.incident.has_investigation` is `undefined` at runtime while TypeScript types it as `boolean`, so a `has_investigation ? ... : ...` branch always takes the false path and `investigation_confidence` renders as blank/NaN. Currently latent: no file outside api.ts imports getIncident or IncidentDetail (verified by grep across all .ts/.tsx).

- **`LOW` cosmetic — On-call alert acknowledge/resolve — user_id not URL-encoded**  
  The backend coerces `user_id` to a UUID, so any non-UUID identifier (an email, a display name) is a 422 the caller sees only as `"... 422"`. Passing a raw string with `&` or `#` would also corrupt the query string. Both functions also make `userId` a required TS parameter even though the backend defaults it to the authenticated principal via `_acting_member` (api_router.py:1002-1011), so the client is stricter than t…

- **`LOW` cosmetic — GitHub status read (githubStatus)**  
  Contract is correct but nothing calls it — the UI gets GitHub state from `getConfig().github` instead. Dead export, not a defect.

- **`LOW` cosmetic — WebSocket base URL helper (getWebSocketUrl)**  
  Correct as a base-URL helper (it does avoid the `new URL("")` throw the comment describes), but it returns no path, so a caller must append `/ws/chat/{id}` itself. hooks/useWebSocket.ts calls `wsUrl()` directly instead, so this export is dead.


### small-hooks (hooks/useConfig.ts, hooks/useMe.ts, hooks/useTheme.ts, hooks/useThemeColors.ts, lib/planLimits.ts, lib/emailService.ts)

- **`MED` cosmetic — Nav avatar initial (identity letter next to the theme picker)**  
  Two problems. (1) When GET /me fails — 401 from require_auth (app/api/main.py:671), network error, or a proxy 502 — the hook asserts an identity the server never returned: authenticated:true, role "owner", label "Operator". The backend's own unauthenticated answer is `{multi_tenant:true, authenticated:false, role:null, label:"Guest"}` (app/api/main.py:1592-1594), i.e. the opposite. The visible consequence today is on…

- **`LOW` cosmetic — getThemeColors() imperative export**  
  An exported function duplicating the body of the hook's updateColors, with zero call sites. Dead code.

- **`LOW` cosmetic — EmailJS non-200 error branch**  
  The else branch can only be reached by a 2xx that is not exactly 200, which the EmailJS send API does not return. Dead code; every real failure goes through the catch at :45.

- **`LOW` cosmetic — Landing hero "MCP servers" count / model / provider labels**  
  This is the textbook fabricated-fact pattern — "3 MCP servers", "Opus 4.8", "Anthropic" shown as fact before/instead of any GET /config response — but the component is unreachable dead code, so no user sees it. Flagging it because the file is still compiled and shipped, and the pattern is one edit away from being live.


### landing-demo (components/landing: AIChat, Terminal, Dashboard, Metrics, GlobalMap, Pipeline, HowItWorks, FeatureCards, SecuritySection)

- **`CRIT` broken — GlobalMap round-trip latency probe (per cloud region)**  
  CSP `connect-src` governs fetch() regardless of mode:'no-cors'. All 12 probe fetches (2 per region) are blocked, the promise rejects with TypeError, probe() catches at GlobalMap.tsx:160 and returns null. Every region therefore renders rttMs===null -> tone 'unmeasured' -> gray dot + label 'Not measured' (GlobalMap.tsx:119-140, 399-402) and the detail panel prints "dynamodb.ap-south-1.amazonaws.com did not respond - it…

- **`CRIT` broken — SecuritySection claim: "No customer data stored on our servers"**  
  The product stores the customer's raw investigation prompt and the full RCA report server-side and exposes them over GET /api/investigations. The landing page asserts the opposite as fact. No endpoint produces this claim; it is a hardcoded string.

- **`HIGH` broken — SecuritySection compliance badges (SOC 2 Type II, ISO 27001, GDPR, HIPAA)**  
  Four certification claims rendered as fact with no attestation anywhere in the repo. 'ISO 27001' appears nowhere in the backend at all. These are legally consequential assertions produced entirely by a literal array.

- **`HIGH` broken — SecuritySection feature badge "SAML SSO & RBAC"**  
  SAML is advertised as a shipped security feature; no SAML implementation exists. RBAC is real (tenant roles resolved at app/api/main.py:648-651), SAML is not.

- **`HIGH` broken — Metrics card "48 AI Tools"**  
  The number is a stale literal that has drifted from the catalog it purports to count: the real total is 64, not 48. Metrics.tsx issues no request of any kind, so nothing keeps the two in sync. (Note GET /config requires auth per app/api/main.py:566-660 and _OPEN_PATHS at :246, so an anonymous visitor cannot fetch it as-is — but the displayed figure is still simply wrong.)

- **`HIGH` broken — Metrics card "9 MCP Servers"**  
  Understates the real count by 2 (or more). A displayed integer that no response produced and that disagrees with the backend constant it describes.

- **`HIGH` cosmetic — Metrics card "70% MTTR Reduction"**  
  The landing page prints an improvement percentage that the backend's own metrics module refuses to emit without a measured baseline. The 'target' wording in the small description does not appear in the 70% headline.

- **`HIGH` broken — Metrics card "15min Avg Resolution"**  
  An average presented as a measured fact with no measurement behind it. The '4+ hours' baseline has no source at all — app/database/models.py:186-188 explicitly notes time_saved_minutes is left at 0.0 because nothing measures how long a human would have taken.

- **`HIGH` broken — Metrics "Live Performance / Real-time" graphs (Request Rate, CPU Usage, Memory)**  
  Three charts explicitly badged 'Real-time' are a Math.random() walk. Nothing is measured; the y-axis is unlabelled so the fabricated values are never contradicted.

- **`HIGH` broken — Dashboard "Live Logs" streaming panel**  
  Fabricated log lines with fabricated service names and fabricated percentages, timestamped from the browser clock so they always look current, presented as a live stream. This is the textbook case: every field on screen was produced by Math.random() over a literal.

- **`HIGH` broken — Dashboard metric cards: CPU 68%, Memory 72%, Network I/O 205 MB/s, Requests/sec 1200 (+ trend deltas and sparklines)**  
  Four headline numbers, four trend deltas and four 12-point time series, all literals. AnimatedCounter (Dashboard.tsx:16-51) counts up to the literal, which reads as a live gauge settling. Nothing here is measured.

- **`HIGH` broken — Dashboard "Infrastructure Health" bars (API Gateway 99%, Checkout 95%, Inventory 100%, Payments 87%)**  
  Named services with named health percentages — including one deliberately degraded (Payments 87%, amber) to look authentic. The service names do not come from any response; there is no component registry behind them.

- **`HIGH` broken — Dashboard "Containers" counts (24 Running, 2 Pending, 8 Pods, 3 Nodes)**  
  Four cluster inventory figures rendered as fact, animated up from zero. Note 'Running 24' vs 'Pods 8' is internally inconsistent (24 running containers across 8 pods is stated as two separate top-level counts), which is what happens when numbers are chosen rather than counted.

- **`HIGH` broken — AIChat "Launch Console" button**  
  The control labelled 'Launch Console' replays the canned Node.js deployment demo. It cannot navigate anywhere — the component was never given the navigation callback that every sibling section receives. The user asking for 'real AI assistance' gets the same fake transcript again.

- **`HIGH` broken — AIChat custom prompt submit (type your own question and press Send)**  
  The substring test fires on fragments. Typing 'hi' matches because 'why is checkout api throwing 500 errors?' contains 'hi' (in 'throwing'), so the user is shown a fabricated root-cause analysis of a checkout API they never asked about. Typing 'a' matches 'deploy node.js app to production'. Typing anything containing 'set' (e.g. 'reset the cache') triggers the CI/CD pipeline demo. Only inputs that dodge all four trap…

- **`HIGH` cosmetic — HowItWorks Approve / Reject buttons**  
  A live-looking approval gate whose Approve button performs no request and whose success message claims a write ('Creating PR...') that never happens. This is the one interaction in the slice that a user would reasonably expect to hit the backend, and it is pure local state.

- **`MED` cosmetic — AIChat generated files / code blocks (Dockerfile, deployment.yaml, hpa.yaml, root-cause-analysis.md, ci.yml)**  
  No LLM is invoked. The 'Impact: 23% of transactions failing', '14:32 UTC - Deploy #847', 'Coverage/Confidence' figures inside root-cause-analysis.md (AIChat.tsx:59-74) are literals. The green 'Online' pill with a pulsing dot (AIChat.tsx:487-490) is likewise a static string, not a health check — GET /healthz exists at /tmp/routes.txt line 274.

- **`MED` cosmetic — HowItWorks investigation trace and RCA evidence (847 errors, commit a1b2c3d, checkout.js:42, 94% confidence)**  
  Every evidentiary detail — error count, commit sha, file:line, confidence percentage — is a literal in the STEPS array. Presented inside a section headed 'Watch how OmniPotent investigates and resolves incidents' (:545).

- **`MED` broken — Pipeline stage click (click any of the 8 stage tiles to inspect it)**  
  Clicking a stage rewinds the auto-play to that stage and then marches forward again 1.5 seconds later, so the stage cannot be held. The tile carries `cursor-pointer` (Pipeline.tsx:154) advertising a selection that does not stick. Hovering (Pipeline.tsx:537-538) is the only way to pin the log panel.

- **`MED` cosmetic — Pipeline stage logs and durations (187 tests passed, 89.2% coverage, 847 dependencies scanned, Security score A+, per-stage timings)**  
  Eight stages of fabricated CI output with fabricated coverage, dependency counts and wall-clock durations, replayed on a fixed 1500ms-per-stage timer regardless of the stated durations (Pipeline.tsx:437). The 'Stage completed in {duration}' footer at :354 asserts a timing that contradicts the animation the user just watched.

- **`MED` cosmetic — Terminal "Live Terminal" deployment animation**  
  'Building Docker image', 'Provisioning AWS', 'Production Live' are timer-driven text reveals. Nothing is built, provisioned or deployed. The section is explicitly labelled 'Live' and 'real-time'.

- **`MED` cosmetic — FeatureCards capability grid (12 cards with cursor-pointer and 3D tilt)**  
  Twelve cards advertise themselves as clickable (pointer cursor, hover glow, 3D tilt) and do nothing when clicked. The claims themselves are mostly backed by real backend modules (kubernetes/github/terraform in MCP_CATALOG app/api/main.py:80+, docker at app/mcp/servers/docker/server.py:27, cost at /tmp/routes.txt lines 20-40, secrets at app/secrets_vault.py), so this is an affordance defect rather than a false claim.

- **`LOW` cosmetic — Pipeline log-panel status badge (RUNNING / COMPLETED)**  
  With nothing hovered, the badge reads RUNNING for stages 1-7 even at the moment each visibly completes, and only flips to COMPLETED on the final stage. The hovered branch is correct; the default branch reports the wrong state for 7 of 8 stages.


### app-shell (App.tsx routing/auth-gating, config.ts, lib/apiClient.ts, types.ts)

- **`HIGH` broken — History "Continue" -> resume an investigation in the Console**  
  The `?thread=` query parameter is written into the URL and then read by nobody. The Console mounts with whatever conversation was last persisted locally, so "Continue" on investigation X silently drops the user into unrelated conversation Y (or an empty console on a fresh browser) with no error and no indication the requested thread was not loaded. There is also no endpoint being called to fetch thread X's turns — re…

- **`HIGH` broken — Post-OAuth landing (GitHub/Google sign-in)**  
  Two behaviours that App.tsx implements only in `afterLogin` are skipped for every OAuth sign-in: (1) the PENDING_INVITATION_KEY redirect (App.tsx:44-49), so a user who clicks a team-invite link, gets bounced to /login (AcceptInvite.tsx:44-47 stores the key) and signs in with GitHub lands on the console and the invitation is never accepted — POST /api/teams/invitations/{id}/accept (/tmp/routes.txt:203) is never called…

- **`MED` broken — Authentication gate on protected routes / session expiry**  
  Every page under /dashboard, /team, /incidents, /on-call, /slo, /runbooks, /knowledge etc. renders its full chrome for an unauthenticated visitor, fires its fetches, gets 401 from require_auth, and shows empty tables or a per-page error string. An expired token produces exactly the same picture with no route back to /login — the user must know to navigate to /login by hand. A single 401 handler in apiFetch (clear tok…

- **`LOW` cosmetic — config.ts authHeaders / API_TOKEN / wsUrl exports**  
  Three of the module's five exports are dead, superseded by lib/authToken.ts and lib/apiClient.ts. The file's header still claims to be the "single source of truth" for the API base while three independent derivations exist (config.ts:71, lib/apiClient.ts:17, components/landing/Navbar.tsx:14). Live risk: config.authHeaders only ever sends the build-time VITE_API_TOKEN, so any future caller wiring up to it would send n…

- **`LOW` cosmetic — /api-docs route**  
  An entire page is mounted in the router but unreachable except by typing the URL. It also duplicates the Swagger UI the Navbar already links to, so the two can drift.

- **`LOW` cosmetic — onBilling wiring for the landing page and the dashboard**  
  Two of the three billing entry points App.tsx thinks it wired up do not exist. /settings/billing is reachable only from the console top bar (Console.tsx:87). Not a runtime error — the underlying Billing page works (Billing.tsx:123,135 redirect to checkout_url/portal_url from POST /billing/checkout and POST /billing/portal, /tmp/routes.txt:239,246) — but the dashboard has no route to it.

- **`LOW` cosmetic — types.ts ChatResponse omits tokens_used**  
  The non-streaming POST /chat path (/tmp/routes.txt:261) returns a real token count that the type declaration makes unreadable, so the per-turn cost line silently disappears whenever the app falls back off SSE/WebSocket. The number that does show on the streaming path is genuine.


---

## 7. Partially working — the full list (146)

The happy path works. What follows is the specific input, failure mode or scope that does not.


### knowledge (frontend/src/pages/KnowledgeBase.tsx)

- **`MED` Star / unstar an article** — The star renders as a per-article property (article.starred, line 116) but nothing is ever persisted server-side. Starring in Chrome shows nothing starred in Safari, on another machine, or for a teammate looking at the same org's knowledge base. Clearing site data silently unstars everything.

- **`MED` Header article count ("N articles")** — The number is server-produced and correct at load, but it is not the size of the knowledge base — it is the count matching the current search/category, shown with no qualifier. It also goes stale after a delete: delete 1 of 12 and the header keeps saying "12 articles" (and the pager keeps saying "of

- **`MED` Search box** — Substring search itself works, but typing "database" fires eight GET /api/knowledge/documents requests. Nothing sequences the responses, so a slower earlier response can land last and leave results for "datab" on screen while the box reads "database". Note also that POST /api/knowledge/search (/tmp/

- **`MED` Starred-only filter toggle** — The filter is applied client-side to the loaded page only. With more than 50 documents, an article starred on page 2 is invisible while the filter is on and the page shows the "Nothing starred yet." empty state (line 934) even though the user has starred items. The pager does print a "the star filte

- **`LOW` Category tabs (All / Runbooks / Documentation / FAQ / Alert Patterns / Architecture / Postmortems)** — Filtering itself is a real server-side query. Two gaps: (1) an Incident Resolution document can be created but has no tab that filters to it — it is reachable only under "All", though its card icon exists at line 74; (2) changing category while on page 2 fires the fetch effect (line 646, declared be

- **`LOW` Refresh button** — The request is issued and the list does update, but after the first load `loading` is permanently false, so the icon never spins and no loading state appears. A user on a slow connection gets zero feedback and will click repeatedly; the same absence of a pending indicator applies to every search/cat

- **`LOW` Star click triggers a full list refetch** — Every star toggle issues an extra GET /api/knowledge/documents. If that request happens to fail, the catch block wipes the list (setArticles([]), line 637) and blanks the count (setTotal(null), line 639) — so clicking a star can clear the whole grid and replace it with an error banner. The same depe

- **`LOW` Markdown rendering in the article viewer** — Any list item containing inline code renders its backticks literally — the shipped kb-001 and kb-002 runbooks display "2. Check logs: `kubectl logs <pod> --previous`" with visible backticks, i.e. the exact commands an operator is meant to copy are the ones formatted worst. Fence lines are dropped en

- **`LOW` Card preview text** — Because every seeded and template document starts with a heading, the preview is just that heading — the kb-001 card's description reads "Symptoms", kb-002's reads "Definition". The line-clamp-2 styling implies a summary that is never actually shown.

- **`LOW` Timestamp shown on cards and in the viewer** — The date is real, but it is the creation date shown with an unlabelled clock icon next to the author. After the edit flow rewrites a runbook, the card still shows the original creation date, so a document corrected five minutes ago looks months old — while the backend sorts the list by updated_at DE


### runbooks - pages/RunbookBuilder.tsx

- **`HIGH` Execution count in the header ("N executions")** — The handler accepts both `runbook_id` and `limit` query parameters and the frontend passes neither, so the count is computed from at most the 50 most recent executions across every runbook in the org. An org with 60 executions of other runbooks shows "0 executions" for a runbook that has run many ti

- **`HIGH` Test Run button** — For an already-saved runbook this works: the request body `{service}` matches ExecuteRunbookRequest (:102-105), and `execution.status` is read from a real field (ExecutionResponse.status, :111). For a brand-new runbook the POST mints `rb-<uuid8>` server-side while the closure still holds the client-

- **`MED` Trigger selector (manual / alert / schedule / webhook)** — Setting and saving a trigger works — the value round-trips through UpdateRunbookRequest.trigger (router.py:95) and _apply_runbook (store.py:414). But the backend's own runbooks use qualified triggers the 4-pill union cannot represent, so those load with no pill highlighted, and clicking any pill sil


### dashboard (frontend/src/pages/Dashboard.tsx)

- **`CRIT` Alert/runbook/execution data load (all five fetches)** — Every non-OK response is discarded silently. Auth is a global app dependency, and /dashboard is reachable unauthenticated straight from the landing page (App.tsx:66 onDashboard), so on any deployment with COPILOT_API_TOKEN set all five calls return 401 and the page renders an all-clear: Active Alert

- **`MED` Auto-refresh (30 s poll)** — The poll refetches correctly, but because every tick sets loading=true the entire tab body is replaced by a spinner while the five requests are in flight. The alerts table, scroll position and any row the operator was reading vanish every 30 seconds mid-incident.

- **`MED` Alerts tab table (heading "Active Alerts")** — Resolved alerts are listed under a heading that says they are active. The Overview tile at :231 counts correctly (`status !== "resolved"`, :619) so the tile and the table disagree about the same word.

- **`MED` Active Alerts tile count ("N" / "X critical, Y high")** — The count is over the 50 most recent alerts of ALL statuses, then filtered to non-resolved in the browser. If the newest 50 rows are mostly resolved, a firing alert older than them is invisible and the tile under-reports; past 50 total alerts the number is simply capped. A sibling page already passe

- **`MED` Alerts surface vs. the actual ingestion pipeline** — Alerts that arrive through the webhook receivers become incidents, not OncallAlert rows, so they never appear in this tile or this table. On a deployment where alerting is wired through Alertmanager/Datadog, the dashboard says "No active alerts - all systems operational" (Dashboard.tsx:402) while in

- **`MED` Pending Approvals stat card** — The statuses are real, but the count is taken from the 50 newest executions only. A backlog of more than 50 runs hides older pending approvals from both this tile and the Pending Approvals panel (:427-429), which is exactly the queue this card exists to make visible. The static sub-label "Awaiting a

- **`LOW` Alert "Started" column** — The value is real, but toLocaleTimeString() drops the date entirely, so an alert that fired three days ago displays as a time-of-day and reads as today. Since the list is not status-filtered, old resolved alerts routinely appear with misleading timestamps.


### oncall - frontend/src/pages/OnCall.tsx

- **`HIGH` "New Schedule" button (creates a team + schedule)** — Both POSTs are correct and both rows really are created. But the page hard-selects teams[0] and has no team switcher, so from the second team onward the user fills in the form, sees the modal close with no error, and the page is byte-identical to before — the team they just created is invisible. Als

- **`MED` Escalation step "Notify …" target names** — Two target shapes reach the same JSON column. Policies created through POST /oncall/escalation-policies/enhanced or /from-template store target_type/target_id/target_name and render fine. Policies created through the plain POST /oncall/escalation-policies in the shape its own docstring documents sto

- **`MED` Escalation policies fetch error handling** — A 500/401/404 on either escalation call is swallowed with no branch and no error state. The card renders "No escalation policy configured", so a broken backend is indistinguishable from a team that genuinely has no policy — and on the detail-fetch failure path the previously loaded policy is left on

- **`LOW` Only the first escalation policy is shown** — The list endpoint returns every active policy and the UI silently drops all but the first, with no count and no indication that others exist. The card's title is the policy name, so it looks like the team's only policy.

- **`LOW` "Shift ends" timestamp** — With a primary and a secondary layer both active (or a shadow), the card lists every person but states one single end time, taken from whichever row the backend happened to emit first, under a generic "Shift ends" label. It reads as a fact about all of them.

- **`LOW` Green "active" presence dot / active-shift filter** — The status the backend sends is thrown away and replaced with a literal, so the filter at :177 is a tautology and the dot is a constant rather than a value any response produced. Behaviour happens to match today only because get_current_oncall hardcodes "active" on its side too.

- **`LOW` Empty-calendar message when there is no schedule** — With no schedule the grid says "No shifts generated for this week", which points the user at shift generation when the actual missing thing is the schedule itself. The same early return also leaves any previous `shiftsError` on screen because it never clears it.

- **`LOW` Error banner precedence** — Only one of the two errors can ever be visible and loadError always wins, so an action failure raised while a load error is displayed is written to state and never rendered.

- **`LOW` Refresh button** — The refetch genuinely runs and the data updates, but the button is never disabled, the icon never spins and no loading state is entered, so on an unchanged dataset the click produces no visible feedback at all.

- **`LOW` Override date range validation** — Neither side validates ordering. The POST returns 200, the page closes the modal and refetches as if it succeeded, and the override can never match _get_active_override, so it silently never takes effect.

- **`LOW` Override notification text** — Because the page never sends original_user_id the broadcast notification reads "<uuid> is covering for None", and the stored override row has a null original_user_id so nothing downstream can tell who was replaced.

- **`LOW` Person names and avatar initials** — Honest about its source (the schema has no display name), but the initials logic splits on a space that an email local part never contains, so "jane.doe" yields the single initial "j" and every avatar shows one letter. Users with no email row render as "user 3f2a10bc".

- **`LOW` Notification channel chips on escalation steps** — The backend's default channel set includes "phone", and "teams"/"webhook" are valid too — none are in the map, so those chips render the raw lowercase enum value next to the properly cased ones. The map's "voice" key matches no backend value at all.

- **`LOW` Create Schedule modal labelling** — Three different nouns for one action in one modal, and the schedule's actual name is derived rather than entered — the user never sees or controls the name of the schedule the header button promised to create.


### team

- **`MED` Only the first team is ever shown; no team switcher** — list_teams returns every team the user belongs to, ordered oldest-first, and the page discards all but index 0 with no UI to pick another. After clicking Accept, the banner reads "You have joined Platform" but loadTeam() immediately re-selects the oldest team — so the member roster rendered directly

- **`MED` Pending Invitations list** — The happy path is correct — real pending invitations with a server-computed expiry, rendered at Team.tsx:696. But line 144 collapses every non-ok status to an empty array. The 403 case for members/viewers is intentional and documented, yet the same branch silently eats a 500 or a proxy error for an 

- **`MED` Invitation email delivery** — The invitation record is created correctly and the link carries the server-issued id (Team.tsx:270), so the /invite/{id} route resolves to a real invitation. But out of the box VITE_EMAILJS_PUBLIC_KEY is unset, so no email is ever sent and the backend does not send one either. The UI is honest about


### slos - frontend/src/pages/SLOTracker.tsx (SLO Tracker page, mounted at /slo)

- **`MED` Create SLO — blank Name / Service fields** — There is no client-side required-field validation anywhere in CreateSLOModal (SLOTracker.tsx:571-579 calls onCreate with whatever is in state). Empty inputs are silently replaced with placeholder values, so the user gets a real, persisted SLO called "New SLO" tracking a service literally named "unkn

- **`MED` Create SLO — blank/invalid Target field** — A blank target produces a raw JSON blob ('{"detail":[{"type":"float_parsing","loc":["body","target"]...') as the user-facing error — and that blob is itself rendered behind the modal overlay and then cleared by the poll. Every other error path in this file correctly uses describeFailure (lines 329, 

- **`MED` "Warning" summary tile and warning filter** — For every SLO created through this page, any burn_rate > 1.0 already makes the backend return status "at_risk", which SLOTracker.tsx:749 maps to "critical". The `burn_rate > 1` warning branch at line 750 is therefore unreachable, so the Warning tile (SLOTracker.tsx:926) permanently reads 0 and the W

- **`MED` Summary tiles doubling as status filters (Total / Healthy / Warning / Critical / No Data)** — Total, Healthy, Critical and No Data are honest counts of real fetched rows and the filtering works. Two defects: the Warning tile is structurally unreachable for UI-created SLOs (see the Warning finding), and when a filter matches nothing the grid at SLOTracker.tsx:949-953 renders as blank space wi

- **`LOW` Create SLO — double-submit** — Two clicks issue two POST /api/slos requests and create two distinct SLOs with identical names, both of which then appear in the grid. There is no idempotency key and no uniqueness check in the create path.

- **`LOW` SLI type label on card ("Availability" / "P99 Latency" / …)** — An SLO created via the API with sli_type="saturation" (or freshness/correctness/custom) renders an empty label above its headline number — the uppercase caption line is blank, giving no indication what the percentage measures. The create modal only offers the four mapped types (SLOTracker.tsx:622-62

- **`LOW` Measurement window label ("30d window")** — Correct for the rolling_* values the modal can produce. For an SLO defined with calendar_week/calendar_month/calendar_quarter the prefix strip does nothing and the card reads "calendar_month window"; for rolling_24h it reads "24h window" even though the TypeScript union claims only 30d/7d/1d. Cosmet

- **`LOW` Empty state ("No SLOs configured")** — A backend failure and a genuinely empty account render the same "No SLOs configured / Create your first SLO" message. The red error banner does render above it (SLOTracker.tsx:880-891) so the failure is not fully hidden, but the primary message on screen still tells the user their SLOs do not exist 


### incidents (frontend/src/pages/Incidents.tsx)

- **`MED` Overall Confidence bar / AI column percentage** — A queued/running investigation shows a yellow bar reading '0%'; a failed one shows '0%' forever; a completed run whose report omitted a confidence field also shows '0%'. Three different states render as the same stated measurement. Also note the confidence is only ever one of 0.9/0.6/0.3/0.0 — the b

- **`MED` Declare Incident button + modal** — Creation itself works end to end, including the error path (res.ok checked at :752, describeFailure flattens 422 field lists). The gap: `description` is stored (models.py:92) but toIncident never maps it (Incidents.tsx:96-114) and IncidentDetail never renders it. Whatever the operator types in the D

- **`MED` Filter tabs (All / Active / Resolved)** — Filtering is purely client-side over the 50 most recent incidents. The backend supports `?status=resolved&limit=200` and it is unused. With more than 50 incidents, 'Resolved' can render as empty even when resolved incidents exist, and 'All' silently truncates with no pagination and no 'showing 50 of

- **`MED` Empty state ('All Clear / No active incidents')** — Two contradictory messages at once: a red 'HTTP 500' banner above a green check reading 'All Clear — No active incidents'. Also mislabeled on the Resolved tab, where zero rows means 'no resolved incidents', not 'no active incidents'.

- **`LOW` 'Assigned' column** — The value shown is real, but it renders '-' for essentially every incident because nothing in the product can set it — there is no assign control, and no status/severity/title edit either, despite the PATCH route existing.

- **`LOW` 'Affected Services' panel** — Always exactly one chip despite the plural heading; no multi-service concept exists server-side. If `service` is an empty string the card renders as an empty box with no explanation.


### analytics (frontend/src/pages/Analytics.tsx)

- **`MED` Silent degradation when the alerts or teams fetch fails** — A failing alerts fetch is swallowed into an empty array, so MTTR, MTTA, Total Incidents, Resolved, Open, Resolution Rate and the trend chart all silently drop their alert half and render as confident, precise, wrong numbers with no error banner. A failing teams fetch renders "No team data available"

- **`MED` "vs previous Nd" deltas on the six metric cards** — The truncation guard is a genuinely good idea but leaks across the two datasets. `oldest` is the minimum over the merged array, so a deployment with old alerts but more than 200 incidents in the window satisfies `comparable` on the strength of the alerts alone. The previous window then contains aler

- **`LOW` Incident Trends severity stacking** — Incident severities map cleanly since they are enum-constrained. Alert severities are an unconstrained string column, and the frontend's match list disagrees with the backend's own: an alert with severity "p1" — which app/oncall/api_router.py:967 explicitly classifies as critical — falls through :61


### servicemap

- **`MED` Zoom in / zoom out / zoom percentage** — The transform applies and the percentage readout is correct, but there is no pan, no drag, no wheel handler and no scrollbar. With `transform-origin: center center` inside `overflow-hidden`, zooming to 200% pushes content off every edge with no way to reach it: a node at y=110 in an ~800px-tall canv

- **`MED` Topology endpoint cost under the 15s poll** — N+1 by construction: at the handler's default `limit=200` a single map load issues 200 full-graph SELECTs plus 200 edge loads, and the page repeats that every 15 seconds per open tab. The data returned is correct — this is a cost/latency defect, not a truthfulness one, but it is the reason a large m

- **`LOW` 15-second auto-refresh** — The interval fires and the cleanup clears it, so the polling works. But there is no in-flight guard or abort: `/service-metrics` can legitimately take up to 30s (three sequential Prometheus queries at a 10s timeout each), so with a slow Prometheus requests overlap and whichever resolves last wins — 

- **`LOW` Metrics note on a topology network failure** — On a thrown topology fetch the freshly-computed `note` from this cycle is discarded and the previous cycle's `metricsNote` stays on screen. The banner can therefore keep asserting a stale reason (e.g. 'http://prom:9090 returned no series') that belongs to an earlier poll. Small inconsistency with th


### statuspage - frontend/src/pages/StatusPage.tsx (public status page)

- **`HIGH` Subscribe form submit (POST /api/status/subscribe)** — Method, path and body shape are all correct, and the success/failure text comes from the real response (`data.message` / `data.detail`), not from a client-side string. But the feature cannot complete end to end: (1) anonymous visitors are rejected 401 by the app-wide require_auth (main.py:1093) befo

- **`MED` "Last updated" timestamp** — The timestamp is the browser clock at mount / at response time, not the server's data timestamp. Before the first response completes it already claims "Last updated: <now>" for data that does not exist. It also hides server-side staleness: component status is refreshed by a 30s poll loop (app/status

- **`MED` Uptime percentage labelled as 30-day / 90-day** — The figure is real data, but the window is "since we started measuring", not 30 or 90 days. On a freshly deployed instance every service reads "100.00% uptime" for both 30d and 90d within minutes of install, and a service with 5 days of history still reports its 5-day number under a "90d" label. Not

- **`MED` "No incidents in the last 7 days" empty state** — The limit is applied before the is_active filter, so active incidents silently eat slots. Concretely: 3 ongoing incidents plus 8 resolved ones in the last 7 days - the query returns the 10 newest, drops the 3 active, and the page shows only 7 of the 8 resolved with no indication anything was omitted

- **`LOW` Subscribe error rendering** — Fine for FastAPI's JSON error bodies, but a proxy/CDN HTML error page or a gateway 502 makes res.json() throw, and the catch at StatusPage.tsx:153-156 puts the raw JSON.parse exception text ("Unexpected token '<'...") into the user-visible message instead of the HTTP status.


### billing (frontend/src/pages/Billing.tsx + components/billing/*, api/billing.ts)

- **`MED` Whole-page data load (all tabs)** — Promise.all rejects on the first failure, so one failing request blanks all five other datasets. If GET /billing/invoices 500s, the Plans tab shows no plans, the Usage tab shows no bars and the Overview shows no subscription card — even though those four calls succeeded. The deliberate per-request i

- **`LOW` "Update billing information" and "Change billing email" rows (Settings tab)** — Six differently-labelled controls all open the same generic Stripe portal home page. Neither row deep-links to the billing-address form or the email form as its subtitle promises ("Change your billing address and details", "Update where invoices are sent"); the portal API supports a `flow_data` deep

- **`LOW` Cancel-modal feedback textarea** — The typed feedback is accepted by the endpoint and then dropped for any subscription without a live Stripe subscription id, and is never stored in the product's own database in any case. The user is told their feedback helps; nothing in the system can read it back.


### postmortem (frontend/src/pages/Postmortem.tsx)

- **`MED` Add Item form — due date** — The `<input type="date">` value "2026-03-01" is parsed as UTC midnight and stored as `2026-03-01T00:00:00.000Z`. The table then renders it with `toLocaleDateString()`, which for any user at a negative UTC offset (all of the Americas) shows the previous day. A user picks March 1 and the row says Feb 

- **`MED` Export button (Markdown download)** — Purely client-side (no request needed) and it does download a file. But it emits only the four prose sections: the action items — the part of a postmortem that outlives the incident and the whole point of the review — plus the timeline, the metrics, the authors, the severity and even the document ti


### marketplace - pages/IntegrationMarketplace.tsx + lib/integrationSpecs.ts

- **`CRIT` Connect GitHub (credential modal → verify + save)** — On a multi-tenant deployment — the only mode where the rest of this page works at all — the token is validated against api.github.com, the modal renders "Connected — GitHub answered with the repository, so the token works" (IntegrationMarketplace.tsx:510-514), and then the value is written to a proc

- **`HIGH` Connect / Replace credentials (Datadog, PagerDuty, Sentry, Distributed tracing)** — Multi-tenant: works end to end. The env names are correct — DD_API_KEY/DD_APP_KEY/DD_SITE (integrationSpecs.ts:79-88) are read verbatim at app/mcp/client.py:60-63, PAGERDUTY_API_TOKEN/PAGERDUTY_FROM_EMAIL (integrationSpecs.ts:96-104) at app/mcp/client.py:97-98, SENTRY_* (integrationSpecs.ts:112-114)

- **`HIGH` "Connected" status badge and the "N connected" header count** — In multi-tenant mode the badge answers a different question than the one it claims to (IntegrationMarketplace.tsx:36: "GET /integrations/status says the connector has working credentials"). Two concrete inversions. (a) The operator has DD_API_KEY/DD_APP_KEY in the deployment env; org B has stored no

- **`MED` Multi-secret save (partial failure)** — SENTRY_API_TOKEN lands in the store, SENTRY_ORG's POST 403s, SENTRY_PROJECT is never attempted. The modal shows the error and stays open; the card behind it is never refreshed, so it still reads "Available" while one secret is now persisted server-side. Closing the modal leaves that stale card until

- **`MED` Disconnect GitHub** — A connected GitHub card renders the static text "Configured by the deployment" (:379) with no control at all, even when the user themselves connected it from this very page seconds earlier in single-tenant mode. The one endpoint that would undo it is never called from anywhere in the frontend.

- **`LOW` Category filter buttons (All / Observability / Alerting / Code / Infrastructure / Chat / Knowledge)** — Filtering works for all seven pills. The Chat pill renders with no glyph — an 18px gap where every sibling has an icon — because `message-square` is missing from the icon set. Every other icon used on this page (server, chart, bell, code, book, x, check, arrow-left, refresh-cw, search, alert-triangl

- **`LOW` Catalog coverage vs. what the backend actually reports on** — Same class of gap the file's own comment (:245-248) says was fixed for `traces`. The deploys connector is real, is status-reported, and has three tools, but never appears in the grid — so its connected/offline state is invisible here. Lower impact than the traces case since DEPLOYS_API_URL is not in


### apidocs-profile (frontend/src/pages/ApiDocs.tsx, frontend/src/pages/Profile.tsx)

- **`HIGH` Sign Out button (Danger Zone)** — The button clears the browser's localStorage credential and navigates away, so the local session ends. But no request is ever sent to the server. The backend implements per-jti revocation (app/auth/oauth.py:493-507 verify_session_token consults is_session_revoked) and exposes POST /auth/logout preci

- **`LOW` Copy-to-clipboard buttons (Base URL + each endpoint URL)** — Two failure modes. (1) Over plain http:// on a non-localhost host navigator.clipboard is undefined, so line 169 throws a synchronous TypeError, setCopied(true) never runs and the button gives no feedback at all. (2) In a secure context where writeText() rejects (document not focused, permission deni

- **`LOW` Authentication instructions card** — Wrong for three endpoints documented on this very page. GET /metrics/slo (ApiDocs.tsx:95) and POST /signup (ApiDocs.tsx:133) need no credential at all. PATCH /admin/plan (ApiDocs.tsx:132) is in _OPERATOR_PATHS and needs COPILOT_OPERATOR_TOKEN, not the org API key the card tells you to send — a devel


### auth-pages (pages/Login.tsx, pages/AuthCallback.tsx, pages/AcceptInvite.tsx)

- **`MED` GitHub / Google button availability ("Continue with GitHub", "Continue with Google")** — As soon as a Supabase URL+anon key exist, BOTH buttons render regardless of which providers the Supabase project actually enables and regardless of what /auth/providers returned. The rendered capability list is asserted from two config strings, not from any response. Clicking a provider that is not 

- **`MED` Password reset link destination** — The reset mail is genuinely requested and genuinely sent (that part works), but the recovery link just signs the user in and drops them on /console; they are never offered a form to set a new password, so the password is never actually reset and the next normal sign-in fails again.

- **`MED` Invitation sign-in handoff ("Sign in to join" button)** — The round trip is genuinely implemented and works for Supabase email sign-in, but it is defeated by the two paths above: OAuth logins land on AuthCallback which hardcodes /console, and token-less logins (demo / non-Supabase password) return here with no credential and loop.

- **`LOW` Provider-discovery failure feedback** — A 500 or a network failure is indistinguishable from "this deployment has no OAuth": the spinner clears, no OAuth buttons render, no message is shown, and the user is silently pushed toward the email form that (per the findings above) cannot authenticate them.

- **`LOW` /auth/callback reachability under `npm run dev`** — In dev the browser navigation to /auth/callback is proxied to the API instead of being served by Vite, so the user gets the stale built bundle from frontend/dist — or a raw JSON error object if dist has never been built. Production (single origin, main.py:1243) is fine. Note also that the comment at

- **`LOW` Auto-redirect when already signed in** — Navigation is triggered from inside render rather than an effect, which React flags as a state update during render of another component. It also fires for the build-time fallback credential (authToken.ts:13,37): a bundle built with VITE_API_TOKEN set makes /login permanently unreachable — it bounce


### history

- **`MED` Status filter dropdown (All / Completed / Active / Error)** — The request/response wiring is correct, but no code path ever persists status "active" or "error": failed turns return a ChatResponse without recording history (app/api/main.py:2170-2181) and in-flight turns are never written. Both options are guaranteed to return an empty list and show "No investig

- **`MED` Relative timestamps ("5m ago", "3h ago")** — An ISO string with no offset is parsed by the browser as LOCAL time, but the value stored is UTC. On a UTC+5:30 client a just-finished investigation reads "5h ago"; on a UTC-7 client `diffMins` goes negative and the row literally renders "-420m ago". Correct only on a Postgres deployment (timestampt

- **`LOW` Expanded "Report Summary" panel** — Rows with no structured report still render a "Report Summary" heading over a literal `{}`. Conversely, on the Supabase branch (History.tsx:92-104) `report` comes back as real null, so the panel never opens — which also means the Slack, Markdown, HTML, JSON and Continue buttons are unreachable for t

- **`LOW` Filter change while paginated (double fetch race)** — Changing the filter issues fetch #1 for the old page with the new status, then the page reset issues fetch #2 for page 1. Both are in flight with no cancellation; if #1 resolves last the list shows offset-30 rows while the pager reads "Page 1".


### queries (SavedQueries.tsx, PromptSuggestions.tsx, useSavedQueries.ts)

- **`MED` Saved-queries count on the composer toolbar button** — N is correct at page load and after saves made from the composer, but is stale after any add/delete/label-edit/clear performed inside the modal, because the modal uses a separate useState copy with no cross-instance sync. It is a rendered number that can contradict the list the user just edited.

- **`MED` Recent / Most Used / All tabs** — Tab switching and sorting work, purely client-side. Two misleads: (a) Recent and Most Used are hard-capped at 5 while the footer simultaneously claims "{queries.length} saved queries" (SavedQueries.tsx:254) — with 20 saved the default Recent tab shows 5 items next to a "20 saved queries" label and n

- **`MED` Modal dismissal (backdrop click, X button, Escape)** — Backdrop click and the X button both close correctly and the stopPropagation guard prevents accidental close when clicking inside the card. Escape does not close it (no key handler), there is no `role="dialog"`/`aria-modal`, no focus trap, and the query rows are plain `<div onClick>` (:147-158) rath

- **`LOW` Click a saved query to load it into the composer** — The text does land in the textarea and the composer is focused, so the flow works. But `autoGrow(ref.current)` (Composer.tsx:46) runs synchronously in the same tick as `setValue(text)` (:42), so it measures `el.scrollHeight` of the OLD textarea content — a long multi-line saved query is inserted but


### admin-config (ModelConfig, AdminPanel, SourceConnect, GithubConnect)

- **`HIGH` "Change model" → Apply (switch provider/model/key at runtime)** — Works in single-tenant (the default, app/config.py:294 copilot_multi_tenant=False): body shape matches ModelConfigRequest exactly, the backend validates the model ID against the catalog and rolls back if no key resolves, and refreshConfig() re-reads /config so the sidebar MODEL card updates. BROKEN 

- **`HIGH` GitHub "Connect repo"** — Works in single-tenant: the backend really calls api.github.com/repos/{repo} and maps 401/404/502 to distinct errors that the form displays. BROKEN in multi-tenant: github_connect() calls runtime.set_github() which writes the process globals, while _github_status() reads runtime.github_connected() →

- **`HIGH` "Verify chain" (audit tamper-evidence check)** — verify() has no try/catch and its rejection is never handled. /audit/verify requires the admin role, so for a viewer or responder the call 403s, the promise rejects unhandled, setChain never runs and the button does nothing at all — no spinner, no error, no text. Even on the success path the failure

- **`MED` Admin console API-key list** — The rendered fields (k.id, k.prefix, k.name, k.role, k.active) all exist in the handler's return dict (app/api/main.py:3160-3164) — so on the happy path this is real. But the .catch collapses every failure into an empty array: a 403 for a viewer/responder, a 400 from _current_org_id(), or a network 

- **`MED` Admin console audit trail list** — e.ts, e.event and e.actor are all real fields stamped by audit.record() (app/audit_legacy.py:102-108), and the store scopes to the caller's org (app/audit_legacy.py:334-336) — correct on the happy path. But the same .catch swallow applies: a viewer/responder gets a 403 and sees an empty "Audit trail

- **`MED` Set repo / logs source path ("Use this path")** — Two limits the UI never mentions. (1) The placeholder invites an arbitrary absolute path ("/path/to/your/repo", SourceConnect.tsx:58) but _set_source() confines the path to settings.sources_root, which defaults to the OmniPotent project root (app/config.py:733-736), so any path outside the checkout 

- **`MED` "Remember credentials" checkbox + auto-reconnect on reload** — Three problems. (1) Unticking the box does not clear a previously saved credential — connect() only calls saveCredentials() when rememberMe is true (:98) and never calls clearCredentials() when it is false — so the note "Session only · enter token each time" (:214) is false: on the next reload the a

- **`MED` "Reset" (revert runtime config to .env)** — Works in single-tenant. Two gaps: (a) in multi-tenant it clears process globals the tenant never read, so nothing changes for the caller (same root cause as Apply); (b) even in single-tenant the component's local `provider`/`model`/`fast` state is not re-synced to what the server reverted to, so reo

- **`LOW` GitHub "Switch" (change repo while connected)** — Once connected, clicking Switch opens the credential form and there is no way to close it again. `open` is only set back to false inside connect()'s success path (:103), so a user who opens the form and changes their mind is stuck with it expanded until they either complete a connect or reload the p

- **`LOW` Repository field accepts a pasted GitHub URL** — For "https://github.com/acme/app", split("/") yields ["https:", "", "github.com", "acme", "app"], so name === "" → repoOk false → canSubmit false → the "Connect repo" button stays disabled with no explanation of why. The backend was deliberately written to accept exactly this form; the client-side v


### shell-ui (Sidebar, ThemePicker, SignupModal, ErrorBoundary, Skeleton)

- **`MED` Connector badge stays stale after GitHub connect/disconnect** — getIntegrationStatus() is called once on Sidebar mount and the result is never refetched. GithubConnect refreshes only the /config store (refreshConfig), which the badge does not read. So after a successful POST /github/connect the backend's /integrations/status would now return github:true, but the

- **`MED` App does not refresh after a successful signup** — Console renders SignupModal and Sidebar as siblings (Console.tsx:190, :193), so the sidebar's single GET /integrations/status already fired — unauthenticated, so it 401'd and pinned every badge to "unknown" permanently. Acquiring a credential does not retrigger it. GET /config recovers on its own be


### useWebSocket - real-time chat transport (frontend/src/hooks/useWebSocket.ts)

- **`MED` Sending a chat message over an already-open socket** — Happy path works. But after `stop()` -> `wsDisconnect()` (useCopilot.ts:343-350) followed by a new send, the old socket's `onclose` lands a tick later and nulls the ref belonging to the NEW socket; `sendChat` then becomes a permanent no-op while React state still reads "connected", so the message is

- **`MED` Automatic reconnection with exponential backoff** — Backoff itself is correct. The defect is the retry budget after a manual disconnect: following `stop()` or `newConversation()` (useCopilot.ts:343-359, both call `wsDisconnect`), the next connection starts with the counter already at the maximum, so if it drops before `onopen` fires there is zero ret

- **`LOW` Reconnecting when the thread changes** — Latent rather than live: useCopilot.newConversation (useCopilot.ts:353-359) calls `wsDisconnect()` and `setThreadId()` in the same batched update, so the guard at :297 (`status === "connected" || "connecting"`) sees "disconnected" and the effect no-ops. Any consumer that changes threadId without dis


### useCopilot - the copilot conversation hook (frontend/src/hooks/useCopilot.ts) and its only consumer, components/Console.tsx

- **`HIGH` Approve / Reject a pending write action** — The request, method, path and body are all correct and the happy path works. Two defects. (1) Trace wipe: app/session.py:233 `_drive_events` starts a FRESH `trace: list[str] = []` for the resume, so the terminal `done` event carries only the post-approval steps (app/session.py:274-280 -> app/api/mai

- **`HIGH` Automatic SSE fallback when WebSocket is unavailable** — The fallback only works because probeWebSocket (useWebSocket.ts:351-354) happens to resolve false via `onerror` on a rejected handshake, and it only applies to the NEXT message — never to the one already queued. Any WS failure that is not detected before send() runs produces the permanent hang above

- **`MED` New conversation button** — Clicking New while a WebSocket investigation is running clears the transcript but leaves `busy === true`, so Console.tsx:155 keeps the Composer and every suggestion button disabled on the brand-new empty conversation. The user is locked out until they reload. No network call is involved — thread cre

- **`MED` Thumbs up / thumbs down feedback** — The request itself is correct and app/feedback.py:29-51 really persists it. But (1) useCopilot.ts:335-337 swallows every error and Message.tsx:107 shows "Thanks for the feedback" purely from optimistic local state, so a 401/500 is indistinguishable from success; (2) app/api/main.py:2857 `investigati

- **`MED` Approval request rejected at the HTTP layer** — The ApprovalCard silently reappears exactly as it was, with no error text anywhere, so a user whose token expired can click Approve indefinitely and see nothing happen. This only covers transport/HTTP failures — the semantic failures are fine, because app/api/main.py:2545-2557 returns them as a 200 


### console (Console.tsx, Composer.tsx, Message.tsx)

- **`HIGH` "New" button (start a fresh conversation)** — busy stays true forever, so disabled = busy || awaitingApproval (Console.tsx:155) stays true: the fresh empty state renders with all 4 suggestion chips disabled (Console.tsx:215) and the Composer textarea + Send disabled (Composer.tsx:62,106). The console is unusable until a full page reload. When S

- **`MED` Thumbs up / thumbs down on an investigation** — The happy path really persists (app/feedback.py:29-51 appends to feedback.jsonl and audits). But the UI commits optimistically: Message.tsx:87-90 sets `rated` before the promise resolves, useCopilot.ts:335 swallows every rejection, and Message.tsx:107 then renders "Thanks for the feedback" while Mes

- **`MED` Logout button** — When it renders it does the right thing (clears both session_token and api_key). But a user authenticated by self-serve signup (api_key) or by the backend OAuth callback has user === null, so no Logout button renders at all and there is no way to sign out from the console — while Console.tsx:82 simu

- **`MED` Conversation persistence across reload** — Restore-on-reload works correctly, including downgrading an interrupted "thinking" turn to a recoverable note. The defect is that the full investigation transcript — questions, agent traces, RCA reports — survives logout in localStorage, so the next user on the same browser sees the previous user's 

- **`LOW` Sign up button** — Opens the modal correctly. The visibility condition is wrong for non-Supabase credentials: a user holding an api_key from self-serve signup (POST /signup, /tmp/routes.txt:332) or a session_token from the backend OAuth callback still has user === null and is therefore still prompted to sign up.


### rca-feedback (FeedbackButtons.tsx, RcaReportCard.tsx, ApprovalCard.tsx, ActivityTimeline.tsx)

- **`LOW` ActivityTimeline — "verify" step renders as a bare internal node name** — Every tool-enabled investigation ends with a timeline row that just says "verify" — a raw graph node identifier with no icon and no human description, sitting next to polished rows like "tool result: search_logs". In multi-agent mode (app/graph/builder.py:383-386) it is worse: "orchestrator", "inves


### auth-hooks (frontend/src/hooks/useSupabaseAuth.ts, frontend/src/lib/supabase.ts, frontend/src/lib/authToken.ts)

- **`MED` Sign out** — Local clearing is correct and unconditional (clearAuthToken runs before the `if (!client) return` guard at line 180, so a backend-OAuth session is dropped even when Supabase never loaded). But the hook never calls `POST /auth/logout`, which is the only thing that records the token's `jti` as revoked

- **`MED` Runtime Supabase config fetch (enables the whole auth UI)** — The happy path is correct — right method, right path, right field names, and the route is in _OPEN_PATHS so the unauthenticated fetch is not gated. Three failure modes are silent: (1) supabase.ts:34 `if (res.ok)` has no `else`, so a 404/500 produces no log and no state at all; (2) supabase.ts:40-42 

- **`MED` OAuth redirect target for Supabase GitHub/Google login** — In production this works by design — app/api/main.py:1250-1254 serves frontend/dist/index.html for /auth/callback so React Router reaches AuthCallback.tsx, which calls getSession() and stores the token (AuthCallback.tsx:64-79). In `npm run dev` it does not: vite.config.ts proxies the whole `/auth` p

- **`LOW` Auth loading spinner resolves** — Every normal path resolves (supabase.ts:40-42 and :66-69 swallow their own errors), so this is not routinely hit. But if `getSession()` rejects — an expired refresh token against an unreachable Supabase project, for instance — `setLoading(false)` at :85 never runs and the login form stays permanentl


### onboarding - frontend/src/components/OnboardingWizard.tsx (5-step setup wizard mounted at /onboarding, App.tsx:83-89)

- **`HIGH` GitHub connect form (token + repo)** — In multi-tenant mode the whole connect path is a no-op that reports success. GitHub validates the credential, the handler returns HTTP 200, so `res.ok` is true at OnboardingWizard.tsx:296, the token box is cleared and loadStatus() re-fetches /integrations/status — which calls runtime.github_connecte

- **`HIGH` Integration tiles "Connected — using live data" / "Not connected" label** — In multi-tenant mode the tile states a fact about the wrong scope, in both directions. Host has DD_API_KEY set + this org has not: the tile says "Datadog — Connected — using live data" and the footer counts it, while every investigation this org runs gets "" for DD_API_KEY and answers from bundled f

- **`HIGH` "N of 4 connected" summary line** — The count is genuinely derived from a response — no hardcoded number — but it inherits the scope bug above verbatim. On a multi-tenant deployment whose host env carries DD/PagerDuty/Slack keys, every org sees "3 of 4 connected" on its very first screen regardless of what that org configured.

- **`MED` "Tell me in Slack when an alert fires" delivery warning** — The wizard never reads channels[].enabled, which is the other half of the delivery condition. An org whose Slack channel row has enabled=false but a stored webhook reports configured=true, so the wizard shows the toggle on, shows no warning, and Save writes only the rule — and nothing is ever delive

- **`MED` Playground approval pause** — The wizard has no approve/reject control, so a demo run that hits a safety gate stops permanently: no answer, no "Investigation Complete" panel, and no way to resume from this screen. The user is told the run paused but not that it can only be resumed from the Console. Ironically the Welcome card at

- **`LOW` Playground re-run** — Once a single trace line has arrived, steps.length is permanently > 0 and the button is unmounted for good. If the run failed midway (say the LLM key was missing, then got fixed), the only way back to a runnable state is to leave the step and come back so PlaygroundStep remounts — which the progress


### landing-copy (Navbar, HeroSection, Pricing, Documentation, Blog, Footer, legacy Landing.tsx)

- **`MED` Pricing plan cards (prices, seats, feature bullets)** *(unverified)* — Wiring is correct and the contract matches field-for-field, but on any deployment with COPILOT_API_TOKEN set (render.yaml:34 generates one) a marketing visitor is never authenticated, so the public pricing page ALWAYS renders the hardcoded table, not the server's. The literals happen to match today 

- **`LOW` Blog "Read more" deep links** *(unverified)* — Two of the four cards open the repo but the fragment matches no heading, so the browser silently drops the reader at the top of a 500-line README instead of the promised section.

- **`LOW` Documentation feature tile: "Multi-Cloud — AWS, GCP, Azure support out of the box"** *(unverified)* — "Out of the box" overstates it: the code exists in the repo but no exposed endpoint or catalog entry lets a user reach it, so nothing in the running product corroborates the claim.

- **`LOW` Copy-to-clipboard on documentation code blocks** *(unverified)* — The green checkmark confirms success even when the clipboard write rejects (permission denied / not focused). On a plain-HTTP origin navigator.clipboard is undefined and the handler throws before setCopied, so the button appears completely inert with the error only in the console.

- **`LOW` Per-card loading spinner during checkout** *(unverified)* — One click turns all six plan buttons into spinners and disables them simultaneously, so the UI cannot indicate which plan is being purchased.


### api-layer (frontend/src/api.ts) - the typed client for chat/SSE, config, admin/tenancy, health, integrations, incidents and on-call

- **`MED` Error-message quality on every GET/DELETE** — `get`/`del` discard the FastAPI `detail` body. A 503 from `POST /api/incidents/{id}/investigate` carries a structured `{"error":"llm_not_configured","message":"...","missing_configuration":[...]}` (app/api/main.py in app/incidents/router.py:252-262) and a 403 from `/admin/api-keys` carries the permi

- **`MED` Landing page error-rate chart fed by getMetrics()** — The endpoint and the contract (`services`, `error_summary.total_errors`, `error_summary.breakdown[].message/count`) match types.ts:163-171 exactly. But the consumer seeds state with a hardcoded curve and swallows the fetch error, so if /metrics 401s, 404s, or the deployment has no `checkout-svc.erro


### small-hooks (hooks/useConfig.ts, hooks/useMe.ts, hooks/useTheme.ts, hooks/useThemeColors.ts, lib/planLimits.ts, lib/emailService.ts)

- **`MED` Nav model tag (top-right pill showing the active model)** — The regex at useConfig.ts:86 requires two numeric groups (`claude-([a-z]+)-(\d+)-(\d+)`), so it only prettifies two-part versions. The current-generation ids claude-opus-5 / claude-sonnet-5 / claude-fable-5 have one numeric segment and fall through to `return id` (useConfig.ts:99), so the pill shows

- **`MED` "Backend offline — start the API" placeholder in the Sidebar** — getConfig throws a plain `Error("/config 401")` (frontend/src/api.ts:32-33) which useConfig.ts:40 swallows without inspecting the status. A 401 (backend up, credential missing) is reported to the operator with the same words as a connection refusal, and the suggested remedy — start the API — is wron

- **`MED` Plan limits on the public pricing page are fed by literals, not the API** — planLimits.ts's opening comment states the point of the module is "Reading plan limits and savings off the pricing API rather than restating them in the UI". On the public marketing page that never happens in a real deployment: the fetch 401s, the catch swallows it with no logging, and every rendere

- **`MED` Invitation email delivery after "Invite member"** — isEmailServiceConfigured() (emailService.ts:51-53) returns true as soon as VITE_EMAILJS_PUBLIC_KEY is set, but emailjs.send needs all three IDs to belong to the same EmailJS account. A deployer who sets only their public key silently sends against someone else's service/template IDs; validateParams/

- **`MED` Invitation email is sent entirely client-side; the backend never mails anything** — With the shipped .env.example (all EmailJS vars blank) no invitation email is ever sent by anything; emailService.ts:16-19 warns to console and returns false, and Team.tsx:287-289 honestly reports "(email not configured)". The invitee is not stranded — frontend/src/pages/Team.tsx:153 fetches GET /ap

- **`LOW` Duplicate GET /me on every Console mount** — Two independent GET /me requests fire per Console mount (four under React StrictMode's double-effect in dev) and one of the two results is thrown away. Harmless functionally, dead code with a network cost.


### landing-demo (components/landing: AIChat, Terminal, Dashboard, Metrics, GlobalMap, Pipeline, HowItWorks, FeatureCards, SecuritySection)

- **`MED` AIChat Play/Pause button** — Pause leaves the suspended runDemo promise permanently unresolved (the resolver is cancelled, never called), so the transcript freezes half-rendered and the partially typed prompt stays in the now re-enabled input. Play does not resume — it wipes the transcript and starts the same demo from characte

- **`LOW` AIChat copy-to-clipboard button on each code block** — navigator.clipboard is undefined in insecure contexts and writeText() rejects when the document lacks focus or permission. In every one of those cases the green check still renders for 2 seconds, telling the user the copy succeeded when nothing reached the clipboard (and producing an unhandled promi


### app-shell (App.tsx routing/auth-gating, config.ts, lib/apiClient.ts, types.ts)

- **`MED` Onboarding wizard completion is remembered** — Completion is device-local state the server never stores. The DB column stays false forever from the SPA's point of view, so the customer-health score at manager.py:732 never gets its +10, and the wizard replays on every new browser, incognito window or storage clear. There is currently no user-call

- **`LOW` types.ts GithubStatus.full_name / .private** — Two fields on the type contract that the backend never sends. Harmless today because nothing reads them, but they are an invitation to render `undefined` as a repo name later.

- **`LOW` /auth/callback under the local Vite dev server** — In development the browser lands on a stale built bundle or on a raw JSON error instead of the running dev SPA, so the token in the URL fragment is never consumed by AuthCallback.tsx:14-22. Production is unaffected (index.html is always present there). Dev-environment defect, not a user-facing one.


---

## 8. Working — the full list (401)

These were traced from the UI control to the backend handler and found correct: 
right method, right path, request body matching the request model, and every rendered 
field actually present in the response.


**knowledge (frontend/src/pages/KnowledgeBase.tsx)** (8)

- Article list load (initial + on filter change)
- Edit an existing article (pencil → Save)
- Create Knowledge Article modal
- Delete an article (trash icon)
- Pagination (Previous / Next, "Showing X–Y of N")
- Error banner with Retry
- Back navigation
- Modal dismissal while editing


**runbooks - pages/RunbookBuilder.tsx** (8)

- Test Run on a requires_approval runbook
- Save Draft (existing runbook)
- Publish button
- Step name / Description / Timeout / Requires approval / Continue on failure editors
- Command textarea
- Add / delete / reorder steps and step selection
- "Saved <time>" indicator
- Back arrow


**dashboard (frontend/src/pages/Dashboard.tsx)** (14)

- Acknowledge ("Ack") button
- Resolve button
- Execute runbook button
- Approve / Reject execution buttons
- Services stat card (count + "N healthy" / "health unmeasured")
- Runbooks stat card ("N" / "M available")
- Recent Executions panel
- Tab switcher (Overview / Alerts / Runbooks / Metrics)
- Refresh button and Back button
- Action-error banner + Dismiss
- Create Runbook button
- Quick-navigation grid (Incidents, On-Call, Service Map, Notifications, Analytics, SLOs, Runbooks, Postmortems, Integrations, Knowledge Base)
- "Configure Integrations" button (Metrics tab)
- Billing entry point


**oncall - frontend/src/pages/OnCall.tsx** (7)

- "Who's On-Call Now" card
- Team + schedule list load and 30s auto-refresh
- Week navigation (Previous / Next / Today)
- Create Override request itself
- Header Create Override enablement / tooltip
- Back button
- Authorization on every request


**team** (11)

- "You" badge on your own row / self-remove guard
- Change member role dropdown
- Remove member (trash button)
- Cancel pending invitation
- Send Invitation (invite modal form)
- Create team form (input + Create team button)
- Empty state ("You do not belong to a team yet")
- Invite Member button enable/disable state
- Error and success banners with dismiss
- Invite modal open/close and backdrop dismiss
- Back to Console button


**slos - frontend/src/pages/SLOTracker.tsx (SLO Tracker page, mounted at /slo)** (12)

- "View Detailed Metrics" burn-rate windows
- "Configure Alerts" — edit and save burn-rate threshold
- "Investigate with AI" button
- SLO list load + 30-second auto-refresh
- Error budget gauge (remaining / total minutes, % bar)
- Current SLI value + sample count on card
- Burn-rate indicator on card / in panel
- Time to Exhaustion estimate
- SLO card click → detail slide-over panel
- Back arrow (header)
- Loading spinner on first load
- Error banner Dismiss button


**incidents (frontend/src/pages/Incidents.tsx)** (14)

- Incident list table + 10s auto-refresh
- ConfidenceBar pass/fail threshold (0.8)
- Safety Gates card
- Recommended Actions list
- Investigation elapsed time
- Modal Cancel / backdrop dismiss / submit-disabled guard
- Acknowledge button
- Resolve button
- Detail view 10s re-read
- Error banner + Dismiss
- Row 'AI' indicator dot
- Back navigation (page header and detail header)
- Manual Refresh button
- 'Created' relative timestamps


**analytics (frontend/src/pages/Analytics.tsx)** (11)

- Time range selector (7d / 30d / 90d)
- Model Routing panel (per-model share bars, Total Routed)
- AI Involvement panel (Investigated, Of Those Resolved, Mean Confidence, MTTR vs Uninvestigated)
- Service Health table (Service, Incidents, Resolved, MTTR, Share)
- MTTR Comparison chart (Manual vs AI-Assisted)
- Metric card delta arrows and colour coding
- Truncation warning banner
- Back button
- Error banner and Dismiss button
- Auto-refresh / manual refresh
- created_at timestamp fallback


**servicemap** (18)

- Service map canvas (nodes + dependency edges)
- Per-service RPS / Err% / P99 on each node card
- Node cards ignore the topology payload's fabricated zero metrics
- Health status badge and border colour
- "N services · N recorded dependencies" header count
- Click a node to select / click again to deselect
- Click empty canvas to deselect
- Detail panel: "Depends on" / "Depended on by" lists
- Detail panel metrics rows + "no series" note
- "Live metrics unavailable — …" banner
- Empty / error state and its remediation instructions
- Refresh button
- Back button
- Detail panel close button
- Dependency edge lines and arrowheads
- Layered auto-layout
- Status legend (healthy / degraded / unhealthy / unknown)
- Auth header on both requests


**notifications - pages/NotificationPreferences.tsx** (20)

- Load preferences on mount
- Save Changes button
- "Saved!" confirmation
- Channel enable/disable toggle
- "Configured" checkmark on a channel card
- "credentials missing" warning
- "Cannot deliver in this build" notice (unavailable)
- "Nothing raises this event" notice (unproduced)
- Rules matrix switches
- Matrix switch disabled for blocked pairs
- Configure modal (open, edit, cancel)
- Secret handling in the Configure modal
- "Clear stored value" link
- Required-field enforcement in the Configure modal
- Quiet Hours enable checkbox and start/end times
- Digest Mode select
- No RBAC gating on an admin-only page
- Load-failure state (no mock fallback)
- Reload button
- Back button


**statuspage - frontend/src/pages/StatusPage.tsx (public status page)** (13)

- Load status summary on mount (GET /api/status)
- 30d / 90d uptime toggle
- Manual refresh button
- Auto-refresh every 60 seconds
- Services list (name, description, status, icon)
- Active Incidents card
- Incident expand / collapse (chevron)
- "Resolved" badge on recent incidents
- Incident duration display
- Scheduled Maintenance card
- Subscribe panel toggle (Bell button)
- Retry button on the error screen
- "Back to Console" button


**billing (frontend/src/pages/Billing.tsx + components/billing/*, api/billing.ts)** (18)

- Manage Subscription / Billing Portal button
- Confirm Cancellation
- Reactivate subscription
- Current-plan chip in the header
- Subscription-load failure banner + Retry
- SubscriptionCard facts (status badge, price, next billing date, renews-in, trial days, cancel notice, past-due notice)
- Plan cards: price, savings line, feature list, Most Popular badge
- Monthly/Annual toggle and "Save up to N%" badge
- "Unlimited" seat/quota labels
- AI Investigations usage bar and chart
- Usage chart "N in Mon YYYY" caption and empty state
- Tab navigation (Overview / Plans / Usage / Invoices / Settings)
- Back button
- Quick actions "View Plans" and "Usage Analytics"
- Contact Sales (Enterprise plan card)
- Cancel-modal two-step flow (reason radios, Continue gating, Back, close)
- Invoices empty state and "hosted invoice" link
- Auth headers on every billing request


**postmortem (frontend/src/pages/Postmortem.tsx)** (19)

- Load postmortem on page open
- Incident Metrics card (Time to Detect / Acknowledge / Mitigate / Resolve)
- Timeline tab
- Action item "Type" selector (prevent / detect / mitigate)
- "Affected Services" list
- Authors / Reviewers line
- Auto-generate the document when none exists
- "No model configured" / paywall message instead of a canned postmortem
- Regenerate section button
- Edit a section and Save
- Action item status dropdown
- Add Item form (title / owner / priority / type)
- Publish / Unpublish button
- Severity and Status badges
- Content / Timeline / Actions tabs
- "Try Again" button on the empty state
- Back arrow
- Error banner
- customer_impact / impact / generated_at


**marketplace - pages/IntegrationMarketplace.tsx + lib/integrationSpecs.ts** (8)

- Disconnect (Datadog / PagerDuty / Sentry / Traces)
- Capability chips (tool names) and "+N more" tooltip
- "Not available" cards and their reason text
- "Credentials saved" (yellow) status and "M awaiting first use" count
- "Integrations unavailable" error banner + dismiss
- Search box
- Back button
- Connect modal form (controlled inputs, password masking, required validation, close/Done)


**apidocs-profile (frontend/src/pages/ApiDocs.tsx, frontend/src/pages/Profile.tsx)** (19)

- Endpoint catalogue ("OmniPotent REST API — the complete API reference")
- Per-group "N endpoints" count
- API health status dot ("API Online" / "API Offline")
- Swagger UI link
- ReDoc link
- OpenAPI Spec link
- Base URL panel
- Endpoint group expand / collapse
- GitHub repo link
- ApiDocs back button
- Profile account fetch (GET /auth/me) for JWT sessions
- Role row
- Organization row
- Avatar / name / email header
- "Signed in with <provider>" rows
- "Member since" row
- Session token card
- Back to Console button
- Signing-out button disabled state


**auth-pages (pages/Login.tsx, pages/AuthCallback.tsx, pages/AcceptInvite.tsx)** (11)

- Login page "?token=" pickup
- OAuth provider discovery (GET /auth/providers)
- Backend OAuth redirect ("Continue with GitHub/Google" without Supabase)
- AuthCallback session-token capture from the OAuth redirect
- AuthCallback Supabase session adoption
- Forgot password with Supabase ("Send reset link")
- Forgot password on a deployment with no password store
- Accept invitation (POST /api/teams/invitations/{id}/accept)
- Invitation failure messages (403 wrong recipient / 404 expired)
- "Go to the team" / "Home" navigation buttons
- Password show/hide toggle, mode switcher, Back button, loading spinner


**history** (11)

- Share to Slack button
- Load investigation history (list fetch)
- Pagination Previous / Next
- Refresh button
- Tokens column
- Latency column
- Feedback thumbs indicator
- Markdown / HTML / JSON export buttons
- Row click to expand/collapse
- Empty / loading / error states
- Back to Console button


**queries (SavedQueries.tsx, PromptSuggestions.tsx, useSavedQueries.ts)** (1)

- Edit query label (pencil → input → check)


**admin-config (ModelConfig, AdminPanel, SourceConnect, GithubConnect)** (19)

- Model catalog load (GET /api/models)
- Model price labels in the dropdowns
- "Prices last verified {date}" note
- "Server default (X)" label on the empty dropdown option
- Offline fallback model list
- Provider dropdown (5 providers)
- API-key input in the model picker + "stored in memory only" note
- Admin console load + single-tenant / outdated-build notices
- Usage card + quota progress bar
- "Approaching your quota" warning
- Issue API key (name + role → new secret)
- Revoke API key
- Escape-to-close / close button on the Admin console
- Source path display in the sidebar cards
- Logs directory "missing files" warning
- GitHub connection status dot and repo label
- GitHub Disconnect
- "Forget saved credentials" button
- "Get a GitHub token" link


**shell-ui (Sidebar, ThemePicker, SignupModal, ErrorBoundary, Skeleton)** (18)

- Sidebar connector status badges (live / offline demo / local / unknown)
- Model card: provider pill + main/fast model IDs
- Model card online / "no API key" pill
- MCP server list, count, and per-server tool chips
- Logs / repo source pickers rendered per server card
- Sidebar loading / offline placeholder
- "Need help getting started?" documentation link
- Theme picker: open/close, select theme, persist across reload
- Theme menu keyboard navigation (Up/Down/Home/End/Escape)
- Signup modal: create organization
- Signup error messages surfaced inline
- Signup client-side email validation is weaker than the server's
- Copy API key button
- Signup modal Escape / close / scroll lock
- ErrorBoundary catches render crashes
- ErrorBoundary "Your investigation history is safe" claim
- ErrorBoundary "Try again" button
- Skeleton / SkeletonBlock loading placeholders


**useWebSocket - real-time chat transport (frontend/src/hooks/useWebSocket.ts)** (7)

- WebSocket URL construction and dev-proxy routing
- Bearer-token auth over the WebSocket subprotocol
- Send a question over WebSocket and stream the investigation
- Approve / reject a pending write action over WebSocket
- Client-initiated keepalive ping (every 25s)
- Connection confirmation / session id capture
- probeWebSocket availability check on mount


**useCopilot - the copilot conversation hook (frontend/src/hooks/useCopilot.ts) and its only consumer, components/Console.tsx** (7)

- Send a question (SSE transport) and stream the answer
- Live agent-activity trace during an investigation
- Final answer, RCA report card and token count
- Conversation persistence across reload / navigating home
- Retry button on a failed turn
- Input locked while an approval is pending
- WebSocket availability probe on mount


**console (Console.tsx, Composer.tsx, Message.tsx)** (19)

- Ask an investigation (Send / suggestion prompt) — SSE transport
- Live agent activity trace
- Token count badge ("N tokens")
- Approve / Reject a risky action
- Retry a failed investigation
- Ask an investigation — WebSocket transport (default) message delivery
- Per-turn feedback attribution
- Online / offline status dot
- Model badge in the top bar
- Top-bar navigation (History, Team, Status, Dashboard, Billing, Home)
- Admin button
- Empty-state suggestion chips
- Empty-state tip ("Click Change model in the sidebar")
- Cmd/Ctrl+Enter to send, Enter for newline
- Save current query button (lock icon)
- RCA report card
- Inline error message on a failed turn
- Composer disabled while awaiting approval
- Auto-scroll to the newest message


**rca-feedback (FeedbackButtons.tsx, RcaReportCard.tsx, ApprovalCard.tsx, ActivityTimeline.tsx)** (22)

- FeedbackButtons — modal Cancel / X close buttons
- ApprovalCard — "Approve & run" button
- ApprovalCard — "Reject" button + optional reason input
- ApprovalCard — Approve/Reject over the WebSocket transport (default path)
- ApprovalCard — "Confidence gate" warning banner
- ApprovalCard — "Investigated with N tool results" evidence counter
- ApprovalCard — proposed action list (WRITE badge, risk chip, tool name, preview, why, args)
- RcaReportCard — collapse/expand header toggle
- RcaReportCard — severity badge ("SEV1 · Critical" etc.)
- RcaReportCard — confidence chip ("high/medium/low confidence")
- RcaReportCard — "Insufficient evidence" abstain block + needs list
- RcaReportCard — summary, root cause, affected-services chips
- RcaReportCard — adversarial critique block + standing objections
- RcaReportCard — ranked hypotheses with verdict icons + cited evidence
- RcaReportCard — flat "Evidence" list
- RcaReportCard — "Recommended actions" ordered list
- RcaReportCard — "Fix verification" block (verdict label, confidence, rationale, resolution criteria, residual risks)
- RcaReportCard — sandbox counterfactual line ("Sandbox counterfactual: resolved — …")
- RcaReportCard — "Download postmortem" button
- ActivityTimeline — live agent trace list
- ActivityTimeline — "working…" live indicator
- ActivityTimeline — empty-state suppression


**auth-hooks (frontend/src/hooks/useSupabaseAuth.ts, frontend/src/lib/supabase.ts, frontend/src/lib/authToken.ts)** (7)

- Supabase config fetch on a cross-origin deployment
- Password reset link ("Send reset link")
- Authorization header on every API request
- WebSocket authentication (chat stream)
- Credential precedence (login session > pasted API key > build-time token)
- Credential survives hostile storage environments
- Logout clears both credential kinds


**onboarding - frontend/src/components/OnboardingWizard.tsx (5-step setup wizard mounted at /onboarding, App.tsx:83-89)** (12)

- Save and Continue (alert routing + Slack webhook)
- Alert routing toggle initial state
- Playground "Run Investigation"
- Playground trace lines and "Investigation Complete" panel
- Non-GitHub integration hints ("Needs DD_API_KEY, DD_APP_KEY…")
- Service Catalog knowledge card
- Terminology and Best Practices knowledge cards
- Knowledge card "Added \"X\"" confirmation
- Knowledge card Add / Save / Cancel buttons
- Static "What happens when an alert arrives" explainer
- Step navigation (progress pills, Back, Skip Setup, "Step X of 5")
- "Start Investigating" / onboarding completion


**landing-copy (Navbar, HeroSection, Pricing, Documentation, Blog, Footer, legacy Landing.tsx)** (13)

- Monthly/Yearly billing toggle + "Save up to N%" badge
- Seat-limit line on each plan card
- Plan feature bullet list
- "Get Started Free" (Starter) and "Contact Sales" (Enterprise) buttons
- Navbar section links (Features / Pipeline / Docs / Blog / Pricing)
- Navbar "API" link and Documentation Swagger/ReDoc/OpenAPI links
- Theme switcher (10 themes)
- "Launch Console" / "Sign in" / "Start Free Today" CTAs
- Mobile menu open/close
- "Open Documentation" expander and sidebar section tabs
- Documentation Quick Start commands
- Documentation API Reference endpoint table
- Footer link columns, social icons and copyright


**api-layer (frontend/src/api.ts) - the typed client for chat/SSE, config, admin/tenancy, health, integrations, incidents and on-call** (23)

- Console header liveness dot (health)
- WebSocket availability probe (healthExtended / isWebSocketEnabled)
- Integration status badges (getIntegrationStatus)
- Agent config display (getConfig)
- Identity label (getMe)
- Model picker catalog (getModelCatalog)
- Switch LLM provider/model (configureModel)
- Connect a local repo directory (connectRepo)
- Connect a local logs directory (connectLogs)
- Reset runtime config to .env defaults (resetConfig)
- Connect GitHub repo (githubConnect)
- Disconnect GitHub (githubDisconnect)
- Usage / quota panel (getUsage)
- Audit trail list (getAudit)
- Verify audit hash chain (verifyAudit)
- Organization summary (adminOrg)
- List API keys (adminListKeys)
- Issue a new API key (adminCreateKey)
- Revoke an API key (adminRevokeKey)
- Self-serve signup (signup)
- Thumbs up/down feedback (submitFeedback)
- Share an investigation report to Slack (shareToSlack)
- Non-streaming chat / approve fallbacks


**small-hooks (hooks/useConfig.ts, hooks/useMe.ts, hooks/useTheme.ts, hooks/useThemeColors.ts, lib/planLimits.ts, lib/emailService.ts)** (11)

- Sidebar "Model" card — provider pill, main model, fast model
- Sidebar "online" / "no API key" badge
- Sidebar "MCP Servers · N" count, server labels and tool chips
- Config auto-refresh after Change model / GitHub connect / source connect
- Config fetch retry / auto-recovery
- `failed` flag after a manual refresh error
- Theme picker — select a theme, apply it, persist it
- Theme menu keyboard navigation (Up/Down/Home/End/Escape) and outside-click close
- Theme-reactive colors in the landing hero and footer 3D scenes
- Plan seat-limit line ("Unlimited users" / "N users") on plan cards
- "Save up to N%" badge beside the monthly/annual toggle


**landing-demo (components/landing: AIChat, Terminal, Dashboard, Metrics, GlobalMap, Pipeline, HowItWorks, FeatureCards, SecuritySection)** (9)

- Pipeline "Replay" button
- Terminal "Replay" button
- AIChat quick-prompt chips (4 buttons)
- AIChat "Next example" replay button
- GlobalMap region cards and 3D marker hover (selects which region the detail panel shows)
- GlobalMap 3D globe, orbit controls, connection arcs and data packets
- HowItWorks step indicator, Play/Pause and Reset controls
- HowItWorks "Try It Yourself" CTA
- SecuritySection "Audit Logging" badge


**app-shell (App.tsx routing/auth-gating, config.ts, lib/apiClient.ts, types.ts)** (11)

- Client-side routing + deep links + hard refresh (23 routes)
- Console top-bar navigation (Home / Dashboard / Billing / History / Team / Status / Profile / Logout)
- Dashboard feature-tile navigation (10 sub-pages)
- Team invitation link (/invite/:invitationId)
- apiFetch / apiUrl — per-call bearer credential on every backend call
- describeFailure — human-readable error text from a failed response
- wsUrl — WebSocket URL for the live investigation stream
- config.ts API_BASE_URL (the "Base URL" the API Docs page prints as fact)
- types.ts contract mirror for /me, /config, /chat and the RCA report
- Unknown-URL catch-all
- Root-level ErrorBoundary


---

## 9. Could not be settled without a live account (2)

- **Incidents client block (list/get/create/acknowledge/resolve/investigate/investigation/timeline)** (api-layer (frontend/src/api.ts) - the typed client for chat/SSE, config, admin/tenancy, health, integrations, incidents and on-call) — Paths, methods, query-param names (`user`, `notes`, `status`, `severity`, `service`, `limit`) and response shapes for list/timeline/investigation all check out — `acknowledge`/`resolve` correctly send `user` as a query param and the backend now defaults it to the authenticated principal (router.py:4
- **On-call client block (teams/current-oncall/alerts list)** (api-layer (frontend/src/api.ts) - the typed client for chat/SSE, config, admin/tenancy, health, integrations, incidents and on-call) — This is the exact prefix trap called out in the brief and api.ts gets it right. `OncallShift` (api.ts:478-486) is declared but never fetched by any function here. No importer, so unverifiable in practice.

---

## 10. Coverage — what the audit reached, and what has no UI at all

### 10.1 Frontend files the audit never touched

Of 96 non-test source files under `frontend/src`, **92 were examined**. The four that
were not:

| File | State |
|---|---|
| `components/Hero3D.tsx` | **Dead** — no importer anywhere, not even a barrel export. |
| `components/billing/FeatureGate.tsx` | **Unused in the app** — exported from `components/billing/index.ts` and has its own test, but no production component renders it. The whole plan-gating mechanism is inert. |
| `components/landing/Scene3D.tsx` | **Shadowed** — `HeroSection.tsx:215` defines its own local `Scene3D` and renders that (`:272`). The standalone file is only reachable via the barrel export, which nobody imports. |
| `components/landing/CursorGlow.tsx` | **Live** but purely decorative (`CinematicLanding.tsx:57`). No behaviour to audit. |

(`src/test/setup.ts` and `src/vite-env.d.ts` are not features.)

### 10.2 Backend routes with no frontend caller

**105 of the 343 live routes are not referenced anywhere in `frontend/src`.** Thirteen
whole families have zero references — not a single mention of the path prefix:

| Family | Routes | Note |
|---|---:|---|
| `/api/cost` | 22 | Cost analysis / optimisation. No UI. |
| `/api/planning` | 20 | No UI. |
| `/api/event-intelligence` | 11 | No UI. |
| `/api/slack` | 10 | No UI (Slack posting is server-side only). |
| `/api/verification` | 9 | Fix-verification; the RCA card renders a verification block from the report payload instead. |
| `/api/dependency-graph` | 8 | No UI. The Service Map uses `/api/knowledge-graph/*`. |
| `/customers` | 6 | CRM. No UI. |
| `/api/deduplication` | 5 | **Sold on the pricing page** — `Pricing.tsx:42/48/72/78/102/108` gate `basic_deduplication` / `advanced_deduplication` per plan, and no screen anywhere exposes the feature. |
| `/api/webhooks` | 5 | Distinct from the `/webhooks` receivers, which are used. |
| `/scim` | 3 | Directory sync. No UI. |
| `/waitlist` | 3 | The blog's "Subscribe via Email" CTA opens a mail client instead of calling this. |
| `/api/dashboard` | 2 | `GET /api/dashboard/metrics` and `/trends` exist and are purpose-built — and `Dashboard.tsx:575-609` ignores both, fanning out to five other endpoints instead and then computing the numbers client-side. This is the direct cause of several of the Dashboard defects in §7. |
| `/api/metrics` | 1 | The landing chart uses the bare `/metrics` instead. |

Partial families with unused members include `/oncall` (21 of 44 unused — `/oncall/ai/*`,
`/oncall/swaps*`, `POST /oncall/teams/{id}/members`, `POST /oncall/schedules/{id}/generate-shifts`,
the last two being the direct cause of §3.9) and `DELETE /api/slos/{id}` (which is why the
SLO page has no delete control).

### 10.3 One inconsistency worth fixing while you are in there

`lib/supabase.ts:33` calls `fetch('/frontend-config')` — a **relative** URL. Every other
request in the app goes through `apiFetch`/`apiUrl` and is resolved against `API_BASE`.
On any deployment where the SPA and the API are not same-origin, the Supabase bootstrap
silently fails and the entire auth UI disappears with no error.

Relatedly, the API base is derived independently in three places (`config.ts`,
`lib/apiClient.ts`, and this line), while `config.ts`'s own header comment still claims to
be the single source of truth.
