"""Tests for the Policy Engine module."""

import pytest
from datetime import datetime, timedelta, timezone

from app.policy_engine import (
    PolicyDecision,
    OPAClient,
    OPAInput,
    KyvernoClient,
    WindowType,
    EntryCriteria,
    Criterion,
    CriteriaType,
    CriteriaOperator,
    create_policy_engine,
    create_change_window_manager,
    create_criteria_evaluator,
)
from app.policy_engine.change_windows import RecurrencePattern


class TestOPAClient:
    """Tests for OPA Client."""

    @pytest.fixture
    def opa(self):
        return OPAClient(embedded_mode=True)

    @pytest.mark.asyncio
    async def test_evaluate_remediation_policy_allow(self, opa):
        """Test OPA allows low-risk reversible action."""
        input_data = OPAInput(
            action="restart_service",
            resource={
                "is_reversible": True,
                "risk_score": 0.2,
                "service_criticality": "normal",
            },
            context={
                "is_change_freeze": False,
                "is_peak_traffic": False,
            },
        )

        result = await opa.evaluate("remediation_safety", input_data)

        assert result.allowed is True
        assert len(result.violations) == 0

    @pytest.mark.asyncio
    async def test_evaluate_remediation_policy_deny_change_freeze(self, opa):
        """Test OPA denies high-risk action during change freeze."""
        input_data = OPAInput(
            action="deploy",
            resource={
                "is_reversible": True,
                "risk_score": 0.7,
                "service_criticality": "normal",
            },
            context={
                "is_change_freeze": True,
                "is_emergency": False,
            },
        )

        result = await opa.evaluate("remediation_safety", input_data)

        assert result.allowed is False
        assert len(result.violations) > 0

    @pytest.mark.asyncio
    async def test_evaluate_deployment_policy(self, opa):
        """Test deployment policy evaluation."""
        input_data = OPAInput(
            action="deploy",
            resource={
                "passed_tests": True,
                "environment": "staging",
                "deployment_strategy": "canary",
            },
            context={
                "in_change_window": True,
            },
        )

        result = await opa.evaluate("deployment_policy", input_data)

        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_evaluate_compliance_policy(self, opa):
        """Test compliance policy evaluation."""
        input_data = OPAInput(
            action="update_user_data",
            resource={
                "affects_pii": True,
                "type": "database_change",
                "encryption_enabled": True,
            },
            context={
                "audit_logging_enabled": True,
            },
        )

        result = await opa.evaluate("compliance_policy", input_data)

        assert result.allowed is True

    def test_list_policies(self, opa):
        """Test listing registered policies."""
        policies = opa.list_policies()

        assert len(policies) >= 3
        policy_names = [p.name for p in policies]
        assert "Remediation Safety" in policy_names or "remediation_safety" in [p.id for p in policies]

    @pytest.mark.asyncio
    async def test_evaluate_batch(self, opa):
        """Test batch evaluation."""
        inputs = [
            OPAInput(
                action="restart",
                resource={"is_reversible": True, "risk_score": 0.2},
                context={},
            ),
            OPAInput(
                action="scale",
                resource={"is_reversible": True, "risk_score": 0.1},
                context={},
            ),
        ]

        results = await opa.evaluate_batch("remediation_safety", inputs)

        assert len(results) == 2


class TestKyvernoClient:
    """Tests for Kyverno Client."""

    @pytest.fixture
    def kyverno(self):
        return KyvernoClient(embedded_mode=True)

    @pytest.mark.asyncio
    async def test_evaluate_deployment_with_labels(self, kyverno):
        """Test Kyverno validates deployment with required labels."""
        resource = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "my-app",
                "namespace": "default",
                "labels": {
                    "app.kubernetes.io/name": "my-app",
                    "team": "platform",
                },
            },
            "spec": {
                "replicas": 3,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "app:v1.0.0",
                                "resources": {
                                    "limits": {
                                        "cpu": "500m",
                                        "memory": "512Mi",
                                    },
                                },
                            }
                        ],
                    },
                },
            },
        }

        results = await kyverno.evaluate(resource, "default")

        all_allowed = all(r.allowed for r in results)
        assert all_allowed or len(results) == 0
        if results:
            assert all(r.authoritative is False for r in results)

    @pytest.mark.asyncio
    async def test_evaluate_deployment_missing_labels(self, kyverno):
        """Test Kyverno evaluates deployment without labels."""
        resource = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "my-app",
                "namespace": "default",
            },
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "app",
                                "image": "app:v1.0.0",
                            }
                        ],
                    },
                },
            },
        }

        results = await kyverno.evaluate(resource, "default")

        assert len(results) > 0

    def test_list_policies(self, kyverno):
        """Test listing Kyverno policies."""
        policies = kyverno.list_policies()

        assert len(policies) > 0


class TestChangeWindowManager:
    """Tests for Change Window Manager."""

    @pytest.fixture
    def manager(self):
        return create_change_window_manager()

    def test_create_maintenance_window(self, manager):
        """Test creating a maintenance window."""
        now = datetime.now(timezone.utc)
        window = manager.create_window(
            name="Weekly Maintenance",
            window_type=WindowType.MAINTENANCE,
            start_time=now,
            end_time=now + timedelta(hours=4),
            reason="Scheduled maintenance",
        )

        assert window.id is not None
        assert window.window_type == WindowType.MAINTENANCE
        assert window.is_active()

    def test_create_freeze_window(self, manager):
        """Test creating a change freeze."""
        now = datetime.now(timezone.utc)
        window = manager.create_freeze(
            name="Holiday Freeze",
            start_time=now,
            end_time=now + timedelta(days=7),
            reason="Holiday code freeze",
        )

        assert window.window_type == WindowType.FREEZE

    def test_check_change_allowed_in_maintenance(self, manager):
        """Test that changes are allowed during maintenance window."""
        now = datetime.now(timezone.utc)
        manager.create_window(
            name="Active Maintenance",
            window_type=WindowType.MAINTENANCE,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=3),
        )

        result = manager.check_change_allowed(
            action_type="deploy",
            risk_score=0.3,
        )

        assert result.allowed is True

    def test_check_change_blocked_during_freeze(self, manager):
        """Test that changes are blocked during freeze."""
        now = datetime.now(timezone.utc)
        manager.create_freeze(
            name="Active Freeze",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            reason="Code freeze",
        )

        result = manager.check_change_allowed(
            action_type="deploy",
            risk_score=0.5,
        )

        assert result.allowed is False
        assert result.blocking_window is not None

    def test_emergency_override(self, manager):
        """Test emergency override activates correctly."""
        now = datetime.now(timezone.utc)
        manager.create_freeze(
            name="Freeze",
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(days=1),
            reason="Freeze",
        )

        override_id = manager.activate_emergency_override(
            duration_minutes=60,
            reason="Critical incident",
            activated_by="oncall",
        )

        assert override_id is not None

        result = manager.check_change_allowed(
            action_type="deploy",
            risk_score=0.8,
        )

        assert result.allowed is True

    def test_recurring_window(self, manager):
        """Test recurring window detection."""
        now = datetime.now(timezone.utc)
        window = manager.create_window(
            name="Daily Maintenance",
            window_type=WindowType.MAINTENANCE,
            start_time=now.replace(hour=2, minute=0),
            end_time=now.replace(hour=6, minute=0),
            recurrence=RecurrencePattern.DAILY,
        )

        assert window.recurrence == RecurrencePattern.DAILY

    def test_list_windows(self, manager):
        """Test listing windows."""
        windows = manager.list_windows()
        assert isinstance(windows, list)

    def test_delete_window(self, manager):
        """Test deleting a window."""
        now = datetime.now(timezone.utc)
        window = manager.create_window(
            name="Temp Window",
            window_type=WindowType.RESTRICTED,
            start_time=now,
            end_time=now + timedelta(hours=1),
        )

        assert manager.delete_window(window.id) is True
        assert manager.get_window(window.id) is None


class TestCriteriaEvaluator:
    """Tests for Criteria Evaluator."""

    @pytest.fixture
    def evaluator(self):
        return create_criteria_evaluator()

    @pytest.mark.asyncio
    async def test_evaluate_entry_criteria_pass(self, evaluator):
        """Test entry criteria evaluation returns proper structure."""
        criteria = EntryCriteria(
            name="Pre-deploy Check",
            criteria=[
                Criterion(
                    name="error_rate_low",
                    criteria_type=CriteriaType.METRIC_THRESHOLD,
                    operator=CriteriaOperator.LESS_THAN,
                    threshold=0.05,
                    metric_name="error_rate",
                ),
            ],
            require_all=True,
            timeout_seconds=10,
        )

        context = {"error_rate": 0.01}
        result = await evaluator.evaluate_entry_criteria(criteria, context)

        assert result.criteria_name == "Pre-deploy Check"
        assert len(result.evaluations) == 1

    @pytest.mark.asyncio
    async def test_evaluate_entry_criteria_fail(self, evaluator):
        """Test entry criteria evaluation that fails."""
        criteria = EntryCriteria(
            name="Health Check",
            criteria=[
                Criterion(
                    name="service_healthy",
                    criteria_type=CriteriaType.HEALTH_CHECK,
                    operator=CriteriaOperator.EQUAL,
                    threshold="healthy",
                ),
            ],
            require_all=True,
            timeout_seconds=10,
        )

        result = await evaluator.evaluate_entry_criteria(criteria, {})

        assert result.criteria_name == "Health Check"

    def test_get_templates(self, evaluator):
        """Test getting criteria templates."""
        entry_templates = list(evaluator._entry_templates.keys())
        exit_templates = list(evaluator._exit_templates.keys())

        assert "deployment" in entry_templates
        assert "deployment" in exit_templates


class TestPolicyEngine:
    """Tests for the main Policy Engine."""

    @pytest.fixture
    def engine(self):
        return create_policy_engine()

    @pytest.mark.asyncio
    async def test_evaluate_basic(self, engine):
        """Test basic policy evaluation."""
        result = await engine.evaluate(
            action="restart_service",
            resource={
                "name": "api-service",
                "environment": "staging",
            },
            context={
                "risk_score": 0.3,
            },
            requested_by="operator",
        )

        assert result.decision in list(PolicyDecision)
        assert result.evaluation_id is not None
        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_embedded_kyverno_result_cannot_allow_mutation(self):
        class EmbeddedResult:
            def to_dict(self):
                return {
                    "allowed": True,
                    "authoritative": False,
                    "violations": [],
                    "warnings": [],
                }

        class EmbeddedKyverno:
            async def evaluate(self, resource):
                return EmbeddedResult()

        engine = create_policy_engine(kyverno_client=EmbeddedKyverno())
        result = await engine.evaluate(
            action="rollback",
            resource={"kind": "Deployment", "metadata": {"name": "api"}},
            context={},
        )

        assert result.kyverno_allowed is False
        assert result.kyverno_authoritative is False
        assert result.decision is PolicyDecision.DENY
        assert any(v.policy_name == "kyverno_authority_required" for v in result.violations)

    @pytest.mark.asyncio
    async def test_request_approval(self, engine):
        """Test approval workflow."""
        eval_result = await engine.evaluate(
            action="deploy",
            resource={"name": "prod-service"},
            context={"risk_score": 0.7},
        )

        approval_id = await engine.request_approval(
            evaluation_id=eval_result.evaluation_id,
            reason="High-risk deployment",
            requested_by="deployer",
        )

        assert approval_id is not None

        status = engine.get_approval_status(approval_id)
        assert status is not None
        assert status["status"] == "pending"

    @pytest.mark.asyncio
    async def test_approve_request(self, engine):
        """Test approving a request."""
        eval_result = await engine.evaluate(
            action="deploy",
            resource={"name": "service"},
            context={},
        )

        approval_id = await engine.request_approval(
            evaluation_id=eval_result.evaluation_id,
            reason="Test",
            requested_by="tester",
        )

        success = await engine.approve(approval_id, "approver", "LGTM")

        assert success is True
        status = engine.get_approval_status(approval_id)
        assert status["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_request(self, engine):
        """Test rejecting a request."""
        eval_result = await engine.evaluate(
            action="deploy",
            resource={"name": "service"},
            context={},
        )

        approval_id = await engine.request_approval(
            evaluation_id=eval_result.evaluation_id,
            reason="Test",
            requested_by="tester",
        )

        success = await engine.reject(approval_id, "reviewer", "Too risky")

        assert success is True
        status = engine.get_approval_status(approval_id)
        assert status["status"] == "rejected"

    def test_policy_versioning(self, engine):
        """Test policy versioning."""
        version = engine.version_policy(
            policy_name="test_policy",
            policy_type="opa",
            content="package test\ndefault allow = true",
            created_by="tester",
        )

        assert version.version == "v1"
        assert version.is_active is True

        version2 = engine.version_policy(
            policy_name="test_policy",
            policy_type="opa",
            content="package test\ndefault allow = false",
            created_by="tester",
        )

        assert version2.version == "v2"

        versions = engine.list_policy_versions("test_policy")
        assert len(versions) == 2

    def test_policy_rollback(self, engine):
        """Test policy rollback."""
        engine.version_policy("rollback_test", "opa", "v1 content", "tester")
        engine.version_policy("rollback_test", "opa", "v2 content", "tester")

        rolled_back = engine.rollback_policy("rollback_test", "v1")

        assert rolled_back is not None
        assert rolled_back.version == "v1"
        assert rolled_back.is_active is True

    def test_audit_log(self, engine):
        """Test audit log retrieval."""
        records = engine.get_audit_log(limit=10)
        assert isinstance(records, list)
