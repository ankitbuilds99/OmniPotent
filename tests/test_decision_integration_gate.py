"""The decision safety gate ran on invented inputs (C4).

`_build_task_features` hardcoded a favourable profile for every task — 10
similar incidents at an 85% success rate, P2 severity, 0.7 root-cause
confidence, 5 pieces of evidence — so the success prediction that gated
execution was fabricated. `_tasks_to_options` hardcoded 0.8/0.3 with a "will be
updated" comment that never happened, and `approve_dag` flipped a flag nothing
read while returning True as if it resumed the DAG.

These pin the fixed behaviour: features derive from the incident context that
was actually passed in; anything unavailable stays neutral/conservative and is
flagged; a context with no real signal fails closed to REQUIRE_APPROVAL with
`prediction_available: False`; and approve_dag is honest that nothing resumes.
"""

from __future__ import annotations

import pytest

from app.coordinator.dag_executor import ExecutionState, TaskDAG, TaskNode
from app.coordinator.decision_integration import DecisionAwareDAGExecutor


@pytest.fixture
def executor() -> DecisionAwareDAGExecutor:
    return DecisionAwareDAGExecutor()


@pytest.fixture
def dag() -> TaskDAG:
    d = TaskDAG("dag-test", "Test remediation")
    d.add_task(TaskNode(
        id="restart",
        name="Restart checkout",
        handler="restart_service",
        estimated_duration_seconds=120,
    ))
    d.add_task(TaskNode(
        id="verify",
        name="Verify recovery",
        handler="verify_resolution",
        depends_on=["restart"],
    ))
    return d


RICH_CONTEXT = {
    "incident_id": "INC-1",
    "severity": "P3",
    "duration_minutes": 12,
    "affected_services": ["checkout-svc", "orders-svc"],
    "is_customer_facing": False,
    "is_revenue_impacting": False,
    "root_cause_confidence": 0.9,
    "evidence_collected": [{"supports_hypothesis": True}] * 6,
    "diagnostic_coverage": 0.8,
    "on_call_experience_level": 0.9,
}


class TestFeaturesComeFromTheContext:
    def test_empty_context_gets_conservative_features_not_favourable_ones(
        self, executor, dag
    ):
        features, missing = executor._build_task_features(dag.tasks["restart"], {})

        # The exact values the old code invented must not reappear.
        assert features.similar_incidents_count == 0
        assert features.similar_action_success_rate == 0.5
        assert features.root_cause_confidence == 0.0
        assert features.evidence_count == 0
        assert features.diagnostic_coverage == 0.0
        # Unknown severity reads as high, not the old comfortable P2.
        assert features.incident_severity == "P1"

        # And every substituted feature is flagged, not silent.
        for key in ("severity", "root_cause_confidence", "evidence_collected",
                    "similar_incidents_count", "diagnostic_coverage"):
            assert key in missing

    def test_context_values_are_what_the_predictor_sees(self, executor, dag):
        features, missing = executor._build_task_features(
            dag.tasks["restart"], RICH_CONTEXT
        )

        assert features.incident_severity == "P3"
        assert features.incident_duration_minutes == 12
        assert features.affected_services_count == 2
        assert features.is_customer_facing is False
        assert features.root_cause_confidence == 0.9
        assert features.evidence_count == 6
        assert features.diagnostic_coverage == 0.8
        for key in ("severity", "root_cause_confidence", "evidence_collected"):
            assert key not in missing


class TestGateFailsClosedWithoutRealInputs:
    async def test_empty_context_parks_the_dag_on_approval(self, executor, dag):
        results = await executor.execute_with_decision(dag, incident_context={})

        assert set(results) == {"restart", "verify"}
        for execution in results.values():
            assert execution.state == ExecutionState.WAITING_APPROVAL
            assert execution.prediction_available is False
            assert execution.to_dict()["prediction_available"] is False

    async def test_no_context_at_all_parks_the_dag_too(self, executor, dag):
        results = await executor.execute_with_decision(dag, incident_context=None)

        assert all(
            e.state == ExecutionState.WAITING_APPROVAL for e in results.values()
        )
        assert all(e.prediction_available is False for e in results.values())

    async def test_the_gate_lands_in_pending_approvals(self, executor, dag):
        await executor.execute_with_decision(dag, incident_context={})

        pending = executor.get_pending_approvals()
        assert any(p["dag_id"] == dag.id for p in pending)


class TestOptionsAreNoLongerHardcoded:
    async def test_options_carry_real_predictions_and_a_heuristic_marker(
        self, executor, dag
    ):
        options = await executor._tasks_to_options(dag, incident_context={})

        for option in options:
            # A real prediction id, not the old constants with a promise.
            assert option.metadata["prediction_id"].startswith("pred_")
            assert 0.0 < option.success_probability < 1.0
            assert option.risk_score in (0.1, 0.3, 0.5, 0.7, 0.9)
            # No real context: the ranking is declared heuristic and every
            # option is forced through approval, so an invented number cannot
            # auto-PROCEED.
            assert option.metadata["heuristic_ranking"] is True
            assert option.requires_approval is True

    async def test_with_real_context_the_ranking_is_not_heuristic(
        self, executor, dag
    ):
        options = await executor._tasks_to_options(dag, incident_context=RICH_CONTEXT)

        by_id = {o.option_id: o for o in options}
        assert by_id["restart"].metadata["heuristic_ranking"] is False
        # requires_approval reflects the task definition again.
        assert by_id["restart"].requires_approval is False


class TestApproveDagIsHonest:
    async def test_unknown_dag_is_an_error_not_a_true(self, executor):
        result = await executor.approve_dag("no-such-dag", approved_by="alice")

        assert result["ok"] is False
        assert result["error"] == "unknown_dag"

    async def test_approval_is_recorded_but_does_not_claim_a_resume(
        self, executor, dag
    ):
        await executor.execute_with_decision(dag, incident_context={})

        result = await executor.approve_dag(dag.id, approved_by="alice", reason="ack")

        assert result["ok"] is True
        assert result["resumed"] is False
        assert "re-submit" in result["detail"]
        # The gate is no longer pending.
        assert all(p["dag_id"] != dag.id for p in executor.get_pending_approvals())

    async def test_an_approved_gate_is_honoured_on_resubmission(self, executor, dag):
        await executor.execute_with_decision(dag, incident_context={})
        await executor.approve_dag(dag.id, approved_by="alice", reason="ack")

        results = await executor.execute_with_decision(dag, incident_context={})

        # The DAG is no longer parked wholesale on the missing-context gate.
        # (Individual tasks may still fail — no handlers are registered here —
        # but nothing waits on an approval that was already given.)
        gates = {
            e.decision_gate.name for e in results.values() if e.decision_gate
        }
        assert f"dag_{dag.id}" not in gates
        assert any(
            e.state != ExecutionState.WAITING_APPROVAL for e in results.values()
        )
