"""Tests for consistency_checker.py - data consistency and action safety checks."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.verification.consistency_checker import (
    ActionSafetyCheck,
    DataConsistencyChecker,
    ResourceLockManager,
    normalize_action_type,
)


class TestActionSafetyCheck:
    def test_defaults(self):
        check = ActionSafetyCheck(action_id="test-123", action_type="scale", target="my-deployment")
        assert check.is_safe is False
        assert check.is_reversible is True

    def test_to_dict(self):
        check = ActionSafetyCheck(action_id="test-123", action_type="scale", target="my-deployment", is_safe=True)
        d = check.to_dict()
        assert d["action_id"] == "test-123"
        assert "checked_at" in d


class TestDataConsistencyChecker:
    def test_init_no_dependencies(self):
        checker = DataConsistencyChecker()
        assert checker.k8s is None

    @pytest.mark.asyncio
    async def test_pre_action_check_scale(self):
        checker = DataConsistencyChecker()
        check = await checker.pre_action_check(action_type="scale", target="my-deployment", params={"replicas": 3})
        assert check.action_type == "scale"

    @pytest.mark.asyncio
    async def test_pre_action_check_scale_to_zero_blocked(self):
        checker = DataConsistencyChecker()
        check = await checker.pre_action_check(action_type="scale", target="my-deployment", params={"replicas": 0})
        assert any("zero" in p.lower() for p in check.failed_preconditions)

    @pytest.mark.asyncio
    async def test_pre_action_check_restart(self):
        checker = DataConsistencyChecker()
        check = await checker.pre_action_check(action_type="restart", target="my-deployment", params={})
        assert check.action_type == "restart"

    @pytest.mark.asyncio
    async def test_pre_action_check_restart_single_replica(self):
        mock_k8s = AsyncMock()
        mock_deployment = MagicMock()
        mock_deployment.spec.replicas = 1
        mock_deployment.status.ready_replicas = 1
        mock_deployment.status.available_replicas = 1
        mock_deployment.status.updated_replicas = 1
        mock_deployment.spec.template.spec.containers = [MagicMock(image="app:v1")]
        mock_k8s.read_namespaced_deployment.return_value = mock_deployment

        checker = DataConsistencyChecker(k8s_client=mock_k8s)
        check = await checker.pre_action_check(action_type="restart", target="my-deployment", params={})
        assert any("replica" in r.lower() or "downtime" in r.lower() for r in check.identified_risks)

    @pytest.mark.asyncio
    async def test_pre_action_check_delete(self):
        checker = DataConsistencyChecker()
        check = await checker.pre_action_check(action_type="delete", target="my-deployment", params={})
        assert check.is_safe is False
        assert check.is_reversible is False

    @pytest.mark.asyncio
    async def test_post_action_validate_no_expected_state(self):
        checker = DataConsistencyChecker()
        check = ActionSafetyCheck(action_id="test", action_type="restart", target="my-deployment")
        result = await checker.post_action_validate(check, timeout_seconds=1)
        assert result is True

    @pytest.mark.asyncio
    async def test_capture_state_no_k8s(self):
        """Without a client there is no state to report, so None — not a guess.

        This used to return a fabricated {"replicas": 2, ...}. That value is
        compared against expected post-action state and used to decide whether
        preconditions were met, so inventing it made every caller believe it had
        read the cluster.
        """
        checker = DataConsistencyChecker()
        state = await checker._capture_state("my-deployment")
        assert state is None

    @pytest.mark.asyncio
    async def test_capture_state_k8s_error(self):
        mock_k8s = AsyncMock()
        mock_k8s.read_namespaced_deployment.side_effect = Exception("Not found")
        checker = DataConsistencyChecker(k8s_client=mock_k8s)
        state = await checker._capture_state("missing-deployment")
        assert state is None

    def test_states_match_exact(self):
        checker = DataConsistencyChecker()
        assert checker._states_match({"replicas": 5}, {"replicas": 5}) is True

    def test_states_match_different(self):
        checker = DataConsistencyChecker()
        assert checker._states_match({"replicas": 3}, {"replicas": 5}) is False

    def test_calculate_safety_score(self):
        checker = DataConsistencyChecker()
        check = ActionSafetyCheck(action_id="test", action_type="scale", target="deploy", is_reversible=True)
        score = checker._calculate_safety_score(check)
        assert score > 0.5


def _k8s_returning(
    replicas: int,
    image: str = "app:v1",
    updated: int | None = None,
    node_cpu: str = "16",
):
    """An async k8s client whose deployment read reports `replicas`.

    `node_cpu` is the allocatable CPU of the single node it reports, so the
    capacity check has real numbers to work with instead of an empty node list
    (which legitimately reads as "no capacity").
    """
    client = AsyncMock()
    deployment = MagicMock()
    deployment.spec.replicas = replicas
    deployment.status.ready_replicas = replicas
    deployment.status.available_replicas = replicas
    deployment.status.updated_replicas = replicas if updated is None else updated
    deployment.spec.template.spec.containers = [MagicMock(image=image)]
    client.read_namespaced_deployment.return_value = deployment

    node = MagicMock()
    node.status.allocatable = {"cpu": node_cpu, "memory": "32Gi"}
    node_list = MagicMock()
    node_list.items = [node]
    client.list_node.return_value = node_list

    empty = MagicMock()
    empty.items = []
    client.list_namespaced_horizontal_pod_autoscaler.return_value = empty
    client.list_namespaced_pod_disruption_budget.return_value = empty
    return client


class TestActionTypeNormalization:
    """An action name nobody wrote a rule for must not be waved through.

    Dispatch was an exact-string lookup with a permissive fallback, so
    `drop_database`, `terraform_destroy` and `delete_deployment` all missed the
    delete checker and came back is_safe=True with an empty risk list.
    """

    @pytest.mark.parametrize(
        "action_type",
        [
            "delete", "delete_deployment", "terraform_destroy", "drop_database",
            "remove-namespace", "PURGE", "truncate_table", " wipe_disk ",
            "evict_pods", "uninstall_release",
        ],
    )
    def test_destructive_verbs_reach_the_delete_checker(self, action_type):
        assert normalize_action_type(action_type) == "delete"

    @pytest.mark.parametrize(
        ("action_type", "expected"),
        [
            ("scale", "scale"),
            ("scale_up", "scale"),
            ("Restart", "restart"),
            ("rollback_deployment", "rollback"),
            ("config_update", "config_update"),
            ("update_config", "config_update"),
        ],
    )
    def test_known_actions_normalize(self, action_type, expected):
        assert normalize_action_type(action_type) == expected

    @pytest.mark.parametrize("action_type", ["launch_missiles", "frobnicate", "", "   "])
    def test_unknown_actions_fail_closed(self, action_type):
        assert normalize_action_type(action_type) == ""

    @pytest.mark.asyncio
    async def test_unrecognized_action_is_refused_not_generically_approved(self):
        checker = DataConsistencyChecker(k8s_client=_k8s_returning(2))

        check = await checker.pre_action_check(
            action_type="launch_missiles", target="silo-1", params={}
        )

        assert check.is_safe is False
        assert check.preconditions_met is False
        assert check.is_reversible is False
        assert any("nrecognized" in p for p in check.failed_preconditions)
        assert check.identified_risks

    @pytest.mark.asyncio
    async def test_destructive_action_is_refused_even_with_a_readable_cluster(self):
        checker = DataConsistencyChecker(k8s_client=_k8s_returning(2))

        check = await checker.pre_action_check(
            action_type="drop_database", target="prod-db", params={}
        )

        assert check.is_safe is False
        assert check.is_reversible is False
        assert any("confirmation" in p.lower() for p in check.failed_preconditions)
        assert any("Data loss" in r for r in check.identified_risks)


class TestSafetyScoring:
    @pytest.mark.asyncio
    async def test_an_identified_risk_lowers_the_score(self):
        """A flagged risk has to cost something.

        With the old weights a risk subtracted 0.1 and `is_reversible` added it
        straight back, so a scale-up flagged "may cause resource issues" still
        scored a clean 1.0 and was reported is_safe=True.
        """
        checker = DataConsistencyChecker(k8s_client=_k8s_returning(2))

        check = await checker.pre_action_check(
            action_type="scale", target="checkout", params={"replicas": 50}
        )

        assert check.identified_risks, check
        assert check.safety_score < 1.0, check.safety_score

    @pytest.mark.asyncio
    async def test_a_clean_action_still_scores_well(self):
        """The stricter weights must not make every action look dangerous."""
        checker = DataConsistencyChecker(k8s_client=_k8s_returning(3))

        check = await checker.pre_action_check(
            action_type="scale", target="checkout", params={"replicas": 4}
        )

        assert check.identified_risks == []
        assert check.failed_preconditions == []
        assert check.safety_score >= 0.7
        assert check.is_safe is True

    @pytest.mark.asyncio
    async def test_preconditions_are_not_claimed_when_state_cannot_be_read(self):
        checker = DataConsistencyChecker()

        check = await checker.pre_action_check(
            action_type="scale", target="checkout", params={"replicas": 3}
        )

        assert check.preconditions_met is False
        assert check.is_safe is False
        assert any("Kubernetes" in p for p in check.failed_preconditions)


class TestPostActionValidation:
    @pytest.mark.asyncio
    async def test_a_real_transition_is_confirmed_against_the_cluster(self):
        """The validator has to be able to say yes when the cluster agrees."""
        checker = DataConsistencyChecker(k8s_client=_k8s_returning(3))
        check = await checker.pre_action_check(
            action_type="scale", target="checkout", params={"replicas": 3}
        )

        result = await checker.post_action_validate_detailed(check, timeout_seconds=1)

        assert result.validated is True
        assert result.observed_state is not None
        assert result.observed_state["replicas"] == 3
        assert result.expected_post_state == {"replicas": 3, "available_replicas": 3}
        assert await checker.post_action_validate(check, timeout_seconds=1) is True

    @pytest.mark.asyncio
    async def test_a_transition_that_never_happens_is_reported_as_failed(self):
        checker = DataConsistencyChecker(k8s_client=_k8s_returning(2))
        check = await checker.pre_action_check(
            action_type="scale", target="checkout", params={"replicas": 9}
        )

        result = await checker.post_action_validate_detailed(check, timeout_seconds=0)

        assert result.validated is False
        assert result.observed_state is not None
        assert result.observed_state["replicas"] == 2
        assert "did not reach" in result.reason
        assert await checker.post_action_validate(check, timeout_seconds=0) is False

    @pytest.mark.asyncio
    async def test_an_unobservable_cluster_is_unknown_not_success(self):
        """"Could not look" must never be reported as "the action worked"."""
        checker = DataConsistencyChecker()
        check = ActionSafetyCheck(
            action_id="test",
            action_type="scale",
            target="checkout",
            expected_post_state={"replicas": 3},
        )

        result = await checker.post_action_validate_detailed(check, timeout_seconds=30)

        assert result.validated is None
        assert result.observed_state is None
        assert "Kubernetes" in result.reason
        # The boolean wrapper answers the only safe way.
        assert await checker.post_action_validate(check, timeout_seconds=30) is False

    @pytest.mark.asyncio
    async def test_validation_does_not_poll_when_it_cannot_observe(self):
        """The no-client branch used to sleep in 5s steps until the deadline."""
        checker = DataConsistencyChecker()
        check = ActionSafetyCheck(
            action_id="test",
            action_type="scale",
            target="checkout",
            expected_post_state={"replicas": 3},
        )

        result = await checker.post_action_validate_detailed(check, timeout_seconds=600)

        assert result.waited_seconds < 1.0

    @pytest.mark.asyncio
    async def test_action_ids_do_not_collide_within_one_second(self):
        checker = DataConsistencyChecker()

        ids = {
            (await checker.pre_action_check("scale", f"svc-{i}", {"replicas": 2})).action_id
            for i in range(10)
        }

        assert len(ids) == 10


class TestResourceLockManager:
    def test_init_no_redis(self):
        mgr = ResourceLockManager()
        assert mgr.redis is None

    @pytest.mark.asyncio
    async def test_acquire_lock_local(self):
        mgr = ResourceLockManager()
        acquired = await mgr.acquire_lock("resource-1", "owner-1")
        assert acquired is True

    @pytest.mark.asyncio
    async def test_acquire_lock_local_already_held(self):
        mgr = ResourceLockManager()
        await mgr.acquire_lock("resource-1", "owner-1")
        acquired = await mgr.acquire_lock("resource-1", "owner-2")
        assert acquired is False

    @pytest.mark.asyncio
    async def test_acquire_lock_redis(self):
        mock_redis = AsyncMock()
        mock_redis.set.return_value = True
        mgr = ResourceLockManager(redis_client=mock_redis)
        acquired = await mgr.acquire_lock("resource-1", "owner-1")
        assert acquired is True

    @pytest.mark.asyncio
    async def test_release_lock_local(self):
        mgr = ResourceLockManager()
        await mgr.acquire_lock("resource-1", "owner-1")
        released = await mgr.release_lock("resource-1", "owner-1")
        assert released is True

    @pytest.mark.asyncio
    async def test_check_lock_no_lock(self):
        mgr = ResourceLockManager()
        owner = await mgr.check_lock("resource-1")
        assert owner is None

    @pytest.mark.asyncio
    async def test_check_lock_local(self):
        mgr = ResourceLockManager()
        await mgr.acquire_lock("resource-1", "owner-1")
        owner = await mgr.check_lock("resource-1")
        assert owner == "local"
