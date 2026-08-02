"""Pre-Deploy Chaos Gates.

Validates deployments with chaos tests before allowing promotion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.chaos_engineering.gates")


class GateStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ChaosTest:
    """A chaos test in the gate."""

    test_id: str
    name: str
    description: str
    status: GateStatus = GateStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.test_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "error": self.error_message,
        }


@dataclass
class GateResult:
    """Result of a chaos gate evaluation."""

    gate_id: str
    deploy_id: str
    status: GateStatus
    tests: list[ChaosTest]
    total_tests: int
    passed_tests: int
    failed_tests: int
    can_proceed: bool
    started_at: datetime
    ended_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "deploy_id": self.deploy_id,
            "status": self.status.value,
            "tests": [t.to_dict() for t in self.tests],
            "total": self.total_tests,
            "passed": self.passed_tests,
            "failed": self.failed_tests,
            "can_proceed": self.can_proceed,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


@dataclass
class ChaosGate:
    """A chaos gate configuration."""

    gate_id: str
    name: str
    environment: str
    required_tests: list[str]
    min_pass_percent: float = 100.0
    enabled: bool = True
    fail_fast: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.gate_id,
            "name": self.name,
            "environment": self.environment,
            "required_tests": self.required_tests,
            "min_pass_percent": self.min_pass_percent,
            "enabled": self.enabled,
            "fail_fast": self.fail_fast,
        }


class PreDeployChaosGate:
    """Manages pre-deploy chaos gates."""

    def __init__(self) -> None:
        self._gates: dict[str, ChaosGate] = {}
        self._results: dict[str, GateResult] = {}
        self._running: dict[str, GateResult] = {}
        self._init_default_gates()

    def _init_default_gates(self) -> None:
        """Initialize default chaos gates."""
        self._gates["staging"] = ChaosGate(
            gate_id="staging",
            name="Staging Chaos Gate",
            environment="staging",
            required_tests=[
                "pod_failure_recovery",
                "memory_pressure",
                "network_partition",
                "dependency_timeout",
            ],
            min_pass_percent=100.0,
        )

        self._gates["production"] = ChaosGate(
            gate_id="production",
            name="Production Chaos Gate",
            environment="production",
            required_tests=[
                "pod_failure_recovery",
                "memory_pressure",
            ],
            min_pass_percent=100.0,
            fail_fast=True,
        )

    def get_gate(self, gate_id: str) -> ChaosGate | None:
        """Get a gate configuration."""
        return self._gates.get(gate_id)

    def configure_gate(
        self,
        gate_id: str,
        name: str,
        environment: str,
        required_tests: list[str],
        min_pass_percent: float = 100.0,
    ) -> ChaosGate:
        """Configure a chaos gate."""
        gate = ChaosGate(
            gate_id=gate_id,
            name=name,
            environment=environment,
            required_tests=required_tests,
            min_pass_percent=min_pass_percent,
        )

        self._gates[gate_id] = gate

        log.info("Configured gate: id=%s env=%s", gate_id, environment)

        return gate

    def evaluate_gate(
        self,
        gate_id: str,
        deploy_id: str,
    ) -> GateResult:
        """Start evaluating a chaos gate."""
        gate = self._gates.get(gate_id)
        if not gate:
            raise ValueError(f"Gate not found: {gate_id}")

        if not gate.enabled:
            return GateResult(
                gate_id=gate_id,
                deploy_id=deploy_id,
                status=GateStatus.SKIPPED,
                tests=[],
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                can_proceed=True,
                started_at=datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
            )

        tests = []
        for test_name in gate.required_tests:
            tests.append(ChaosTest(
                test_id=f"test_{test_name}_{deploy_id}",
                name=test_name.replace("_", " ").title(),
                description=f"Running {test_name} chaos test",
                status=GateStatus.PENDING,
            ))

        result = GateResult(
            gate_id=gate_id,
            deploy_id=deploy_id,
            status=GateStatus.RUNNING,
            tests=tests,
            total_tests=len(tests),
            passed_tests=0,
            failed_tests=0,
            can_proceed=False,
            started_at=datetime.now(timezone.utc),
        )

        self._running[deploy_id] = result

        log.info("Started gate evaluation: gate=%s deploy=%s", gate_id, deploy_id)

        return result

    def update_test_status(
        self,
        deploy_id: str,
        test_id: str,
        status: GateStatus,
        error_message: str = "",
    ) -> bool:
        """Update status of a test in the gate."""
        result = self._running.get(deploy_id)
        if not result:
            return False

        for test in result.tests:
            if test.test_id == test_id:
                test.status = status
                test.error_message = error_message

                if status == GateStatus.RUNNING:
                    test.started_at = datetime.now(timezone.utc)
                elif status in [GateStatus.PASSED, GateStatus.FAILED]:
                    test.ended_at = datetime.now(timezone.utc)

                break

        result.passed_tests = sum(
            1 for t in result.tests if t.status == GateStatus.PASSED
        )
        result.failed_tests = sum(
            1 for t in result.tests if t.status == GateStatus.FAILED
        )

        return True

    def complete_evaluation(self, deploy_id: str) -> GateResult | None:
        """Complete gate evaluation and determine result."""
        result = self._running.get(deploy_id)
        if not result:
            return None

        gate = self._gates.get(result.gate_id)
        if not gate:
            return None

        all_completed = all(
            t.status in [GateStatus.PASSED, GateStatus.FAILED, GateStatus.SKIPPED]
            for t in result.tests
        )

        if not all_completed:
            return result

        if result.total_tests > 0:
            pass_percent = (result.passed_tests / result.total_tests) * 100
        else:
            pass_percent = 100

        result.can_proceed = pass_percent >= gate.min_pass_percent
        result.status = GateStatus.PASSED if result.can_proceed else GateStatus.FAILED
        result.ended_at = datetime.now(timezone.utc)

        self._results[deploy_id] = result
        del self._running[deploy_id]

        log.info(
            "Gate evaluation complete: deploy=%s passed=%s",
            deploy_id,
            result.can_proceed,
        )

        return result

    def get_gate_status(self, deploy_id: str) -> dict[str, Any]:
        """Get current status of gate evaluation."""
        if deploy_id in self._running:
            result = self._running[deploy_id]
            return {
                "status": result.status.value,
                "tests": [t.to_dict() for t in result.tests],
                "progress": f"{result.passed_tests + result.failed_tests}/{result.total_tests}",
                "can_proceed": False,
            }

        if deploy_id in self._results:
            result = self._results[deploy_id]
            return result.to_dict()

        return {"status": "not_found"}

    def get_gate_history(
        self,
        gate_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Get history of gate evaluations."""
        history = []
        for deploy_id, result in self._results.items():
            if result.gate_id == gate_id:
                history.append({
                    "deploy_id": deploy_id,
                    "status": result.status.value,
                    "passed": result.passed_tests,
                    "total": result.total_tests,
                    "started_at": result.started_at.isoformat(),
                })

        history.sort(key=lambda x: x["started_at"], reverse=True)
        return history[:limit]


_gate: PreDeployChaosGate | None = None


def get_chaos_gate() -> PreDeployChaosGate:
    """Get the default chaos gate."""
    global _gate
    if _gate is None:
        _gate = PreDeployChaosGate()
    return _gate
