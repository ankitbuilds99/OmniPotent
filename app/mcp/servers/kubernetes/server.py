"""Kubernetes MCP server — read-only cluster inspection + bounded remediation.

Most production incidents surface first as orchestration symptoms — CrashLoopBackOff,
OOMKilled, a stuck rollout, pods not becoming Ready — and the highest-value
remediation (rollback) lives here too. This server gives the agent that context.

When `KUBE_CONFIG_PATH` is set it talks to the real cluster via the official
`kubernetes` client; otherwise it runs in OFFLINE DEMO mode with fixtures that tie
into the bundled checkout-svc incident (a bad discount deploy → CrashLoopBackOff),
so the whole agent stays runnable with no cluster — the same live/offline pattern
as the datadog/github/pagerduty servers.

Read tools (allow): list_pods, describe_pod, get_events, get_deployment_status,
rollout_history.
Write tools (gated via app/policy.py → human approval): scale_deployment,
rollback_deployment, restart_deployment.

Run standalone for debugging:
    python -m app.mcp.servers.kubernetes.server
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

KUBE_CONFIG_PATH = os.environ.get("KUBE_CONFIG_PATH", "").strip()
KUBE_NAMESPACE = os.environ.get("KUBE_NAMESPACE", "default").strip() or "default"
OFFLINE = not KUBE_CONFIG_PATH

mcp = FastMCP("kubernetes")


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Offline fixtures — multi-scenario support via centralized fixture system
# --------------------------------------------------------------------------- #
def _get_pods() -> list:
    from app.mcp.fixtures import get_fixture
    return get_fixture("kubernetes", "pods") or []


def _get_events() -> list:
    from app.mcp.fixtures import get_fixture
    return get_fixture("kubernetes", "events") or []


def _get_deployment() -> dict:
    from app.mcp.fixtures import get_fixture
    return get_fixture("kubernetes", "deployment") or {}


def _get_rollout_history() -> list:
    from app.mcp.fixtures import get_fixture
    return get_fixture("kubernetes", "rollout_history") or []


def _demo(payload):
    """Label fixture data so the agent can't mistake it for a live cluster read.

    The write tools below already say "(simulated)"; the read tools returned
    fixtures shaped exactly like real API responses, so an investigation built
    on invented pods looked identical to one built on a real cluster.
    """
    from app.mcp.fixtures import mark_demo
    return mark_demo(payload, "kubernetes")


def _gate() -> dict | None:
    """Fail closed when no kubeconfig is set outside demo mode: fixtures are a
    demo feature, not a production fallback (app/config.py's contract)."""
    from app.mcp.fixtures import offline_gate
    return offline_gate("Kubernetes", "KUBE_CONFIG_PATH")


def _client():
    """Build (CoreV1Api, AppsV1Api) from the configured kubeconfig (live mode)."""
    from kubernetes import client, config  # lazy: optional dependency

    config.load_kube_config(config_file=KUBE_CONFIG_PATH)
    return client.CoreV1Api(), client.AppsV1Api()


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_pods(namespace: str = "", status: str | None = None) -> list[dict]:
    """List pods with status, readiness, restart count, age, and node.

    Args:
        namespace: kube namespace (defaults to the configured one).
        status: optionally filter by phase/state (e.g. "CrashLoopBackOff", "Running").
    """
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        return _demo([p for p in _get_pods() if not status or str(p["status"]).lower() == status.lower()])
    core, _ = _client()
    out = []
    for p in core.list_namespaced_pod(ns).items:
        cs = p.status.container_statuses or []
        restarts = sum(c.restart_count for c in cs)
        ready = f"{sum(1 for c in cs if c.ready)}/{len(cs)}" if cs else "0/0"
        phase = p.status.phase or "Unknown"
        out.append({
            "name": p.metadata.name, "namespace": ns, "status": phase,
            "ready": ready, "restarts": restarts, "node": p.spec.node_name or "",
        })
    if status:
        out = [p for p in out if p["status"].lower() == status.lower()]
    return out


@mcp.tool()
def describe_pod(name: str, namespace: str = "") -> dict:
    """Describe a pod: container statuses (CrashLoopBackOff / OOMKilled / exit
    codes), restart count, and the pod's recent events — the fastest path from
    'pod is unhealthy' to *why*."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return err
        pod = next((p for p in _get_pods() if p["name"] == name), None)
        if not pod:
            return {"error": f"pod '{name}' not found (offline demo)",
                    "available": [p["name"] for p in _get_pods()]}
        events = [e for e in _get_events() if name in e["object"]]
        return _demo({**pod, "events": events,
                      "containers": [{"name": "checkout", "state": pod["status"],
                                      "last_exit_code": 1 if pod["restarts"] else 0,
                                      "reason": pod["reason"]}]})
    core, _ = _client()
    p = core.read_namespaced_pod(name, ns)
    containers = [{
        "name": c.name,
        "ready": c.ready,
        "restarts": c.restart_count,
        "state": next(iter((c.state.to_dict() or {}).keys()), "unknown") if c.state else "unknown",
    } for c in (p.status.container_statuses or [])]
    return {"name": name, "namespace": ns, "status": p.status.phase, "containers": containers}


@mcp.tool()
def get_events(namespace: str = "", only_warnings: bool = False) -> list[dict]:
    """Recent cluster events (Normal/Warning) — surfaces BackOff, Unhealthy probes,
    image pull errors, and rollout activity. Set only_warnings to focus on problems."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        return _demo([e for e in _get_events() if not only_warnings or e["type"] == "Warning"])
    core, _ = _client()
    out = []
    for e in core.list_namespaced_event(ns).items:
        if only_warnings and e.type != "Warning":
            continue
        out.append({"type": e.type, "reason": e.reason,
                    "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "message": e.message})
    return out


@mcp.tool()
def get_deployment_status(name: str, namespace: str = "") -> dict:
    """Deployment health: desired/ready/available/unavailable replicas, rollout
    conditions, and image — i.e. is the rollout healthy or stuck?"""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return err
        if name != _get_deployment()["name"]:
            return {"error": f"deployment '{name}' not found (offline demo has '{_get_deployment()['name']}')"}
        return _demo(dict(_get_deployment()))
    _, apps = _client()
    d = apps.read_namespaced_deployment(name, ns)
    s = d.status
    return {
        "name": name, "namespace": ns,
        "desired": d.spec.replicas, "ready": s.ready_replicas or 0,
        "available": s.available_replicas or 0,
        "unavailable": s.unavailable_replicas or 0,
        "conditions": [{"type": c.type, "status": c.status, "reason": c.reason}
                       for c in (s.conditions or [])],
    }


@mcp.tool()
def rollout_history(deployment: str, namespace: str = "") -> list[dict]:
    """Revision history of a deployment with change-causes — the 'what changed'
    needed to correlate a bad rollout with the offending release/commit."""
    if OFFLINE:
        err = _gate()
        if err:
            return [err]
        if deployment != _get_deployment()["name"]:
            return [{"error": f"deployment '{deployment}' not found (offline demo)"}]
        return _demo(list(_get_rollout_history()))
    # Live revision history lives on the deployment's ReplicaSets; summarize them.
    _, apps = _client()
    ns = namespace or KUBE_NAMESPACE
    out = []
    for rs in apps.list_namespaced_replica_set(ns).items:
        ann = rs.metadata.annotations or {}
        rev = ann.get("deployment.kubernetes.io/revision")
        if rev:
            out.append({"revision": _as_int(rev, 0),
                        "change_cause": ann.get("kubernetes.io/change-cause", ""),
                        "image": rs.spec.template.spec.containers[0].image})
    return sorted(out, key=lambda r: r["revision"])


# --------------------------------------------------------------------------- #
# Write tools — gated via app/policy.py (human approval; scale-to-zero = high risk)
# --------------------------------------------------------------------------- #
@mcp.tool()
def scale_deployment(deployment: str, replicas: int | str, namespace: str = "") -> dict:
    """(WRITE) Scale a deployment's replica count. Requires human approval."""
    replicas = _as_int(replicas, -1)
    if replicas < 0:
        return {"error": "replicas must be a non-negative integer"}
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return err
        return _demo({"status": "scaled (simulated — offline demo, no cluster touched)",
                      "deployment": deployment, "namespace": ns, "replicas": replicas})
    _, apps = _client()
    apps.patch_namespaced_deployment_scale(deployment, ns, {"spec": {"replicas": replicas}})
    return {"status": "scaled", "deployment": deployment, "namespace": ns, "replicas": replicas}


@mcp.tool()
def rollback_deployment(deployment: str, namespace: str = "") -> dict:
    """(WRITE) Roll a deployment back to its previous revision — the highest-value
    remediation for a change-induced incident. Requires human approval."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return err
        prev = _get_rollout_history()[-2] if len(_get_rollout_history()) >= 2 else None
        return _demo({"status": "rolled back (simulated — offline demo, no cluster touched)",
                      "deployment": deployment, "namespace": ns,
                      "to_revision": prev["revision"] if prev else None,
                      "to_image": prev["image"] if prev else None})
    # Live rollback = restore the previous ReplicaSet's pod template (kubectl
    # rollout undo semantics). Left to the operator's GitOps in this build.
    return {"status": "not_implemented_live",
            "detail": "Live rollback should go through your GitOps/CD; offline demo simulates it."}


@mcp.tool()
def restart_deployment(deployment: str, namespace: str = "") -> dict:
    """(WRITE) Trigger a rolling restart of a deployment. Requires human approval."""
    ns = namespace or KUBE_NAMESPACE
    if OFFLINE:
        err = _gate()
        if err:
            return err
        return _demo({"status": "restart triggered (simulated — offline demo, no cluster touched)",
                      "deployment": deployment, "namespace": ns})
    import datetime

    _, apps = _client()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    patch = {"spec": {"template": {"metadata": {"annotations":
            {"kubectl.kubernetes.io/restartedAt": now}}}}}
    apps.patch_namespaced_deployment(deployment, ns, patch)
    return {"status": "restart triggered", "deployment": deployment, "namespace": ns}


if __name__ == "__main__":
    mcp.run(transport="stdio")
