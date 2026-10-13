from types import SimpleNamespace

from app.mcp.servers.kubernetes import server


def _deployment(*, annotations=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            annotations=annotations or {"deployment.kubernetes.io/revision": "3"},
            resource_version="rv-3",
        )
    )


def _replica_set(revision, *, owner="checkout"):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            annotations={"deployment.kubernetes.io/revision": str(revision)},
            owner_references=[SimpleNamespace(kind="Deployment", name=owner)],
        ),
        spec=SimpleNamespace(template={"metadata": {}, "spec": {"containers": []}}),
    )


def test_live_rollback_blocks_gitops_owned_deployment(monkeypatch):
    class Apps:
        def read_namespaced_deployment(self, *_):
            return _deployment(annotations={"argocd.argoproj.io/instance": "checkout"})

    monkeypatch.setattr(server, "OFFLINE", False)
    monkeypatch.setattr(server, "_client", lambda: (None, Apps()))
    result = server.rollback_deployment("checkout", "prod", revision=2)
    assert result["status"] == "unsupported"
    assert result["reason"] == "gitops_controller_owns_workload"


def test_live_rollback_submits_highest_prior_revision(monkeypatch):
    class Apps:
        def __init__(self):
            self.patched = None

        def read_namespaced_deployment(self, *_):
            return _deployment()

        def list_namespaced_replica_set(self, *_):
            return SimpleNamespace(items=[_replica_set(1), _replica_set(2), _replica_set(3)])

        def patch_namespaced_deployment(self, *_args):
            self.patched = _args
            return SimpleNamespace(metadata=SimpleNamespace(resource_version="rv-4"))

    apps = Apps()
    monkeypatch.setattr(server, "OFFLINE", False)
    monkeypatch.setattr(server, "_client", lambda: (None, apps))
    result = server.rollback_deployment("checkout", "prod", revision=2)
    assert result["status"] == "submitted"
    assert result["to_revision"] == 2
    assert result["resource_version"] == "rv-4"
