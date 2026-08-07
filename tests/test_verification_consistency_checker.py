"""Tests for consistency_checker.py - data consistency and action safety checks."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.verification.consistency_checker import (
    ActionSafetyCheck,
    DataConsistencyChecker,
    ResourceLockManager,
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
        checker = DataConsistencyChecker()
        state = await checker._capture_state("my-deployment")
        assert state == {"replicas": 2, "available_replicas": 2, "updating": False}

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
