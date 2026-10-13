# Customer journeys

These journeys describe the supported read-only customer wedge. They are
contracts for the current application, not claims that external providers or
customer environments have already been verified.

## A. First-time customer to first evidence

1. User signs in and creates or selects an organization.
2. User selects `READ_ONLY` mode; control mode remains disabled by default.
3. User opens Integrations and chooses a supported connector.
4. Backend validates credentials, scope, reachability, authorization, and data
   freshness. The UI shows the actual result: connected, empty, stale, denied,
   unreachable, expired, or error.
5. User maps a service to observed source identity and reviews inferred links.
6. User creates or receives an incident and launches an investigation.
7. User opens source-linked evidence and sees missing/contradictory data and
   uncertainty rather than fabricated success.

Acceptance: no source-code change is needed after signup; every displayed
observation has a source, timestamp, tenant, and evidence identifier.

## B. Alert to collaborative report

1. Alert intake persists and deduplicates the event before acknowledgement.
2. Incident workspace shows severity, service, environment, trigger, source
   times, activity, assignment, and lifecycle state.
3. Investigation runs within the immutable incident scope and bounded budget.
4. Workbench shows agent progress, observations, hypotheses, contradictions,
   and next tests; it never exposes hidden chain-of-thought.
5. Engineer adds a comment or authored correction and optionally exports a
   report. Sharing is limited to authorized organization members.
6. History remains available after refresh and a later authenticated session.

Acceptance: alert resolution and action completion are separate from verified
customer recovery.

## C. Recovery suggestion review (gated later release)

1. Agent proposes a typed action with exact tenant, resource, revision,
   preconditions, expiry, policy version, and observation plan.
2. Deterministic policy and capability checks deny malformed or drifted intent.
3. Authorized approver reviews the exact digest; approval cannot be replayed or
   transferred to another resource.
4. Optional customer runner performs only the approved action.
5. Independent observations classify the result as VERIFIED, FAILED, or
   INCONCLUSIVE. Provider/API success alone never means recovery.

This journey is disabled for production until named-cluster and recovery
evidence exists.

## State display contract

| State | Required UI behavior |
|---|---|
| LOADING | Show progress and preserve the current scope |
| EMPTY | Explain that no source data was observed; never show zero as healthy |
| ERROR | Show safe diagnostic detail and a retry path |
| PERMISSION DENIED | Identify the missing scope without exposing secrets |
| DISCONNECTED | Show the connector/source and reconnection action |
| DEMO | Persistent synthetic-data label and no customer-data controls |
| PARTIAL EVIDENCE | Show missing sources and what was still observed |
| INCONCLUSIVE | Show why a conclusion cannot be proven and next test |
