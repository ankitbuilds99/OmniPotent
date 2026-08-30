"""Deployment must stay single-replica while authoritative state is in-process.

Several modules keep authoritative business state — customers, trials, billing
usage records, investigation history, runbook executions — in module-level Python
dicts with no persistence layer behind them. That state is per-process, so with
more than one replica and no session affinity a customer created on pod A does
not exist on pod B, and the usage records billing is computed from are split
across pods and destroyed on every restart, deploy and scale-down.

The manifests were pinned to a single replica because of that. A plain "assert
replicas == 1" test would be wrong, though: it would outlive its own reason and
block scaling forever. So this module tests the *link* instead —

    in-memory stores still exist  <->  manifests pin to 1 replica

Both directions are enforced. Raising a replica count while the stores are still
in-process fails; externalising the stores and leaving the pin in place also
fails, with a message telling you to lift it. The test retires itself.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Stores that must move to Postgres/Redis before replicas can be raised, as
# (module path, store name). Recorded explicitly rather than discovered so that
# deleting a store is a deliberate edit here, not a silent change in behaviour.
#
# Removed from this list because they were externalised, not because they were
# forgotten:
#   app/customers/manager.py  _customer_store / _email_index / _trial_store /
#                             _usage_store  ->  Postgres (customers, trials,
#                             customer_usage_records; migration 015_growth).
#                             This line used to name crm_customers / crm_trials
#                             / crm_usage_events (migration 002_crm), which is a
#                             different, older set of tables that the manager
#                             does not read: it imports growth_models, so an
#                             operator following this ledger to find a lost
#                             customer would have queried three empty tables.
#                             tests/test_customers.py::TestPersistence covers it.
#   app/dashboard/metrics.py  _investigation_store  ->  Postgres (investigations;
#                             migration 003_investigations). One row per
#                             investigation thread, written by every completion
#                             path in app/api/main.py and read by /dashboard/*.
#                             tests/test_dashboard_api.py covers it.
#   app/planning/api_router.py     _graphs             ->  Postgres (planning_graphs,
#   app/coordinator/api_router.py  _active_executions      coordinator_executions;
#                             migration 004_planning_coord). Both keyed on
#                             (id, org_id) — the ids are caller-supplied, so an
#                             unscoped lookup was cross-tenant reachable.
#                             tests/test_planning_persistence.py and
#                             tests/test_coordinator_persistence.py cover them.
#   app/runbooks/router.py    _runbooks / _executions  ->  Postgres
#                             (runbook_definitions, runbook_runs; migration
#                             005_runbooks). The built-in DEFAULT_RUNBOOKS are
#                             seeded per org rather than shared, so one tenant's
#                             edit no longer rewrites everyone's.
#                             tests/test_runbook_persistence.py covers it.
#   app/waitlist/manager.py   _waitlist_store / _email_index / _position_counter
#                             ->  Postgres (waitlist_signups; migration
#                             006_waitlist). The one table here that is NOT
#                             org-scoped: a signup happens before the person has
#                             an organization. `position` is derived from
#                             MAX(position)+1 and is unique, replacing a counter
#                             that reset to 0 on restart and reissued positions
#                             earlier signups already held.
#                             tests/test_waitlist_persistence.py covers it.
#   app/teams/store.py        TeamStore._teams / _members / _invitations /
#                             _user_teams  ->  Postgres (teams, team_members,
#                             team_invitations; migration 007_teams). Never on
#                             the list below — it was found late, because the
#                             dicts were instance attributes on a singleton
#                             rather than module-level names, so the scan that
#                             built this list did not see them. Its router was
#                             wired the whole time, so teams, memberships and
#                             outstanding invitations were destroyed on every
#                             restart. `_user_teams` got no table: it was a
#                             hand-maintained index that is now a query.
#                             tests/test_teams_persistence.py covers it.
#   app/auth/oauth.py         _OAUTH_ACCOUNTS / _USER_ACCOUNTS / _OAUTH_STATES
#                             ->  Postgres (oauth_accounts, oauth_states;
#                             migration 008_oauth). The worst one found so far,
#                             and it was not on the list below either: the
#                             callback mints a new `user_id` whenever the account
#                             lookup misses, so with the accounts in a dict every
#                             restart returned each existing user as a brand-new
#                             one and stranded their memberships and history
#                             under an id nobody could sign in as again. The
#                             state dict separately broke login outright above one
#                             replica, since the redirect and the callback need
#                             not reach the same pod. `_USER_ACCOUNTS` got no
#                             table; it is a query now.
#                             tests/test_oauth_identity_persistence.py covers it.
#   app/audit_legacy.py       _BUFFER
#                             ->  Postgres (audit_events; migration 009_audit).
#                             Found by the factory-call scan below, and the only
#                             one so far that was already broken on a single
#                             pod: the buffer was a `deque(maxlen=2000)`, so at
#                             the 2001st event it evicted the genesis entry and
#                             /audit/verify -- which recomputes from
#                             `prev_hash == ""` -- reported the trail as
#                             tampered with, permanently, with nothing wrong.
#                             Below 2000 it had the usual per-pod problem in the
#                             opposite direction: `valid: true` over one
#                             replica's fraction of the events. The chain is per
#                             org now and its position is a unique
#                             `(org_id, seq)`, which is what lets several
#                             processes append to a serial chain at once.
#                             tests/test_audit_chain_persistence.py covers it.
#   app/slo/tracker.py        _SLOS / _SLI_DATA  ->  Postgres (slos,
#                             sli_measurements; migration 007_slos). The two
#                             dicts, not the tracker, were where every objective
#                             lived — `_get_tracker` builds a fresh SLOTracker
#                             per request — so a restart deleted each customer's
#                             objectives together with the measurement series
#                             the error budget is computed from, and the
#                             burn-rate alerts went quiet at exactly the moment
#                             the deploy that caused the burn happened.
#                             tests/test_slo_store.py covers it.
#   app/status_page/store.py  _components / _incidents / _maintenances /
#                             _subscribers  ->  Postgres (status_components,
#                             status_incidents, status_incident_updates,
#                             status_maintenances, status_subscribers,
#                             status_uptime_records; migration 008_status). The
#                             page a customer's users refresh during an outage
#                             came back reading "All Systems Operational" as
#                             soon as the process holding the incident went
#                             away. Component status is now measured by real
#                             health checks rather than typed in by an admin,
#                             and anything the checks cannot classify is
#                             DEGRADED, never OPERATIONAL.
#                             tests/test_status_page_store.py covers it.
#   app/incidents/store.py    _incidents / _investigations  ->  Postgres
#                             (incidents, incident_investigations; migration
#                             003_incidents). Both reads and writes filter on
#                             `org_uuid()`, so isolation no longer depends on
#                             which in-process instance the caller was handed,
#                             and the three fabricated demo incidents the dict
#                             was seeded with — which every org saw as its own —
#                             are gone rather than relabelled.
#                             tests/test_incidents_store.py covers it.
#
# The list below was rebuilt after the teams and OAuth slices, because both were
# missing from it and the reason was the same each time: the scan that seeded it
# only looked for lower-case module-level names bound to a dict literal. Three
# shapes of the same problem slipped through —
#   * instance attributes on a module-level singleton (teams);
#   * UPPER_CASE module-level dicts (`_SLOS`, `_OAUTH_ACCOUNTS`);
#   * in-memory implementations behind a `get_*_store()` factory (status page);
#   * containers built by a factory call rather than written as a literal
#     (`defaultdict(list)`, `deque(maxlen=...)`) — found last, when `_SLI_DATA`
#     was listed here but silently failed to resolve against the scanner. That
#     is the unsafe direction to be wrong in: an undetected store drops off the
#     surviving list, so the pin looks liftable when it is not. `_is_mutable_
#     container` now matches both spellings, and a test below asserts every
#     declared entry still resolves so the next one cannot fail silently.
# Everything below is now the result of scanning *every* module reachable by
# import from app/api/main.py, in all four shapes, and keeping what a wired
# endpoint actually reads and writes. Caches over a durable layer (session
# objects over the checkpointer), per-pod control machinery (locks, rate-limit
# buckets, circuit breakers, adaptive timeouts) and prefilled constants are
# deliberately absent: those are correct per-pod and moving them would be wrong.
#
# One exclusion worth naming, because the scan will keep finding it:
# `app/audit_legacy.py::_PENDING` is a bounded hand-off queue between the
# synchronous `record()` and the async drain that writes the chain. It is not a
# store -- nothing reads from it, it is drained on a timer and on shutdown, and
# every pod drains its own into the same table, so it does not stop a second
# replica. It is not free of consequence either: events sitting in it when a
# process is killed are lost, which is a real (sub-second) durability gap
# documented in that module. Closing it needs an await at the call site, which
# the five synchronous callers of `record()` block.
#
# NOT yet externalised, and not movable by adding a table: the live-object state
# behind /planning/executions (cancel, pause) and /coordinator/.../approve is
# asyncio tasks and events owned by the process running the DAG. A row cannot be
# cancelled or approved; releasing work on another replica needs a control
# channel. Tracked below so the pin does not lift on the mistaken belief that
# "the coordinator is persisted now".
IN_MEMORY_STORES: list[tuple[str, str]] = [
    ("app/deduplication/api_router.py", "_pipelines"),
    ("app/dependency_graph/api_router.py", "_graphs"),
    # When the graph for an org was last discovered. Splitting it from _graphs
    # means a second pod re-discovers on its own schedule.
    ("app/dependency_graph/api_router.py", "_discovered_at"),
    ("app/cost_optimizer/api_router.py", "_budget_managers"),
    ("app/cost_optimizer/api_router.py", "_roi_trackers"),
    # Same file, same shape, missed when the first two were listed.
    ("app/cost_optimizer/api_router.py", "_token_optimizers"),
    ("app/cost_optimizer/api_router.py", "_gpu_trackers"),
    # app/api/graphql/schema.py's _investigations/_incidents used to be listed
    # here. The module was deleted outright (never mounted, imported strawberry
    # which is not a core dependency, and served entirely fabricated data), so
    # there is no store left to externalise.
    # Webhook delivery de-duplication. Per-pod means a redelivery that lands on
    # another replica is processed a second time, which for an incident webhook
    # is a duplicate investigation rather than a duplicate row.
    ("app/api/main.py", "_SEEN_DELIVERIES"),
]

# Table-shaped state held on the instance of a module-level singleton. Distinct
# from PROCESS_LOCAL_RUNTIME_STATE below: nothing here is a live asyncio object,
# so these move to Postgres exactly the way the module-level dicts did. They are
# listed apart only because finding them needs the instance-attribute scan —
# the blind spot that hid the teams store through six slices.
#
# Empty as of the status-page and incident slices, and kept rather than deleted:
# the category is what tells the next reader that a `get_*_store()` singleton is
# a place to look. The scan that fills it (`_instance_dict_attrs`) is what found
# the teams store, and losing the category loses that.
SINGLETON_HELD_STORES: list[tuple[str, str]] = []

# Process-local state held on *instances* rather than at module scope, as
# (module path, attribute name). Reached through a module-level singleton, so it
# is every bit as per-pod as a module dict — but it holds live asyncio objects,
# and that changes the remedy: an execution in flight is a task you cancel and an
# approval is a future you resolve. Neither can be done by writing a row, so
# these do not move to Postgres the way the stores above did; they need a control
# channel (pub/sub, a work queue) that routes the command to the pod running the
# work. Listed separately so nobody reads "the coordinator is persisted now" as
# permission to lift the pin.
PROCESS_LOCAL_RUNTIME_STATE: list[tuple[str, str]] = [
    # /planning/executions, /executions/{id}/cancel, /executions/{id}/pause
    ("app/planning/execution_engine.py", "_active_executions"),
    # The other half of cancellation, and the same defect: the cancel request
    # marks the id here, but the loop that reads the mark runs on whichever pod
    # owns the execution. Cancel on the wrong replica records the intent and the
    # work carries on. Listed so externalising _active_executions alone does not
    # read as done.
    ("app/planning/execution_engine.py", "_cancelled"),
    # /coordinator/executions/{id}/tasks/{id}/approve
    ("app/coordinator/dag_executor.py", "_pending_approvals"),
]

REVERT_HINT = (
    "See the comment block in deploy/helm/copilot/values.yaml for the full store "
    "list and the revert condition."
)


# Callables that return a fresh mutable container. A store is no less
# process-local for having been built by one of these instead of written as a
# literal, and `_SLI_DATA = defaultdict(list)` is exactly that case: a
# literal-only scan dropped it from the surviving list, which is the unsafe
# direction to be wrong in -- an undetected store makes the pin look liftable.
_CONTAINER_FACTORIES = frozenset(
    {"dict", "list", "set", "defaultdict", "OrderedDict", "deque", "Counter"}
)


def _is_mutable_container(value: ast.expr) -> bool:
    """Whether `value` constructs a fresh mutable container.

    Covers both spellings: the literal (`{}`, `[]`) and the factory call
    (`defaultdict(list)`, `dict()`). A dotted call such as `collections.deque()`
    is matched on its attribute name, since the import style is irrelevant to
    whether the result is per-process state.
    """
    if isinstance(value, (ast.Dict, ast.List, ast.Set)):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name):
            return func.id in _CONTAINER_FACTORIES
        if isinstance(func, ast.Attribute):
            return func.attr in _CONTAINER_FACTORIES
    return False


def _module_level_dict_names(rel_path: str) -> set[str]:
    """Names bound to a mutable container at module scope in `rel_path`.

    Module scope is what matters: a dict built inside a function is per-call and
    harmless, while one at module scope is process-global and is exactly the
    thing that does not survive a second replica.
    """
    path = REPO_ROOT / rel_path
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:  # module scope only, deliberately not ast.walk
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if not _is_mutable_container(node.value):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                found.add(t.id)
    return found


def _instance_dict_attrs(rel_path: str) -> set[str]:
    """Names bound to a mutable container on `self` anywhere in `rel_path`.

    Unlike the module-scope scan above this walks the whole tree, because the
    assignment lives inside `__init__`. What makes it per-pod is not the
    assignment site but the singleton that holds the instance — checked here by
    presence of the attribute, so deleting or renaming it is a deliberate edit.
    """
    path = REPO_ROOT / rel_path
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not _is_mutable_container(value):
            continue
        for t in targets:
            if (
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == "self"
            ):
                found.add(t.attr)
    return found


def surviving_in_memory_stores() -> list[str]:
    """Every tracked store that is still process-local, of either kind."""
    return (
        [
            f"{mod}::{name}"
            for mod, name in IN_MEMORY_STORES
            if name in _module_level_dict_names(mod)
        ]
        + [
            f"{mod}::self.{name}"
            for mod, name in SINGLETON_HELD_STORES
            if name in _instance_dict_attrs(mod)
        ]
        + [
            f"{mod}::self.{name}"
            for mod, name in PROCESS_LOCAL_RUNTIME_STATE
            if name in _instance_dict_attrs(mod)
        ]
    )


def _load(rel_path: str):
    return yaml.safe_load((REPO_ROOT / rel_path).read_text(encoding="utf-8"))


def _load_all(rel_path: str) -> list:
    return [d for d in yaml.safe_load_all((REPO_ROOT / rel_path).read_text(encoding="utf-8")) if d]


def _appset() -> dict:
    """The ApplicationSet document.

    applicationset.yaml is multi-document — it also carries an AppProject — so it
    has to be loaded with safe_load_all and selected by kind.
    """
    docs = _load_all("deploy/argocd/applicationset.yaml")
    return next(d for d in docs if d.get("kind") == "ApplicationSet")


def _appset_env_elements() -> list[dict]:
    """Per-environment generator elements, which carry the replica settings."""
    return [
        el
        for gen in _appset()["spec"]["generators"]
        for inner in gen["matrix"]["generators"]
        if "list" in inner
        for el in inner["list"]["elements"]
    ]


def _kustomize_replace_values(rel_path: str, json_path: str) -> list[int]:
    """Values of every `replace` op targeting `json_path` in a kustomization.

    Overlay patches are the subtle half of this: they are applied on top of the
    base manifests, so an overlay that patches /spec/replicas back up silently
    defeats a pin applied only to hpa.yaml or deployment.yaml.
    """
    doc = _load(rel_path) or {}
    values: list[int] = []
    for patch in doc.get("patches", []):
        body = patch.get("patch")
        if not isinstance(body, str):
            continue
        for op in yaml.safe_load(body) or []:
            if op.get("op") == "replace" and op.get("path") == json_path:
                values.append(op["value"])
    return values


# Every place a replica count can be set, as (label, callable returning counts).
# Each entry answers "how many pods could this file produce?"
REPLICA_SOURCES: list[tuple[str, object]] = [
    ("helm/copilot values.replicaCount", lambda: [_load("deploy/helm/copilot/values.yaml")["replicaCount"]]),
    ("helm/devops-copilot values.replicaCount", lambda: [_load("deploy/helm/devops-copilot/values.yaml")["replicaCount"]]),
    ("kubernetes/deployment.yaml spec.replicas", lambda: [_load("deploy/kubernetes/deployment.yaml")["spec"]["replicas"]]),
    (
        "kubernetes/hpa.yaml min/maxReplicas",
        lambda: [
            d["spec"]["minReplicas"]
            for d in _load_all("deploy/kubernetes/hpa.yaml")
            if d.get("kind") == "HorizontalPodAutoscaler"
        ]
        + [
            d["spec"]["maxReplicas"]
            for d in _load_all("deploy/kubernetes/hpa.yaml")
            if d.get("kind") == "HorizontalPodAutoscaler"
        ],
    ),
    (
        "kubernetes/keda-scaledobject.yaml",
        lambda: [
            _load("deploy/kubernetes/keda-scaledobject.yaml")["spec"]["minReplicaCount"],
            _load("deploy/kubernetes/keda-scaledobject.yaml")["spec"]["maxReplicaCount"],
            _load("deploy/kubernetes/keda-scaledobject.yaml")["spec"]["fallback"]["replicas"],
        ],
    ),
    (
        "argocd/applicationset.yaml env elements",
        lambda: [
            int(el[key])
            for el in _appset_env_elements()
            for key in ("replicaCount", "minReplicas", "maxReplicas")
            if key in el
        ],
    ),
    (
        "overlays/staging deployment+hpa patches",
        lambda: _kustomize_replace_values("deploy/kubernetes/overlays/staging/kustomization.yaml", "/spec/replicas")
        + _kustomize_replace_values("deploy/kubernetes/overlays/staging/kustomization.yaml", "/spec/minReplicas")
        + _kustomize_replace_values("deploy/kubernetes/overlays/staging/kustomization.yaml", "/spec/maxReplicas"),
    ),
    (
        "overlays/production deployment+hpa patches",
        lambda: _kustomize_replace_values("deploy/kubernetes/overlays/production/kustomization.yaml", "/spec/replicas")
        + _kustomize_replace_values("deploy/kubernetes/overlays/production/kustomization.yaml", "/spec/minReplicas")
        + _kustomize_replace_values("deploy/kubernetes/overlays/production/kustomization.yaml", "/spec/maxReplicas"),
    ),
]


@pytest.mark.parametrize("label,getter", REPLICA_SOURCES, ids=[s[0] for s in REPLICA_SOURCES])
def test_replica_source_pinned_to_one(label: str, getter) -> None:
    """No manifest may schedule a second pod while state is process-local."""
    stores = surviving_in_memory_stores()
    if not stores:
        pytest.skip("stores externalised; see test_pin_is_lifted_once_state_is_externalised")

    counts = getter()
    assert counts, f"{label}: found no replica values — did the manifest layout change?"
    offenders = [c for c in counts if c != 1]
    assert not offenders, (
        f"{label} would schedule {offenders} pod(s), but {len(stores)} in-memory "
        f"store(s) are still process-local, including {stores[0]}. Raising this "
        f"silently corrupts billing usage data. {REVERT_HINT}"
    )


def test_autoscaling_disabled_in_helm_values() -> None:
    """Autoscaling is the acute form of the bug: scale-down destroys usage records."""
    if not surviving_in_memory_stores():
        pytest.skip("stores externalised; see test_pin_is_lifted_once_state_is_externalised")
    values = _load("deploy/helm/copilot/values.yaml")
    assert values["autoscaling"]["enabled"] is False, (
        "helm autoscaling is enabled while state is process-local: every scale-up "
        f"serves from empty stores and every scale-down destroys usage records. {REVERT_HINT}"
    )


def test_argocd_autoscaling_disabled_per_environment() -> None:
    """The ArgoCD generator sets autoscaling per environment, bypassing values.yaml."""
    if not surviving_in_memory_stores():
        pytest.skip("stores externalised; see test_pin_is_lifted_once_state_is_externalised")
    enabled = [
        (el.get("env"), el["autoscaling"])
        for el in _appset_env_elements()
        if el.get("autoscaling") not in (None, "false")
    ]
    assert not enabled, f"ArgoCD enables autoscaling for {enabled} while state is process-local. {REVERT_HINT}"


def test_pin_is_lifted_once_state_is_externalised() -> None:
    """The other direction: don't leave the pin in place after the cause is gone.

    Without this, the pin becomes cargo cult — a permanent single-replica ceiling
    nobody remembers the reason for. When the last store moves to Postgres/Redis
    this fails and hands over the exact list of knobs to restore.
    """
    if surviving_in_memory_stores():
        pytest.skip("in-memory stores still present; pin is still justified")
    pytest.fail(
        "All tracked in-memory stores are gone, so the single-replica pin is no "
        "longer needed. Restore the recorded values (helm 2, deployment 3, hpa "
        "3/20, keda 3/50 fallback 5, argocd staging 2 (2-5) / production 3 "
        "(3-20), overlays staging 2 (2-5) / production 3 (3-20)), re-enable "
        "autoscaling and the PodDisruptionBudget, then delete this test module."
    )


def test_every_declared_store_still_resolves() -> None:
    """Each tracked entry must still be found by the scanner that tracks it.

    The skip above is driven by `surviving_in_memory_stores()`, which keeps an
    entry only if the scan finds it in the source. So an entry that stops
    matching — renamed attribute, deleted module, or a shape the scanner does
    not recognise — does not fail; it quietly leaves the list and moves the pin
    one store closer to lifting. That is how `_SLI_DATA` sat here unresolved: it
    is `defaultdict(list)`, and the scan matched only literals.

    Requiring every declared entry to resolve turns all three into loud
    failures. Externalising a store is then a deliberate two-part edit: change
    the code, and delete its line here.
    """
    declared = (
        [(m, n, f"{m}::{n}") for m, n in IN_MEMORY_STORES]
        + [(m, n, f"{m}::self.{n}") for m, n in SINGLETON_HELD_STORES]
        + [(m, n, f"{m}::self.{n}") for m, n in PROCESS_LOCAL_RUNTIME_STATE]
    )
    surviving = set(surviving_in_memory_stores())
    unresolved = [label for _, _, label in declared if label not in surviving]
    assert not unresolved, (
        "Declared stores that the scanner no longer finds: "
        f"{unresolved}. Either the store really was externalised — in which case "
        "delete its entry from the list above — or it changed shape and the scan "
        "silently stopped counting it, which understates what blocks the pin."
    )
