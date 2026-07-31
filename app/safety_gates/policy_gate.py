"""Policy Gate - Layer 2 of 3-Layer Safety Gates.

Evaluates actions against organizational policies:
1. Blast radius - How many services/users affected?
2. Time windows - Is this change allowed now?
3. Service protection - Is target service protected?
4. Injection detection - Malicious patterns?
5. Custom org rules
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.safety_gates.models import GateResult

log = logging.getLogger("devcopilot.safety_gates.policy")


class PolicyGate:
    """Gate 2: Evaluates action against organizational policies.

    Checks blast radius, time windows, protected services,
    injection patterns, and custom rules.
    """

    # Injection patterns to detect
    INJECTION_PATTERNS = [
        (r';\s*rm\s+-rf', "rm -rf injection"),
        (r'\|\s*bash', "pipe to bash"),
        (r'`.*`', "backtick command execution"),
        (r'\$\(.*\)', "command substitution"),
        (r'>\s*/dev/sd', "write to disk device"),
        (r'curl.*\|\s*sh', "curl pipe to shell"),
        (r'wget.*\|\s*sh', "wget pipe to shell"),
        (r'&&\s*(rm|dd|mkfs)', "destructive command chain"),
        (r'DROP\s+TABLE', "SQL DROP TABLE"),
        (r'DELETE\s+FROM.*WHERE\s+1\s*=\s*1', "SQL delete all"),
        (r';\s*shutdown', "shutdown command"),
        (r'chmod\s+777', "insecure permissions"),
    ]

    # Max blast radius by action type
    MAX_BLAST_RADIUS = {
        "scale_down": 3,
        "rollback": 5,
        "restart": 2,
        "deploy_fix": 10,
        "run_command": 1,
        "scale_up": 10,  # Scaling up affects less
    }

    def __init__(
        self,
        protected_services: dict[str, list[str]] | None = None,
        change_freeze_windows: list[dict[str, Any]] | None = None,
        custom_rules: list[dict[str, Any]] | None = None,
    ):
        """Initialize the policy gate.

        Args:
            protected_services: Map of service -> blocked action types
            change_freeze_windows: Time windows when changes are restricted
            custom_rules: Custom policy rules
        """
        self.protected_services = protected_services or {
            "payments": ["scale_down", "restart"],
            "auth": ["scale_down", "run_command"],
            "database": ["restart", "run_command", "deploy_fix"],
        }
        self.change_freeze_windows = change_freeze_windows or []
        self.custom_rules = custom_rules or []

    def evaluate(
        self,
        action_type: str,
        action_details: dict[str, Any],
        organization_id: str | None = None,
        target_service: str | None = None,
        target_environment: str = "production",
    ) -> GateResult:
        """Evaluate action against all policy rules.

        Args:
            action_type: Type of action
            action_details: Action parameters
            organization_id: For org-specific rules
            target_service: Service being acted on
            target_environment: Environment (production, staging, etc.)

        Returns:
            GateResult with violations list
        """
        start_time = time.time()
        violations = []

        # Check 1: Blast radius
        blast_violations = self._check_blast_radius(
            action_type, action_details, target_service
        )
        violations.extend(blast_violations)

        # Check 2: Time window
        time_violations = self._check_time_window(
            target_environment, organization_id
        )
        violations.extend(time_violations)

        # Check 3: Service protection
        protection_violations = self._check_service_protection(
            target_service, action_type
        )
        violations.extend(protection_violations)

        # Check 4: Injection detection
        injection_violations = self._check_injection(action_details)
        violations.extend(injection_violations)

        # Check 5: Custom org rules
        custom_violations = self._check_custom_rules(
            action_type, action_details, target_service
        )
        violations.extend(custom_violations)

        passed = len(violations) == 0
        duration_ms = int((time.time() - start_time) * 1000)

        # Calculate blast radius score (0-10, lower is safer)
        blast_radius_score = self._calculate_blast_radius_score(
            action_type, action_details, target_service
        )

        log.info(
            "Policy gate: passed=%s violations=%d blast_radius=%d action=%s",
            passed, len(violations), blast_radius_score, action_type,
        )

        return GateResult(
            gate_name="policy",
            passed=passed,
            score=1.0 - (blast_radius_score / 10.0),  # Invert so higher is better
            reasoning=self._build_policy_reasoning(violations, blast_radius_score),
            violations=violations,
            duration_ms=duration_ms,
        )

    def _check_blast_radius(
        self,
        action_type: str,
        action_details: dict[str, Any],
        target_service: str | None,
    ) -> list[dict[str, Any]]:
        """Check if action blast radius is acceptable."""
        violations = []

        estimated_blast = action_details.get("estimated_blast_radius", 1)
        affected_services = action_details.get("affected_services", [])

        # Use number of affected services if not explicitly set
        if estimated_blast == 1 and affected_services:
            estimated_blast = len(affected_services)

        max_allowed = self.MAX_BLAST_RADIUS.get(action_type, 5)

        if estimated_blast > max_allowed:
            violations.append({
                "type": "blast_radius_exceeded",
                "severity": "high",
                "message": f"Action affects {estimated_blast} services, max allowed is {max_allowed}",
                "action": "require_approval",
                "details": {
                    "estimated_blast": estimated_blast,
                    "max_allowed": max_allowed,
                    "affected_services": affected_services,
                },
            })

        return violations

    def _check_time_window(
        self,
        target_environment: str,
        organization_id: str | None,
    ) -> list[dict[str, Any]]:
        """Check if changes are allowed at this time."""
        violations = []
        now = datetime.now(timezone.utc)

        # Default freeze: Friday afternoons for production
        if target_environment == "production":
            if now.weekday() == 4 and now.hour >= 14:  # Friday after 2pm UTC
                violations.append({
                    "type": "change_freeze",
                    "severity": "medium",
                    "message": "Production changes restricted on Friday afternoons",
                    "action": "require_approval",
                    "details": {
                        "current_time": now.isoformat(),
                        "reason": "End-of-week change freeze",
                    },
                })

        # Check custom freeze windows
        for window in self.change_freeze_windows:
            if self._is_in_freeze_window(now, window, target_environment):
                violations.append({
                    "type": "change_freeze",
                    "severity": window.get("severity", "medium"),
                    "message": window.get("message", "Changes restricted during freeze window"),
                    "action": window.get("action", "require_approval"),
                    "details": window,
                })

        return violations

    def _is_in_freeze_window(
        self,
        now: datetime,
        window: dict[str, Any],
        environment: str,
    ) -> bool:
        """Check if current time is in a freeze window."""
        if window.get("environment") and window["environment"] != environment:
            return False

        start_hour = window.get("start_hour", 0)
        end_hour = window.get("end_hour", 24)
        days = window.get("days", list(range(7)))  # All days by default

        return now.weekday() in days and start_hour <= now.hour < end_hour

    def _check_service_protection(
        self,
        target_service: str | None,
        action_type: str,
    ) -> list[dict[str, Any]]:
        """Check if service has protection rules."""
        violations = []

        if not target_service:
            return violations

        # Check all protected services (support partial matching)
        for protected_name, blocked_actions in self.protected_services.items():
            if protected_name in target_service.lower():
                if action_type in blocked_actions:
                    violations.append({
                        "type": "protected_service",
                        "severity": "high",
                        "message": f"Service '{target_service}' is protected from '{action_type}' actions",
                        "action": "block",
                        "details": {
                            "service": target_service,
                            "action_type": action_type,
                            "protected_rule": protected_name,
                        },
                    })

        return violations

    def _check_injection(
        self,
        action_details: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Detect potential command/script injection."""
        violations = []

        def check_value(value: Any, path: str = "") -> None:
            if isinstance(value, str):
                for pattern, description in self.INJECTION_PATTERNS:
                    if re.search(pattern, value, re.IGNORECASE | re.DOTALL):
                        violations.append({
                            "type": "injection_detected",
                            "severity": "critical",
                            "message": f"Potential injection pattern detected: {description}",
                            "action": "block",
                            "details": {
                                "field": path,
                                "pattern": description,
                            },
                        })
            elif isinstance(value, dict):
                for k, v in value.items():
                    check_value(v, f"{path}.{k}" if path else k)
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    check_value(v, f"{path}[{i}]")

        check_value(action_details)

        return violations

    def _check_custom_rules(
        self,
        action_type: str,
        action_details: dict[str, Any],
        target_service: str | None,
    ) -> list[dict[str, Any]]:
        """Check organization-specific custom rules."""
        violations = []

        for rule in self.custom_rules:
            if self._matches_rule(rule, action_type, action_details, target_service):
                violations.append({
                    "type": "custom_rule",
                    "severity": rule.get("severity", "medium"),
                    "message": rule.get("message", "Custom rule violation"),
                    "action": rule.get("action", "require_approval"),
                    "details": {"rule_name": rule.get("name", "unnamed")},
                })

        return violations

    def _matches_rule(
        self,
        rule: dict[str, Any],
        action_type: str,
        action_details: dict[str, Any],
        target_service: str | None,
    ) -> bool:
        """Check if a custom rule matches the current action."""
        conditions = rule.get("conditions", {})

        # Check action type condition
        if "action_types" in conditions:
            if action_type not in conditions["action_types"]:
                return False

        # Check service condition
        if "services" in conditions and target_service:
            if not any(s in target_service for s in conditions["services"]):
                return False

        return True

    def _calculate_blast_radius_score(
        self,
        action_type: str,
        action_details: dict[str, Any],
        target_service: str | None,
    ) -> int:
        """Calculate blast radius score (1-10, higher = more dangerous)."""
        # Base scores by action type
        base_scores = {
            "rollback": 4,
            "scale_up": 2,
            "scale_down": 5,
            "restart": 3,
            "deploy_fix": 6,
            "run_command": 7,
        }

        score = base_scores.get(action_type, 5)

        # Adjust based on environment
        environment = action_details.get("environment", "production")
        if environment == "production":
            score += 2
        elif environment == "staging":
            score += 1

        # Adjust based on affected services count
        affected = action_details.get("affected_services", [])
        score += min(len(affected), 3)

        return min(score, 10)

    def _build_policy_reasoning(
        self,
        violations: list[dict[str, Any]],
        blast_radius_score: int,
    ) -> str:
        """Build human-readable policy reasoning."""
        lines = [
            f"**Blast Radius Score: {blast_radius_score}/10**",
            "",
        ]

        if not violations:
            lines.append("✅ All policy checks passed.")
        else:
            lines.append(f"**⚠️ Policy Violations Found: {len(violations)}**")
            lines.append("")

            for v in violations:
                severity_icon = {
                    "critical": "🔴",
                    "high": "🟠",
                    "medium": "🟡",
                    "low": "🟢",
                }.get(v.get("severity", "medium"), "⚪")

                lines.append(f"{severity_icon} **[{v.get('severity', 'medium').upper()}]** {v['type']}")
                lines.append(f"   {v['message']}")
                lines.append(f"   Action: {v.get('action', 'escalate')}")
                lines.append("")

        return "\n".join(lines)
