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
#                             _usage_store  ->  Postgres (crm_customers,
#                             crm_trials, crm_usage_events; migration 002_crm).
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
#
# NOT yet externalised, and not movable by adding a table: the live-object state
# behind /planning/executions (cancel, pause) and /coordinator/.../approve is
# asyncio tasks and events owned by the process running the DAG. A row cannot be
# cancelled or approved; releasing work on another replica needs a control
# channel. Tracked below so the pin does not lift on the mistaken belief that
# "the coordinator is persisted now".
IN_MEMORY_STORES: list[tuple[str, str]] = [
    ("app/rbac/manager.py", "_managers"),
    ("app/waitlist/manager.py", "_waitlist_store"),
    ("app/deduplication/api_router.py", "_pipelines"),
    ("app/dependency_graph/api_router.py", "_graphs"),
    ("app/cost_optimizer/api_router.py", "_budget_managers"),
    ("app/cost_optimizer/api_router.py", "_roi_trackers"),
]

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
    # /coordinator/executions/{id}/tasks/{id}/approve
    ("app/coordinator/dag_executor.py", "_pending_approvals"),
]

REVERT_HINT = (
    "See the comment block in deploy/helm/copilot/values.yaml for the full store "
    "list and the revert condition."
)


def _module_level_dict_names(rel_path: str) -> set[str]:
    """Names bound to a literal dict/list at module scope in `rel_path`.

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
        if not isinstance(node.value, (ast.Dict, ast.List)):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                found.add(t.id)
    return found


def _instance_dict_attrs(rel_path: str) -> set[str]:
    """Names bound to a literal dict/list on `self` anywhere in `rel_path`.

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
        if not isinstance(value, (ast.Dict, ast.List)):
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
    return [
        f"{mod}::{name}"
        for mod, name in IN_MEMORY_STORES
        if name in _module_level_dict_names(mod)
    ] + [
        f"{mod}::self.{name}"
        for mod, name in PROCESS_LOCAL_RUNTIME_STATE
        if name in _instance_dict_attrs(mod)
    ]


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
