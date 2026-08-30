"""The digital twin is a remediation gate, so it has to be able to say no.

`DecisionIntegration.execute_dag` refuses to run a DAG when
`SimulationResult.safe_to_proceed` is False (decision_integration.py), and the
README sells that gate as "the digital-twin simulation verifies the rollback".
It never verified anything: `_simulate_deployment` built its phase list out of
literal `"status": "passed"` entries, so `all_passed` was True by construction,
`_analyze_results` started from a 0.95 prior, and every deployment cleared the
gate -- including for a service that does not exist in the cluster, and on hosts
with no cluster configured at all.

These tests pin the two properties that make the gate worth having: it reads the
real cluster, and when it cannot it fails closed instead of passing.
"""

import pytest

from app.digital_twin import SimulationConfig, create_digital_twin
from app.digital_twin.simulator import DigitalTwinSimulator, SimulationType


class _Obj:
    """Attribute bag standing in for a kubernetes_asyncio model object."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def _deployment(replicas=3, ready=3, available=3, unavailable=0):
    return _Obj(
        spec=_Obj(replicas=replicas),
        status=_Obj(
            replicas=replicas,
            ready_replicas=ready,
            available_replicas=available,
            unavailable_replicas=unavailable,
        ),
        metadata=_Obj(name="api-service", namespace="production"),
    )


#: Distinguishes "caller said nothing" from an explicit `deployment=None`,
#: which means "this Deployment is not in the cluster".
_UNSET = object()


class _FakeK8s:
    """The `kubernetes_asyncio` facade surface the twin actually calls."""

    def __init__(self, deployment=_UNSET, nodes=None, exc=None):
        self._deployment = _deployment() if deployment is _UNSET else deployment
        self._nodes = nodes if nodes is not None else [
            _Obj(status=_Obj(allocatable={"cpu": "8", "memory": "32Gi"}),
                 metadata=_Obj(name="node-1"))
        ]
        self._exc = exc
        self.reads = 0

    async def read_namespaced_deployment(self, name, namespace):
        self.reads += 1
        if self._exc:
            raise self._exc
        if self._deployment is None:
            raise RuntimeError(f"deployments.apps {name!r} not found in {namespace!r}")
        return self._deployment

    async def list_node(self):
        return _Obj(items=self._nodes)


def _config(sim_type=SimulationType.DEPLOYMENT, **params):
    return SimulationConfig(
        simulation_type=sim_type,
        target_service="api-service",
        environment="production",
        clone_environment=False,
        predict_downtime=False,
        parameters=params,
    )


class TestGateFailsClosedWithoutACluster:
    """No cluster means no verification, which must not read as approval."""

    @pytest.mark.asyncio
    async def test_deployment_without_k8s_is_not_safe_to_proceed(self):
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(_config(target_version="v2.0.0", strategy="rolling"))

        assert result.safe_to_proceed is False, (
            "a simulation that never contacted a cluster cleared the remediation gate"
        )

    @pytest.mark.asyncio
    async def test_deployment_without_k8s_is_marked_simulated(self):
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(_config(target_version="v2.0.0"))

        assert result.simulated is True
        assert result.to_dict()["simulated"] is True

    @pytest.mark.asyncio
    async def test_unverified_deployment_does_not_report_passed_phases(self):
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(_config(target_version="v2.0.0"))

        phases = (result.deployment_result or {}).get("phases", [])
        assert not any(p.get("status") == "passed" for p in phases), (
            "phases reported as passed by a run that checked nothing"
        )

    @pytest.mark.asyncio
    async def test_unverified_deployment_requires_approval(self):
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(_config(target_version="v2.0.0"))

        assert result.requires_approval is True


class TestGateReadsTheRealCluster:
    """With a cluster, the verdict has to come from what the cluster says."""

    @pytest.mark.asyncio
    async def test_healthy_deployment_is_verified_against_the_cluster(self):
        k8s = _FakeK8s(deployment=_deployment(replicas=4, ready=4, available=4))
        twin = DigitalTwinSimulator(k8s_client=k8s)

        result = await twin.simulate(_config(target_version="v2.0.0", strategy="rolling"))

        assert k8s.reads > 0, "the twin never read the deployment it was asked about"
        assert result.simulated is False
        assert result.deployment_result["success"] is True

    @pytest.mark.asyncio
    async def test_replica_counts_come_from_the_cluster_not_a_literal(self):
        k8s = _FakeK8s(deployment=_deployment(replicas=7, ready=7, available=7))
        twin = DigitalTwinSimulator(k8s_client=k8s)

        result = await twin.simulate(_config(target_version="v2.0.0", strategy="rolling"))

        rollout = next(
            p for p in result.deployment_result["phases"] if p["name"] == "rolling_update"
        )
        assert rollout["replicas_total"] == 7, (
            "replica count was the hardcoded 3 rather than the cluster's 7"
        )

    @pytest.mark.asyncio
    async def test_missing_deployment_fails_the_gate(self):
        twin = DigitalTwinSimulator(k8s_client=_FakeK8s(deployment=None))

        result = await twin.simulate(_config(target_version="v2.0.0"))

        assert result.deployment_result["success"] is False
        assert result.safe_to_proceed is False

    @pytest.mark.asyncio
    async def test_degraded_deployment_fails_preflight(self):
        k8s = _FakeK8s(
            deployment=_deployment(replicas=5, ready=2, available=2, unavailable=3)
        )
        twin = DigitalTwinSimulator(k8s_client=k8s)

        result = await twin.simulate(_config(target_version="v2.0.0"))

        preflight = next(
            p for p in result.deployment_result["phases"]
            if p["name"] == "pre_deployment_checks"
        )
        assert preflight["status"] == "failed"
        assert result.safe_to_proceed is False

    @pytest.mark.asyncio
    async def test_cluster_error_is_not_reported_as_a_pass(self):
        twin = DigitalTwinSimulator(k8s_client=_FakeK8s(exc=RuntimeError("api down")))

        result = await twin.simulate(_config(target_version="v2.0.0"))

        assert result.deployment_result["success"] is False
        assert result.safe_to_proceed is False


class TestScalingReadsRealReplicas:
    """`current_replicas` defaulted to 3 whatever the service actually ran."""

    @pytest.mark.asyncio
    async def test_current_replicas_read_from_cluster(self):
        k8s = _FakeK8s(deployment=_deployment(replicas=9, ready=9, available=9))
        twin = DigitalTwinSimulator(k8s_client=k8s)

        result = await twin.simulate(_config(SimulationType.SCALING, target_replicas=12))

        assert result.deployment_result["current_replicas"] == 9

    @pytest.mark.asyncio
    async def test_scaling_without_cluster_does_not_invent_a_cost(self):
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(
            _config(SimulationType.SCALING, current_replicas=3, target_replicas=6)
        )

        assert result.deployment_result.get("estimated_cost_change_hourly") is None, (
            "a dollar figure was invented from a $0.05/replica literal"
        )


class TestFactoryResolvesAClient:
    """`create_digital_twin()` hardcoded k8s_client=None, so nothing could pass one."""

    def test_factory_resolves_client_from_settings(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(
            "app.digital_twin.simulator.build_k8s_client", lambda: sentinel
        )

        twin = create_digital_twin()

        assert twin.k8s is sentinel

    def test_explicit_client_still_wins(self):
        sentinel = object()
        assert create_digital_twin(k8s_client=sentinel).k8s is sentinel


class TestCloneCostIsLabelled:
    """The clone cost cap runs on placeholder rates, which must not read as prices."""

    def test_default_rates_are_declared_an_estimate(self):
        from app.digital_twin import InfrastructureCloner

        assert InfrastructureCloner().cost_basis == "default_estimate"

    def test_operator_rates_are_declared_as_such(self):
        from app.digital_twin import InfrastructureCloner
        from app.digital_twin.infrastructure_clone import ResourceType

        cloner = InfrastructureCloner(cost_rates={ResourceType.DATABASE: 1.25})

        assert cloner.cost_basis == "operator_configured"
        assert cloner._cost_rates[ResourceType.DATABASE] == 1.25

    def test_unpriced_resource_counts_against_the_cap(self):
        """A 0.01 default meant an unpriced resource was effectively free."""
        from app.digital_twin import InfrastructureCloner
        from app.digital_twin.infrastructure_clone import CloneConfig

        cloner = InfrastructureCloner()
        cost = cloner._estimate_cost([{"type": "something-unknown"}], CloneConfig())

        assert cost == InfrastructureCloner._UNPRICED_RATE
        assert cost > 0.01


class _FakeK8sConnector:
    """Stands in for app.connectors.kubernetes.KubernetesConnector, which
    RollbackSimulator._discover_state_changes constructs directly."""

    _result: "object | None" = None

    def __init__(self, *args, **kwargs):
        pass

    async def get_deployment(self, name, namespace="default"):
        if isinstance(type(self)._result, Exception):
            raise type(self)._result
        return type(self)._result

    async def close(self):
        pass


def _patch_connector(monkeypatch, result):
    import app.connectors.kubernetes as k8s_mod

    _FakeK8sConnector._result = result
    monkeypatch.setattr(k8s_mod, "KubernetesConnector", _FakeK8sConnector)


class TestRollbackFeedsTheSameGate:
    """A rollback whose state was never read must not clear the gate.

    `_discover_state_changes` used to invent a deployment StateChange
    (`app:{version}` images, reversible=True) when the cluster read failed, and
    always appended a fabricated `configmap/{id}-config` change; the resulting
    RollbackResult carried `can_proceed=True`, and `_analyze_results` derived
    `simulated` only from deployment_result — None for rollback runs — so the
    fabricated rollback came out `simulated=False, safe_to_proceed=True`.
    """

    @pytest.mark.asyncio
    async def test_unreadable_deployment_is_unverified_and_cannot_proceed(self, monkeypatch):
        from app.connectors.base import QueryResult
        from app.digital_twin.rollback_simulator import RollbackSimulator

        _patch_connector(
            monkeypatch, QueryResult(success=False, data=None, error="no cluster")
        )
        sim = RollbackSimulator()
        scenario = await sim.create_scenario(
            deployment_id="deploy-1", service="api-service",
            from_version="v2.0.0", to_version="v1.9.0",
        )

        assert scenario.verified is False
        assert scenario.state_changes == [], (
            "state changes were invented for a deployment that could not be read"
        )

        result = await sim.simulate(scenario.id)
        assert result.verified is False
        assert result.can_proceed is False
        assert result.requires_approval is True
        assert result.to_dict()["verified"] is False

    @pytest.mark.asyncio
    async def test_readable_deployment_is_verified_with_real_state(self, monkeypatch):
        from app.connectors.base import QueryResult
        from app.digital_twin.rollback_simulator import RollbackSimulator

        _patch_connector(
            monkeypatch,
            QueryResult(success=True, data={"image": "registry/api-service:v2.0.0"}),
        )
        sim = RollbackSimulator()
        scenario = await sim.create_scenario(
            deployment_id="api-service", service="api-service",
            from_version="v2.0.0", to_version="v1.9.0",
        )

        assert scenario.verified is True
        images = [c.before_state.get("image") for c in scenario.state_changes]
        assert "registry/api-service:v2.0.0" in images, (
            "the before-state image should be the cluster's, not app:{version}"
        )
        assert not any(
            c.resource_type == "configmap" for c in scenario.state_changes
        ), "a configmap change was fabricated without reading any configmap"

        result = await sim.simulate(scenario.id)
        assert result.verified is True
        assert result.can_proceed is True

    @pytest.mark.asyncio
    async def test_caller_supplied_state_cannot_clear_the_gate(self):
        from app.digital_twin.rollback_simulator import RollbackSimulator

        sim = RollbackSimulator()
        scenario = await sim.create_scenario(
            deployment_id="deploy-1", service="api-service",
            from_version="v2.0.0", to_version="v1.9.0",
            state_changes=[{
                "resource_type": "deployment",
                "resource_id": "deployment/deploy-1",
                "change_type": "update",
                "reversible": True,
                "complexity": 2,
            }],
        )

        result = await sim.simulate(scenario.id)
        assert result.verified is False, (
            "caller-asserted state is planning input, not cluster evidence"
        )
        assert result.can_proceed is False

    @pytest.mark.asyncio
    async def test_unverified_rollback_marks_the_simulation_simulated(self, monkeypatch):
        from app.connectors.base import QueryResult

        _patch_connector(
            monkeypatch, QueryResult(success=False, data=None, error="no cluster")
        )
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(_config(
            SimulationType.ROLLBACK,
            deployment_id="deploy-1", from_version="v2.0.0", to_version="v1.9.0",
        ))

        assert result.simulated is True, (
            "a fabricated rollback used to come out simulated=False"
        )
        assert result.safe_to_proceed is False

    @pytest.mark.asyncio
    async def test_rollback_without_parameters_fails_instead_of_inventing_them(self):
        """deployment_id defaulted to "deploy-123" and versions to v2.0.0/v1.9.0."""
        twin = DigitalTwinSimulator(k8s_client=None)

        result = await twin.simulate(_config(SimulationType.ROLLBACK))

        assert result.status == "failed"
        assert result.safe_to_proceed is False
        assert result.rollback_result is None


class TestWhatIfCannotClearTheGate:
    """What-if scenarios project from request parameters and read no cluster."""

    @pytest.mark.asyncio
    async def test_traffic_spike_projection_is_marked_simulated(self):
        twin = DigitalTwinSimulator(k8s_client=_FakeK8s())

        result = await twin.simulate(
            _config(SimulationType.WHAT_IF, scenario="traffic_spike", multiplier=3)
        )

        assert result.simulated is True
        assert result.safe_to_proceed is False
