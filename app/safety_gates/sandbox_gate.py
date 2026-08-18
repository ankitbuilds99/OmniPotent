"""Sandbox Gate - Layer 3 of 3-Layer Safety Gates.

Tests the proposed fix in an isolated environment:
1. Creates isolated sandbox (K8s namespace, Docker, temp directory)
2. Applies the proposed change
3. Runs verification tests
4. Checks for side effects
5. Verifies rollback works
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.safety_gates.models import GateResult

log = logging.getLogger("omnipotent.safety_gates.sandbox")

# Neither executor below actually applies the change or runs the tests: they
# sleep and return a green result. This gate is the last thing standing between
# an autonomous remediation and production, so a fabricated pass here fails
# *open* — the most dangerous direction for a gate. Unless the operator asked
# for a simulation, an unimplemented sandbox reports that it verified nothing.
_NOT_VERIFIED = (
    "sandbox verification is not implemented for this executor: the change was "
    "not applied and no tests were run (inject a real SandboxExecutor, or set "
    "COPILOT_DEMO_MODE=true to simulate); refusing to report a fix as verified"
)


def _demo_mode() -> bool:
    """True when the operator explicitly opted into simulated verification."""
    return get_settings().copilot_demo_mode


def _unverified_result(executor: str) -> dict[str, Any]:
    """Fail-closed payload shared by the stub executors' step methods."""
    log.warning("%s: %s", executor, _NOT_VERIFIED)
    return {"success": False, "error": _NOT_VERIFIED, "not_configured": True}


@dataclass
class SandboxTestResult:
    """Result of running tests in sandbox."""
    passed: int = 0
    failed: int = 0
    total: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    # True when the counts above were made up by a stub executor in demo mode
    # rather than observed from a real test run.
    simulated: bool = False


@dataclass
class SideEffect:
    """A side effect detected during sandbox execution."""
    severity: str  # critical, high, medium, low
    description: str
    details: dict[str, Any] = field(default_factory=dict)


class SandboxExecutor(ABC):
    """Base class for sandbox executors."""

    @abstractmethod
    async def create_sandbox(
        self,
        sandbox_id: str,
        action_type: str,
        config: dict[str, Any],
    ) -> str:
        """Create an isolated sandbox environment."""
        pass

    @abstractmethod
    async def apply_change(
        self,
        sandbox_id: str,
        action_type: str,
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply the proposed change in the sandbox."""
        pass

    @abstractmethod
    async def run_tests(
        self,
        sandbox_id: str,
        test_suite: str,
    ) -> SandboxTestResult:
        """Run verification tests."""
        pass

    @abstractmethod
    async def detect_side_effects(
        self,
        sandbox_id: str,
    ) -> list[SideEffect]:
        """Detect any side effects from the change."""
        pass

    @abstractmethod
    async def verify_rollback(
        self,
        sandbox_id: str,
    ) -> dict[str, Any]:
        """Verify that rollback works correctly."""
        pass

    @abstractmethod
    async def destroy_sandbox(
        self,
        sandbox_id: str,
    ) -> None:
        """Clean up the sandbox environment."""
        pass


class LocalSandboxExecutor(SandboxExecutor):
    """Sandbox executor that uses local temp directories.

    Suitable for testing code changes, file operations, etc.
    """

    def __init__(self):
        self._sandboxes: dict[str, dict[str, Any]] = {}

    async def create_sandbox(
        self,
        sandbox_id: str,
        action_type: str,
        config: dict[str, Any],
    ) -> str:
        import tempfile

        temp_dir = tempfile.mkdtemp(prefix=f"sandbox_{sandbox_id}_")
        self._sandboxes[sandbox_id] = {
            "path": temp_dir,
            "action_type": action_type,
            "config": config,
            "created_at": time.time(),
        }
        log.debug("Created local sandbox: %s at %s", sandbox_id, temp_dir)
        return sandbox_id

    async def apply_change(
        self,
        sandbox_id: str,
        action_type: str,
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if sandbox_id not in self._sandboxes:
            return {"success": False, "error": "Sandbox not found"}

        if not _demo_mode():
            return _unverified_result("LocalSandboxExecutor.apply_change")

        # Simulate applying change
        await asyncio.sleep(0.1)  # Simulate work
        self._sandboxes[sandbox_id]["change_applied"] = True

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING change application in sandbox")
        return {"success": True, "simulated": True}

    async def run_tests(
        self,
        sandbox_id: str,
        test_suite: str,
    ) -> SandboxTestResult:
        if sandbox_id not in self._sandboxes:
            return SandboxTestResult(total=0, failures=[{"error": "Sandbox not found"}])

        if not _demo_mode():
            log.warning("LocalSandboxExecutor.run_tests: %s", _NOT_VERIFIED)
            # One failed "test" so the gate scores this 0.0 and cannot pass.
            return SandboxTestResult(
                passed=0,
                failed=1,
                total=1,
                failures=[{"error": _NOT_VERIFIED}],
            )

        # Simulate running tests
        await asyncio.sleep(0.2)

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING a passing sandbox test run")

        # Default: all tests pass
        return SandboxTestResult(
            passed=5,
            failed=0,
            total=5,
            failures=[],
            duration_ms=200,
            simulated=True,
        )

    async def detect_side_effects(
        self,
        sandbox_id: str,
    ) -> list[SideEffect]:
        # No side effects in local sandbox
        return []

    async def verify_rollback(
        self,
        sandbox_id: str,
    ) -> dict[str, Any]:
        if sandbox_id not in self._sandboxes:
            return {"success": False, "error": "Sandbox not found"}

        if not _demo_mode():
            return _unverified_result("LocalSandboxExecutor.verify_rollback")

        # Simulate rollback verification
        await asyncio.sleep(0.1)
        log.warning("COPILOT_DEMO_MODE=true - SIMULATING rollback verification")
        return {"success": True, "simulated": True}

    async def destroy_sandbox(
        self,
        sandbox_id: str,
    ) -> None:
        import shutil

        if sandbox_id in self._sandboxes:
            path = self._sandboxes[sandbox_id].get("path")
            if path:
                try:
                    shutil.rmtree(path, ignore_errors=True)
                except Exception as e:
                    log.warning("Failed to clean up sandbox %s: %s", sandbox_id, e)
            del self._sandboxes[sandbox_id]


class KubernetesSandboxExecutor(SandboxExecutor):
    """Sandbox executor that creates isolated K8s namespaces.

    Creates ephemeral namespace, deploys test workloads,
    runs health checks, then cleans up.
    """

    def __init__(
        self,
        k8s_client: Any | None = None,
        namespace_prefix: str = "sandbox-",
        resource_limits: dict[str, str] | None = None,
    ):
        self.k8s_client = k8s_client
        self.namespace_prefix = namespace_prefix
        self.resource_limits = resource_limits or {
            "cpu": "500m",
            "memory": "512Mi",
        }
        self._sandboxes: dict[str, dict[str, Any]] = {}

    async def create_sandbox(
        self,
        sandbox_id: str,
        action_type: str,
        config: dict[str, Any],
    ) -> str:
        namespace = f"{self.namespace_prefix}{sandbox_id[:8]}"

        # In production: use kubernetes client to create namespace
        # For now, simulate
        self._sandboxes[sandbox_id] = {
            "namespace": namespace,
            "action_type": action_type,
            "config": config,
            "created_at": time.time(),
        }

        log.info("Created K8s sandbox namespace: %s", namespace)
        return sandbox_id

    async def apply_change(
        self,
        sandbox_id: str,
        action_type: str,
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if sandbox_id not in self._sandboxes:
            return {"success": False, "error": "Sandbox not found"}

        # No manifest is ever applied here, whether or not a k8s_client was
        # handed in, so this is a stub in every configuration.
        if not _demo_mode():
            return _unverified_result("KubernetesSandboxExecutor.apply_change")

        # In production: apply K8s manifests, run helm upgrade, etc.
        await asyncio.sleep(0.5)  # Simulate deployment time

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING manifest application in sandbox")
        return {"success": True, "simulated": True}

    async def run_tests(
        self,
        sandbox_id: str,
        test_suite: str,
    ) -> SandboxTestResult:
        if not _demo_mode():
            log.warning("KubernetesSandboxExecutor.run_tests: %s", _NOT_VERIFIED)
            return SandboxTestResult(
                passed=0,
                failed=1,
                total=1,
                failures=[{"error": _NOT_VERIFIED}],
            )

        # In production: run test pods, health checks
        await asyncio.sleep(1.0)

        log.warning("COPILOT_DEMO_MODE=true - SIMULATING a passing sandbox test run")
        return SandboxTestResult(
            passed=10,
            failed=0,
            total=10,
            failures=[],
            duration_ms=1000,
            simulated=True,
        )

    async def detect_side_effects(
        self,
        sandbox_id: str,
    ) -> list[SideEffect]:
        # In production: check resource usage, network calls, etc.
        return []

    async def verify_rollback(
        self,
        sandbox_id: str,
    ) -> dict[str, Any]:
        if not _demo_mode():
            return _unverified_result("KubernetesSandboxExecutor.verify_rollback")

        # In production: run rollback and verify state
        await asyncio.sleep(0.5)
        log.warning("COPILOT_DEMO_MODE=true - SIMULATING rollback verification")
        return {"success": True, "simulated": True}

    async def destroy_sandbox(
        self,
        sandbox_id: str,
    ) -> None:
        if sandbox_id in self._sandboxes:
            namespace = self._sandboxes[sandbox_id].get("namespace")
            # In production: delete namespace
            log.info("Destroying K8s sandbox namespace: %s", namespace)
            del self._sandboxes[sandbox_id]


class SandboxGate:
    """Gate 3: Tests the proposed fix in an isolated environment.

    This gate can be skipped for certain action types (like creating PRs)
    or when explicitly disabled.
    """

    # Actions that skip sandbox (they're reviewable externally)
    SKIP_SANDBOX_ACTIONS = {"create_pr", "create_issue", "send_notification"}

    def __init__(
        self,
        executor: SandboxExecutor | None = None,
        timeout_seconds: int = 300,
        enabled: bool = True,
    ):
        """Initialize the sandbox gate.

        Args:
            executor: Sandbox executor to use (defaults to local)
            timeout_seconds: Max time for sandbox verification
            enabled: Whether sandbox verification is enabled
        """
        self.executor = executor or LocalSandboxExecutor()
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled

    async def evaluate(
        self,
        action_type: str,
        action_details: dict[str, Any],
        organization_id: str | None = None,
        skip_sandbox: bool = False,
    ) -> GateResult:
        """Run sandboxed verification of the proposed action.

        Args:
            action_type: Type of action
            action_details: Action parameters
            organization_id: For org-specific sandbox config
            skip_sandbox: Force skip sandbox verification

        Returns:
            GateResult with test results
        """
        start_time = time.time()

        # Check if we should skip
        if not self.enabled or skip_sandbox or action_type in self.SKIP_SANDBOX_ACTIONS:
            return GateResult(
                gate_name="sandbox",
                passed=True,
                score=1.0,
                reasoning=f"Sandbox skipped (action type '{action_type}' does not require sandbox testing)",
                violations=[],
                duration_ms=0,
            )

        sandbox_id = uuid4().hex[:12]

        try:
            # Step 1: Create sandbox
            await self.executor.create_sandbox(
                sandbox_id=sandbox_id,
                action_type=action_type,
                config=action_details.get("sandbox_config", {}),
            )

            # Step 2: Apply the change
            apply_result = await self.executor.apply_change(
                sandbox_id=sandbox_id,
                action_type=action_type,
                action_payload=action_details,
            )

            if not apply_result.get("success"):
                # "We could not verify this" is a different answer from "this
                # change broke the sandbox": the first needs a human to decide,
                # the second is evidence the action is bad. Either way the gate
                # does not pass, so the action is never auto-approved.
                not_configured = bool(apply_result.get("not_configured"))
                await self.executor.destroy_sandbox(sandbox_id)
                return GateResult(
                    gate_name="sandbox",
                    passed=False,
                    score=0.0,
                    reasoning=(
                        f"Sandbox verification unavailable: {apply_result.get('error')}"
                        if not_configured
                        else f"Failed to apply change in sandbox: {apply_result.get('error')}"
                    ),
                    violations=[{
                        "type": "sandbox_not_configured" if not_configured else "sandbox_apply_failed",
                        "severity": "high",
                        "message": apply_result.get("error", "Unknown error"),
                        "action": "escalate" if not_configured else "block",
                    }],
                    duration_ms=int((time.time() - start_time) * 1000),
                )

            # Step 3: Run tests
            test_result = await self.executor.run_tests(
                sandbox_id=sandbox_id,
                test_suite=action_details.get("test_suite", "smoke"),
            )

            # Step 4: Detect side effects
            side_effects = await self.executor.detect_side_effects(
                sandbox_id=sandbox_id,
            )

            # Step 5: Verify rollback
            rollback_result = await self.executor.verify_rollback(
                sandbox_id=sandbox_id,
            )

            # Step 6: Cleanup
            await self.executor.destroy_sandbox(sandbox_id)

            # Evaluate results
            violations = []
            passed = True

            # Check test results
            if test_result.failed > 0:
                passed = False
                violations.append({
                    "type": "tests_failed",
                    "severity": "high",
                    "message": f"{test_result.failed}/{test_result.total} tests failed in sandbox",
                    "action": "block",
                    "details": {"failures": test_result.failures},
                })

            # Check side effects
            for effect in side_effects:
                if effect.severity == "critical":
                    passed = False
                violations.append({
                    "type": "side_effect_detected",
                    "severity": effect.severity,
                    "message": effect.description,
                    "action": "require_approval" if effect.severity != "critical" else "block",
                    "details": effect.details,
                })

            # Check rollback
            if not rollback_result.get("success"):
                passed = False
                violations.append({
                    "type": "rollback_failed",
                    "severity": "critical",
                    "message": "Unable to verify rollback in sandbox",
                    "action": "block",
                })

            duration_ms = int((time.time() - start_time) * 1000)

            return GateResult(
                gate_name="sandbox",
                passed=passed,
                score=test_result.passed / max(test_result.total, 1),
                reasoning=self._build_sandbox_reasoning(
                    test_result, side_effects, rollback_result, violations
                ),
                violations=violations,
                duration_ms=duration_ms,
            )

        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start_time) * 1000)
            return GateResult(
                gate_name="sandbox",
                passed=False,
                score=0.0,
                reasoning=f"Sandbox verification timed out after {self.timeout_seconds}s",
                violations=[{
                    "type": "sandbox_timeout",
                    "severity": "high",
                    "message": f"Sandbox verification timed out after {self.timeout_seconds}s",
                    "action": "escalate",
                }],
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log.exception("Sandbox gate failed")
            return GateResult(
                gate_name="sandbox",
                passed=False,
                score=0.0,
                reasoning=f"Sandbox execution failed: {e}",
                violations=[{
                    "type": "sandbox_error",
                    "severity": "high",
                    "message": str(e),
                    "action": "escalate",
                }],
                duration_ms=duration_ms,
            )

    def _build_sandbox_reasoning(
        self,
        test_result: SandboxTestResult,
        side_effects: list[SideEffect],
        rollback_result: dict[str, Any],
        violations: list[dict[str, Any]],
    ) -> str:
        """Build human-readable sandbox reasoning."""
        # A reviewer reading this block must be able to tell measured results
        # from a demo-mode stand-in, so the marker travels with the numbers.
        suffix = " (simulated)" if test_result.simulated else ""
        lines = [
            f"**Sandbox Verification Results:**{suffix}",
            "",
            f"✓ Tests: {test_result.passed}/{test_result.total} passed{suffix}",
            f"{'✓' if not side_effects else '⚠️'} Side Effects: {len(side_effects)} detected",
            f"{'✓' if rollback_result.get('success') else '✗'} Rollback: {'Verified' if rollback_result.get('success') else 'Failed'}",
            "",
        ]

        if violations:
            lines.append("**Issues Found:**")
            for v in violations:
                severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}.get(
                    v.get("severity", "medium"), "⚪"
                )
                lines.append(f"  {severity_icon} [{v.get('severity', 'medium').upper()}] {v['message']}")
        else:
            lines.append("✅ All sandbox checks passed.")

        return "\n".join(lines)
