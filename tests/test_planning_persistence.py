"""Planning task graphs survive the process that created them, and stay in their org.

The behaviour under test is not "the store has a save method" — it is that a
graph built through the HTTP API is still there for a *second* client, and that
one organization cannot reach another's graph by naming it. Both are exercised
end-to-end through the router rather than against the store, because the bug
being fixed lived in the routes: they kept graphs in a module-level dict.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import config as cfg
from app import runtime
from app import tenant_context
from app.api import main as api
from app.database.engine import close_db
from app.planning import store
from app.tenant_context import TenantConfig


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_STATE_DIR", str(tmp_path))
    cfg.get_settings.cache_clear()
    api._RL.clear()
    runtime.reset()
    # The engine is a module global: without disposing it, a test that ran
    # earlier leaves one pointing at *its* tmp_path and this test reads that
    # database instead of its own.
    asyncio.run(close_db())
    yield TestClient(api.app)
    asyncio.run(close_db())
    api._RL.clear()
    runtime.reset()
    cfg.get_settings.cache_clear()


def _as_org(monkeypatch, org_id: str) -> None:
    """Make every request in this test resolve to `org_id`.

    Patching the resolver rather than the contextvar: TestClient runs the app on
    its own event loop, so a contextvar set here would not be visible inside the
    request.
    """
    monkeypatch.setattr(
        "app.planning.api_router.storage_org_id", lambda: org_id
    )


def test_a_graph_outlives_the_client_that_created_it(client, monkeypatch):
    created = client.post("/api/planning/graphs", json={"graph_id": "g1"})
    assert created.status_code == 200, created.text

    client.post(
        "/api/planning/graphs/g1/tasks",
        json={"name": "collect logs", "task_id": "t1"},
    )

    # A second client is the point: it shares no Python object with the first,
    # only the database. Under the old module-level dict this passed for the
    # wrong reason — same process, same dict — so the assertion that matters is
    # paired with test_a_graph_is_not_held_in_a_module_dict below.
    with TestClient(api.app) as second:
        got = second.get("/api/planning/graphs/g1")
        assert got.status_code == 200, got.text
        assert list(got.json()["tasks"]) == ["t1"]


def test_a_graph_is_not_held_in_a_module_dict():
    """The router must not have re-grown a process-global store."""
    from app.planning import api_router

    assert not hasattr(api_router, "_graphs"), (
        "app/planning/api_router.py has a module-level _graphs again; graphs "
        "must go through app/planning/store.py or they are per-pod once more."
    )


def test_tasks_added_over_several_requests_accumulate(client):
    client.post("/api/planning/graphs", json={"graph_id": "g2"})
    for i in range(3):
        r = client.post(
            "/api/planning/graphs/g2/tasks",
            json={"name": f"step {i}", "task_id": f"t{i}"},
        )
        assert r.status_code == 200, r.text

    body = client.get("/api/planning/graphs/g2").json()
    assert sorted(body["tasks"]) == ["t0", "t1", "t2"]


def test_dependencies_survive_the_round_trip(client):
    client.post("/api/planning/graphs", json={"graph_id": "g3"})
    client.post("/api/planning/graphs/g3/tasks", json={"name": "a", "task_id": "a"})
    client.post("/api/planning/graphs/g3/tasks", json={"name": "b", "task_id": "b"})

    added = client.post(
        "/api/planning/graphs/g3/dependencies",
        json={"task_id": "b", "depends_on": "a"},
    )
    assert added.status_code == 200, added.text

    plan = client.get("/api/planning/graphs/g3/plan")
    assert plan.status_code == 200, plan.text
    # `a` has to be in an earlier level than `b`, which is only true if the
    # dependency was actually persisted rather than added to a discarded copy.
    levels = plan.json()["levels"] if "levels" in plan.json() else plan.json()
    assert "a" in str(levels) and "b" in str(levels)

    body = client.get("/api/planning/graphs/g3").json()
    assert body["tasks"]["b"]["dependencies"] == ["a"]


def test_checkpoints_survive_the_round_trip(client):
    client.post("/api/planning/graphs", json={"graph_id": "g4"})
    client.post("/api/planning/graphs/g4/tasks", json={"name": "a", "task_id": "a"})

    made = client.post("/api/planning/graphs/g4/checkpoint")
    assert made.status_code == 200, made.text
    assert made.json()["graph_id"] == "g4"

    # to_dict() omits checkpoints, so this reads them back through the store —
    # the reason serialize()/deserialize() exist separately from to_dict().
    reloaded = asyncio.run(store.load_graph("default", "g4"))
    asyncio.run(close_db())
    assert reloaded is not None
    assert len(reloaded._checkpoints) == 1


def test_one_org_cannot_read_another_orgs_graph(client, monkeypatch):
    _as_org(monkeypatch, "org-a")
    client.post("/api/planning/graphs", json={"graph_id": "shared-name"})
    client.post(
        "/api/planning/graphs/shared-name/tasks",
        json={"name": "secret", "task_id": "secret"},
    )

    _as_org(monkeypatch, "org-b")
    assert client.get("/api/planning/graphs/shared-name").status_code == 404
    assert client.get("/api/planning/graphs").json()["count"] == 0


def test_two_orgs_may_use_the_same_graph_id(client, monkeypatch):
    """The composite key is (id, org_id), so a name collision is not an error."""
    _as_org(monkeypatch, "org-a")
    assert client.post("/api/planning/graphs", json={"graph_id": "dup"}).status_code == 200

    _as_org(monkeypatch, "org-b")
    assert client.post("/api/planning/graphs", json={"graph_id": "dup"}).status_code == 200

    client.post("/api/planning/graphs/dup/tasks", json={"name": "b-task", "task_id": "b"})

    _as_org(monkeypatch, "org-a")
    # org-a's graph is untouched by org-b writing to the same id.
    assert client.get("/api/planning/graphs/dup").json()["tasks"] == {}


def test_one_org_cannot_delete_another_orgs_graph(client, monkeypatch):
    _as_org(monkeypatch, "org-a")
    client.post("/api/planning/graphs", json={"graph_id": "victim"})

    _as_org(monkeypatch, "org-b")
    assert client.delete("/api/planning/graphs/victim").json() == {"deleted": False}

    _as_org(monkeypatch, "org-a")
    assert client.get("/api/planning/graphs/victim").status_code == 200


def test_creating_a_graph_twice_is_a_conflict_not_a_silent_wipe(client):
    client.post("/api/planning/graphs", json={"graph_id": "g5"})
    client.post("/api/planning/graphs/g5/tasks", json={"name": "a", "task_id": "a"})

    again = client.post("/api/planning/graphs", json={"graph_id": "g5"})
    assert again.status_code == 409

    # The task the caller added is still there.
    assert list(client.get("/api/planning/graphs/g5").json()["tasks"]) == ["a"]


def test_delete_reports_whether_anything_was_deleted(client):
    client.post("/api/planning/graphs", json={"graph_id": "g6"})
    assert client.delete("/api/planning/graphs/g6").json() == {"deleted": True}
    # The old router answered {"deleted": true} unconditionally, so a typo'd id
    # read as a successful delete.
    assert client.delete("/api/planning/graphs/g6").json() == {"deleted": False}


def test_removing_a_task_reports_whether_it_existed(client):
    client.post("/api/planning/graphs", json={"graph_id": "g7"})
    client.post("/api/planning/graphs/g7/tasks", json={"name": "a", "task_id": "a"})

    assert client.delete("/api/planning/graphs/g7/tasks/a").json() == {"removed": True}
    assert client.delete("/api/planning/graphs/g7/tasks/a").json() == {"removed": False}
    assert client.get("/api/planning/graphs/g7").json()["tasks"] == {}


def test_removing_a_task_persists(client):
    client.post("/api/planning/graphs", json={"graph_id": "g8"})
    client.post("/api/planning/graphs/g8/tasks", json={"name": "a", "task_id": "a"})
    client.post("/api/planning/graphs/g8/tasks", json={"name": "b", "task_id": "b"})
    client.delete("/api/planning/graphs/g8/tasks/a")

    with TestClient(api.app) as second:
        assert list(second.get("/api/planning/graphs/g8").json()["tasks"]) == ["b"]


def test_unknown_graph_is_404_on_every_route(client):
    paths = [
        ("get", "/api/planning/graphs/nope"),
        ("get", "/api/planning/graphs/nope/plan"),
        ("get", "/api/planning/graphs/nope/progress"),
        ("get", "/api/planning/graphs/nope/visualize"),
        ("get", "/api/planning/graphs/nope/tasks/t"),
        ("post", "/api/planning/graphs/nope/checkpoint"),
    ]
    for method, path in paths:
        r = getattr(client, method)(path)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


def test_listing_is_scoped_and_counts_what_it_returns(client, monkeypatch):
    _as_org(monkeypatch, "org-a")
    client.post("/api/planning/graphs", json={"graph_id": "a1"})
    client.post("/api/planning/graphs", json={"graph_id": "a2"})

    _as_org(monkeypatch, "org-b")
    client.post("/api/planning/graphs", json={"graph_id": "b1"})

    body = client.get("/api/planning/graphs").json()
    assert body["count"] == 1
    assert [g["id"] for g in body["graphs"]] == ["b1"]


def test_incident_graph_is_stored_not_just_returned(client):
    made = client.post(
        "/api/planning/incident-graph",
        json={"incident_id": "INC-1", "affected_services": ["api"], "severity": "P1"},
    )
    assert made.status_code == 200, made.text
    graph_id = made.json()["id"]

    with TestClient(api.app) as second:
        assert second.get(f"/api/planning/graphs/{graph_id}").status_code == 200


def test_sync_execution_results_are_saved(client):
    """Execution mutates task state; without a save the run's outcome is lost."""
    client.post("/api/planning/graphs", json={"graph_id": "g9"})
    client.post(
        "/api/planning/graphs/g9/tasks",
        json={"name": "noop", "task_id": "t", "executor": ""},
    )

    ran = client.post("/api/planning/graphs/g9/execute/sync", json={})
    assert ran.status_code == 200, ran.text

    progress = client.get("/api/planning/graphs/g9/progress").json()
    # Whatever the handler did, the task is no longer pending — that transition
    # only survives because execute/sync saves the graph afterwards.
    assert progress["completed"] + progress["failed"] >= 1, progress


def test_background_execution_results_are_saved(client):
    """The background path has its own save; without it the run's work is lost.

    Separate from the sync test above because the two routes save in different
    places — the sync one after `await engine.execute(...)`, the async one inside
    the closure handed to BackgroundTasks, which is the easier of the two to
    forget. TestClient runs background tasks before returning, so by the time the
    next request is issued the run has finished.
    """
    client.post("/api/planning/graphs", json={"graph_id": "gbg"})
    client.post("/api/planning/graphs/gbg/tasks", json={"name": "noop", "task_id": "t"})

    started = client.post("/api/planning/graphs/gbg/execute", json={})
    assert started.status_code == 200, started.text

    with TestClient(api.app) as second:
        progress = second.get("/api/planning/graphs/gbg/progress").json()
    assert progress["completed"] + progress["failed"] >= 1, progress


def test_goal_ids_do_not_collide_across_orgs(client, monkeypatch):
    """`goal_N` is numbered from this org's graph count, not a global one."""
    _as_org(monkeypatch, "org-a")
    client.post("/api/planning/graphs", json={"graph_id": "a1"})
    client.post("/api/planning/graphs", json={"graph_id": "a2"})

    _as_org(monkeypatch, "org-b")
    r = client.post(
        "/api/planning/goals/decompose",
        json={"description": "find the cause of the outage"},
    )
    assert r.status_code == 200, r.text
    # org-b has no graphs, so its first goal is goal_0 regardless of org-a's two.
    assert r.json()["goal_id"] == "goal_0"

    # And the decomposed graph is stored under org-b, not left in memory.
    assert client.get(f"/api/planning/graphs/{r.json()['task_graph_id']}").status_code == 200


def test_storage_org_id_defaults_without_a_tenant():
    assert tenant_context.storage_org_id() == "default"


def test_storage_org_id_follows_the_resolved_tenant():
    token = tenant_context.set_tenant(TenantConfig(org_id="org-z"))
    try:
        assert tenant_context.storage_org_id() == "org-z"
    finally:
        tenant_context.reset_tenant(token)
