# Supabase inventory

Audit date: 2026-10-01

## Repository state

- Commit inspected: `1ce3ea9bbe82f0beb07676cd3f90f024c2a29dd7`
- Working tree: already dirty before this audit. Existing modified frontend files
  and untracked `docs/console-redesign/`, `frontend/src/components/ConsoleRightRail.tsx`,
  and `frontend/src/components/IntegrationLogo.tsx` were preserved.
- No application behavior, dependencies, environment files, deployment manifests,
  or database code were changed during this audit. This inventory document is the
  only new file from the audit phase.

## Search scope and result

The repository was searched with `rg` across source, tests, configuration, Docker,
Helm, CI, deployment, and documentation, excluding `.git`, dependency folders,
and generated build output. Search terms included:

`supabase`, `@supabase`, `createClient`, `SUPABASE_URL`, `SUPABASE_KEY`,
`SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `NEXT_PUBLIC_SUPABASE`,
`VITE_SUPABASE`, `auth.getUser`, `auth.getSession`, `auth.signIn`,
`auth.signOut`, `storage`, `channel(`, `rpc(`, `realtime`, and `postgres_changes`.

No application calls were found for Supabase Storage, Supabase Realtime channels,
`postgres_changes`, Supabase RPC, or Supabase Edge Functions. Generic occurrences
of the word “storage” and unrelated notification channels are not Supabase
dependencies.

## Runtime dependency inventory

| Location | Supabase feature | Current purpose | Client/server | Replacement | Migration risk |
| --- | --- | --- | --- | --- | --- |
| `frontend/src/lib/supabase.ts` | Auth client/config | Lazy-loads `@supabase/supabase-js`, fetches `/frontend-config`, initializes a browser client, probes enabled OAuth providers, and exposes session helpers | Client | Firebase Web Auth in one centralized module | High: session persistence, provider redirects, and error behavior must remain compatible |
| `frontend/src/hooks/useSupabaseAuth.ts` | Auth state | Restores sessions, listens to `onAuthStateChange`, signs in/up/out, resets passwords, and mirrors access tokens into the API token store | Client | Firebase Auth `onAuthStateChanged`, provider SDKs, and Firebase ID tokens | High: protected routes and API authorization depend on this hook |
| `frontend/src/pages/Login.tsx` | Auth UI integration | Calls the Supabase auth hook and provider discovery for email/password, GitHub, Google, signup, reset, and demo entry | Client | Existing UI backed by Firebase/backend auth | High: preserve current flows; demo mode must not become an auth bypass |
| `frontend/src/pages/AuthCallback.tsx` | OAuth callback | Consumes backend OAuth tokens first, otherwise reads a Supabase session from the callback URL and stores its token | Client | Firebase provider redirect handling plus existing backend callback path | High: avoid breaking existing OAuth routes and redirects |
| `frontend/src/pages/History.tsx` | Direct database client query | Reads `investigations` directly with `.from()`, ordering, status filtering, and range pagination when Supabase is configured; otherwise calls the backend endpoint | Client | Use the existing authenticated `/api/investigations` endpoint; do not expose operational tables to Firestore without review | High: direct client query currently relies on RLS and has different behavior from the API path |
| `frontend/src/pages/Profile.tsx` | Auth identity/session | Uses the auth hook for current user, session, and sign-out while `/auth/me` supplies backend profile data | Client | Firebase Auth identity plus existing `/auth/me` profile endpoint | High: separate identity from application profile and preserve backend authorization |
| `frontend/src/pages/Team.tsx` | Auth identity | Uses the auth hook for current user and team UI fallback behavior | Client | Firebase Auth user plus existing team API | Medium/high: user ID mapping and tenant membership must remain server-controlled |
| `frontend/src/components/Console.tsx` | Auth identity/logout | Uses the auth hook to determine signed-in state and sign out | Client | Central Firebase Auth provider/hook and backend logout/revocation behavior | High: logout must clear Firebase state and the backend credential |
| `frontend/src/lib/authToken.ts` | Session credential bridge | Labels and stores Supabase access tokens alongside backend OAuth tokens in `session_token` | Client | Firebase ID-token abstraction with refresh; avoid manually persisting Firebase tokens | High: API clients and stale-token eviction depend on this contract |
| `app/tenancy/supabase_auth.py` | JWT verification | Verifies Supabase-issued RS256/ES256 tokens with the project JWKS, audience, expiry, and signature checks | Server | Firebase Admin `verify_id_token()`; retain explicit tenant membership lookup | High/security-critical: invalid, expired, forged, wrong-project, and revoked token tests required |
| `app/tenancy/auth.py` | Auth-to-tenant mapping | Resolves verified Supabase claims by email to existing org/role membership and integration secrets | Server | Firebase UID/email identity mapping into the same tenant store | High: do not trust browser-supplied org, role, or UID fields |
| `app/api/main.py` | Auth routing and callback | Accepts JWT-shaped credentials, tries Supabase JWT verification then local OAuth session verification, serves `/auth/callback`, adds Supabase URL to CSP, and exposes `/frontend-config` | Server | Firebase Admin verification, existing OAuth callback if retained, Firebase config endpoint, and CSP update | High: order of credential verification and protected-route behavior must not regress |
| `app/teams/router.py` | Auth verification | Imports Supabase JWT verification for team authorization paths and documents its optional behavior | Server | Shared Firebase token verifier / tenant auth resolver | High: team membership and role checks must remain server-side |
| `app/tenancy/scim.py` | Auth documentation/reference | References the Supabase/OIDC JWT verifier while SCIM uses its own IdP bearer token | Server | Update documentation/comments to Firebase/IdP terminology without changing SCIM semantics | Medium |
| `app/database/supabase_client.py` | Database client/admin | Provides anon and service-role Supabase clients plus Supabase-hosted PostgreSQL URL handling | Server | Remove Supabase client; use the existing SQLAlchemy/PostgreSQL repository path and deployment-provided database URL | Critical: current repositories call this client for operational data and secrets |
| `app/database/repositories.py` | Database persistence | CRUD for investigations, incidents, organizations, and audit logs through Supabase REST tables, with in-memory fallbacks when unconfigured | Server | Preserve relational SQLAlchemy/PostgreSQL semantics; replace only the hosting/client dependency | Critical: transactions, joins, constraints, tenant isolation, and audit integrity |
| `app/database/__init__.py` | Database exports | Re-exports Supabase client helpers as part of the database package API | Server | Replace exports with the selected database abstraction | Medium |
| `app/database/migrations/001_initial_schema.sql` | PostgreSQL/RLS | Defines relational tables, indexes, foreign keys, RLS policies using `auth.jwt()` and service-role policies; comments target Supabase SQL Editor | Server/database | Keep relational schema; adapt identity/RLS integration only after choosing the PostgreSQL host and server authorization model | Critical: do not mirror these tables into Firestore automatically |

## Configuration and deployment inventory

| Location | Supabase feature | Current purpose | Client/server | Replacement | Migration risk |
| --- | --- | --- | --- | --- | --- |
| `app/config.py` | Auth/database config | Defines `SUPABASE_JWKS_URL`, `SUPABASE_JWT_AUD`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, and `SUPABASE_DATABASE_URL` | Server | Firebase Admin runtime credentials/config; a neutral PostgreSQL URL for the retained relational database | High: remove only after all callers migrate; do not remove unrelated PostgreSQL settings |
| `.env.example` and `.env` | Runtime configuration | Documents/provides Supabase auth, REST, service-role, and database variables | Server/dev | Firebase variables and neutral database configuration; `.env` must remain local and secrets must not be committed | High |
| `frontend/.env.example` | Browser configuration | Documents `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` | Client | Required `VITE_FIREBASE_*` values only for services actually used | Medium |
| `frontend/package.json` / `frontend/package-lock.json` | SDK dependency | Declares `@supabase/supabase-js` | Client | Modular `firebase` package, after Firebase implementation and tests exist | High: remove only after runtime references are gone |
| `pyproject.toml` | Python dependency | Optional `supabase` extra plus `asyncpg`/SQLAlchemy convenience dependencies; `pyjwt` comment references Supabase/OIDC | Server | Firebase Admin SDK and existing SQLAlchemy/asyncpg path; keep `pyjwt` only if still required by other JWT flows | High |
| `Dockerfile` | Runtime configuration | Notes Supabase runtime injection | Build/deploy | Firebase-safe runtime configuration documentation | Low/medium |
| `docker-compose.yml` | Runtime configuration | Passes `SUPABASE_URL` and `SUPABASE_ANON_KEY` to the app | Deploy | Pass Firebase Admin/runtime config securely and retain database variables separately | Medium |
| `render.yaml` | Deployment config | Publishes server and Vite Supabase variables, JWKS URL, and Supabase database URL | Deploy | Firebase runtime/build configuration and neutral relational DB URL | High |
| `deploy/helm/devops-copilot/values.yaml` | Kubernetes secrets | Defines Supabase URL, anon key, and service-role values | Deploy | Firebase Admin secret references plus retained relational DB secret | High |
| `deploy/helm/devops-copilot/templates/secret.yaml` | Kubernetes secret templating | Renders Supabase environment variables into a Kubernetes Secret | Deploy | Render only approved Firebase Admin/runtime secrets; never service-account material in browser/build output | Critical/security |
| `app/security/headers.py` and `app/api/main.py` | CSP | Allows Supabase URL/websocket origins in `connect-src` | Server | Allow only Firebase Auth endpoints actually used and existing API origins | Medium |

## Tests, audits, and documentation

| Location | Classification | Current dependency | Replacement/action | Risk |
| --- | --- | --- | --- | --- |
| `frontend/src/hooks/useSupabaseAuth.test.ts` | TEST | Tests Supabase hook session/sign-in behavior | Replace with Firebase Auth adapter tests, including refresh/logout/error cases | High |
| `frontend/src/pages/Login.test.tsx` | TEST | Mocks Supabase config/provider discovery and auth hook | Replace mocks with centralized Firebase/backend auth adapter mocks | High |
| `frontend/src/pages/History.test.tsx` | TEST | Mocks `initSupabase` and tests fallback behavior | Test authenticated API history path; remove direct Supabase branch tests | Medium/high |
| `frontend/src/pages/Profile.test.tsx`, `Team.test.tsx` | TEST | Comments and mocks describe Supabase identity | Update to Firebase identity/backend profile semantics | Medium |
| `tests/test_supabase_auth.py` | TEST | Unit-tests Supabase JWKS decoding | Replace with Firebase Admin token verification tests, including invalid project/audience and expiry | Critical/security |
| `tests/test_auth_session_contract.py`, `test_teams_auth.py`, `test_teams_persistence.py`, `test_tenancy_api.py` | TEST | Contracts/comments/env setup include Supabase JWT/JWKS paths | Update credential matrix to Firebase ID tokens plus retained local/API-key flows | High |
| `tests/test_integration_api.py`, `tests/test_security.py` | TEST | Assert `/frontend-config`, CSP, and Supabase URL behavior | Assert Firebase-safe config/CSP and no admin credentials in browser config | High/security |
| `FRONTEND_AUDIT_V2.md`, `AUDIT_REPORT.md` | DOCUMENTATION/AUDIT | Record known Supabase auth/history/token defects | Preserve as historical audit or update a migration cross-reference; do not treat historical mentions as runtime dependencies | Low |
| `README.md`, `docs/COMMERCIALIZATION.md`, `docs/user-guide.md` | DOCUMENTATION | Describe Supabase persistence/auth in product/deployment guidance | Update after implementation and cutover design | Medium |
| `app/database/__init__.py`, migration comments, and source docstrings | DOCUMENTATION/IMPLEMENTATION | Describe Supabase as the database/auth provider | Update alongside the replacement abstraction | Medium |

## Feature decisions from the audit

### AUTH

Migrate to Firebase Authentication. The backend must verify Firebase ID tokens
with Firebase Admin and then resolve the existing organization membership and
role. Firebase identity is not itself tenant authorization.

### DATABASE

Retain the relational PostgreSQL/SQLAlchemy model. The schema includes
organizations, API keys, members, investigations, investigation events,
audit logs, alert rules/events, runbook executions, usage records, and
incidents, with foreign keys, unique constraints, indexes, transactions/RLS
expectations, and operationally sensitive state. This is not a safe blind
Firestore conversion. The hosting/client dependency can be removed separately.

### STORAGE

No Supabase Storage API or bucket usage was found. No Firebase Storage service
should be added unless a later audit finds an actual file-storage requirement.

### REALTIME

No Supabase Realtime subscription was found. Existing WebSocket/SSE/LangGraph
streaming is outside the Supabase migration scope and must be preserved.

### FUNCTION / ADMIN

No Supabase RPC or Edge Function call was found. Existing FastAPI routes remain
the preferred home for business logic. Supabase service-role REST access in
`supabase_client.py` is a server admin/database dependency and must be removed
only after repositories have a relational replacement.

### USER ID / PROFILE

The current tenant mapping commonly resolves membership by email, while the
relational schema also has member/user identifiers. Before cutover, determine
whether production Supabase users exist and create an explicit old-identity to
Firebase-UID mapping. Application profile and tenant membership stay in the
existing backend database.

## Migration blockers and next phase

1. Confirm whether production users/data currently exist and where PostgreSQL is
   hosted in each deployment.
2. Decide the neutral PostgreSQL connection variable and whether the existing
   REST repository can be removed in favor of the SQLAlchemy repository layer.
3. Add Firebase Admin/Web SDK dependencies only after the architecture and
   identity mapping are documented.
4. Implement and test Firebase token verification before removing Supabase auth.
5. Keep Supabase production resources untouched during implementation; no remote
   deletion, data deletion, credential rotation, or deployment is authorized by
   this audit.

## Audit conclusion

The repository has real Supabase runtime dependencies. The migration is not yet
complete and no claim of Firebase compatibility should be made until the auth
and repository paths above are replaced and the regression/security matrix is
passing.
