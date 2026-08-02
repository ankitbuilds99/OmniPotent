"""Root cause analysis with Five Whys and contributing factor discovery."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.postmortem_advanced.models import (
    FiveWhy,
    ContributingFactor,
    FactorCategory,
    FactorImpact,
    Evidence,
    EvidenceType,
    Hypothesis,
)

log = logging.getLogger("omnipotent.postmortem_advanced.analyzer")


@dataclass
class AnalysisConfidence:
    """Confidence metrics for analysis."""

    overall: float = 0.0
    evidence_quality: float = 0.0
    hypothesis_strength: float = 0.0
    alternative_considered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_percent": round(self.overall * 100, 1),
            "evidence_quality_percent": round(self.evidence_quality * 100, 1),
            "hypothesis_strength_percent": round(self.hypothesis_strength * 100, 1),
            "alternatives_considered": self.alternative_considered,
        }


@dataclass
class FiveWhysResult:
    """Result of Five Whys analysis."""

    whys: list[FiveWhy]
    root_cause_summary: str
    confidence: AnalysisConfidence
    alternative_paths: list[list[FiveWhy]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "whys": [w.to_dict() for w in self.whys],
            "root_cause_summary": self.root_cause_summary,
            "confidence": self.confidence.to_dict(),
            "alternative_paths": [
                [w.to_dict() for w in path]
                for path in self.alternative_paths
            ],
        }


@dataclass
class ContributingFactorResult:
    """Result of contributing factor analysis."""

    factors: list[ContributingFactor]
    process_factors: list[ContributingFactor] = field(default_factory=list)
    technical_factors: list[ContributingFactor] = field(default_factory=list)
    human_factors: list[ContributingFactor] = field(default_factory=list)
    pattern_detected: bool = False
    similar_incident_factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_factors": len(self.factors),
            "by_category": {
                "process": [f.to_dict() for f in self.process_factors],
                "technical": [f.to_dict() for f in self.technical_factors],
                "human": [f.to_dict() for f in self.human_factors],
            },
            "pattern_detected": self.pattern_detected,
            "similar_incident_factors": self.similar_incident_factors,
        }


class RootCauseAnalyzer:
    """Analyzes incidents to determine root cause and contributing factors."""

    def __init__(self) -> None:
        self._known_patterns: list[dict[str, Any]] = self._load_known_patterns()
        self._factor_counter = 0

    def _load_known_patterns(self) -> list[dict[str, Any]]:
        """Load known incident patterns."""
        return [
            {
                "pattern": "connection.*exhaust|pool.*full",
                "category": "resource_leak",
                "common_causes": [
                    "Missing connection release in error path",
                    "Connection not returned in finally block",
                    "Timeout not configured",
                ],
                "common_factors": [
                    "Manual resource management",
                    "Missing context managers",
                    "No pool monitoring",
                ],
            },
            {
                "pattern": "memory.*leak|oom|out.of.memory",
                "category": "memory_leak",
                "common_causes": [
                    "Unbounded cache growth",
                    "Event listener not removed",
                    "Large object not dereferenced",
                ],
                "common_factors": [
                    "No memory profiling",
                    "Missing resource limits",
                ],
            },
            {
                "pattern": "timeout|deadline.*exceed",
                "category": "timeout",
                "common_causes": [
                    "Downstream service slow",
                    "Database query not optimized",
                    "Network latency spike",
                ],
                "common_factors": [
                    "No circuit breaker",
                    "Timeout too short or not set",
                ],
            },
            {
                "pattern": "deploy|rollout|release",
                "category": "deploy_related",
                "common_causes": [
                    "Bug introduced in new code",
                    "Configuration mismatch",
                    "Missing migration",
                ],
                "common_factors": [
                    "No canary deployment",
                    "Missing integration tests",
                    "Friday deploy",
                ],
            },
        ]

    def analyze_five_whys(
        self,
        incident_data: dict[str, Any],
        evidence: list[Evidence],
        max_depth: int = 5,
    ) -> FiveWhysResult:
        """Generate Five Whys analysis."""
        symptom = incident_data.get("symptom", "Service experienced errors")
        error_message = incident_data.get("error_message", "")

        pattern = self._identify_pattern(error_message)

        whys = self._generate_whys(symptom, pattern, evidence, max_depth)

        root_cause_summary = whys[-1].answer if whys else "Unknown"

        alternative_paths = self._generate_alternative_paths(
            symptom, pattern, evidence
        )

        confidence = self._calculate_confidence(whys, evidence, alternative_paths)

        return FiveWhysResult(
            whys=whys,
            root_cause_summary=root_cause_summary,
            confidence=confidence,
            alternative_paths=alternative_paths,
        )

    def _identify_pattern(self, error_message: str) -> dict[str, Any] | None:
        """Identify known pattern from error message."""
        error_lower = error_message.lower()

        for pattern in self._known_patterns:
            if re.search(pattern["pattern"], error_lower, re.IGNORECASE):
                return pattern

        return None

    def _generate_whys(
        self,
        symptom: str,
        pattern: dict[str, Any] | None,
        evidence: list[Evidence],
        max_depth: int,
    ) -> list[FiveWhy]:
        """Generate the Five Whys chain."""
        whys = []

        if pattern and pattern.get("category") == "resource_leak":
            whys = self._generate_resource_leak_whys(symptom, evidence)
        elif pattern and pattern.get("category") == "deploy_related":
            whys = self._generate_deploy_related_whys(symptom, evidence)
        else:
            whys = self._generate_generic_whys(symptom, evidence)

        return whys[:max_depth]

    def _generate_resource_leak_whys(
        self,
        symptom: str,
        evidence: list[Evidence],
    ) -> list[FiveWhy]:
        """Generate whys for resource leak pattern."""
        return [
            FiveWhy(
                level=1,
                question=f"Why did {symptom}?",
                answer="Resource pool was exhausted",
                evidence=self._find_relevant_evidence(evidence, ["exhaust", "pool"]),
                confidence=0.95,
            ),
            FiveWhy(
                level=2,
                question="Why was the pool exhausted?",
                answer="Resources were not being released after use",
                evidence=self._find_relevant_evidence(evidence, ["leak", "release"]),
                confidence=0.92,
            ),
            FiveWhy(
                level=3,
                question="Why weren't resources released?",
                answer="Error handling paths skipped resource cleanup",
                evidence=self._find_relevant_evidence(evidence, ["error", "code"]),
                confidence=0.88,
            ),
            FiveWhy(
                level=4,
                question="Why wasn't this caught in testing?",
                answer="Tests mock external resources, missing integration coverage",
                evidence=self._find_relevant_evidence(evidence, ["test", "mock"]),
                confidence=0.82,
            ),
            FiveWhy(
                level=5,
                question="Why no integration tests for resource lifecycle?",
                answer="Testing guidelines don't require resource lifecycle verification",
                evidence=[],
                confidence=0.75,
            ),
        ]

    def _generate_deploy_related_whys(
        self,
        symptom: str,
        evidence: list[Evidence],
    ) -> list[FiveWhy]:
        """Generate whys for deploy-related pattern."""
        return [
            FiveWhy(
                level=1,
                question=f"Why did {symptom}?",
                answer="A bug was introduced in the recent deployment",
                evidence=self._find_relevant_evidence(evidence, ["deploy", "release"]),
                confidence=0.90,
            ),
            FiveWhy(
                level=2,
                question="Why wasn't the bug caught before deploy?",
                answer="Code review and tests did not catch the issue",
                evidence=self._find_relevant_evidence(evidence, ["review", "test"]),
                confidence=0.85,
            ),
            FiveWhy(
                level=3,
                question="Why wasn't it caught in canary?",
                answer="No canary deployment configured, 100% rollout",
                evidence=self._find_relevant_evidence(evidence, ["canary", "rollout"]),
                confidence=0.92,
            ),
            FiveWhy(
                level=4,
                question="Why no canary deployment?",
                answer="Service not onboarded to progressive delivery",
                evidence=[],
                confidence=0.80,
            ),
            FiveWhy(
                level=5,
                question="Why not onboarded?",
                answer="No requirement or timeline for canary adoption",
                evidence=[],
                confidence=0.70,
            ),
        ]

    def _generate_generic_whys(
        self,
        symptom: str,
        evidence: list[Evidence],
    ) -> list[FiveWhy]:
        """Generate generic whys when no pattern matches."""
        return [
            FiveWhy(
                level=1,
                question=f"Why did {symptom}?",
                answer="An error occurred in the system",
                evidence=evidence[:2] if evidence else [],
                confidence=0.70,
            ),
            FiveWhy(
                level=2,
                question="Why did the error occur?",
                answer="A code or configuration change introduced an issue",
                evidence=[],
                confidence=0.60,
            ),
            FiveWhy(
                level=3,
                question="Why wasn't it prevented?",
                answer="Existing safeguards did not catch the issue",
                evidence=[],
                confidence=0.50,
            ),
            FiveWhy(
                level=4,
                question="Why didn't safeguards work?",
                answer="Test coverage or monitoring gaps",
                evidence=[],
                confidence=0.45,
            ),
            FiveWhy(
                level=5,
                question="Why are there gaps?",
                answer="Process or tooling improvements needed",
                evidence=[],
                confidence=0.40,
            ),
        ]

    def _find_relevant_evidence(
        self,
        evidence: list[Evidence],
        keywords: list[str],
    ) -> list[Evidence]:
        """Find evidence matching keywords."""
        relevant = []
        for e in evidence:
            content_lower = e.content.lower()
            if any(kw.lower() in content_lower for kw in keywords):
                relevant.append(e)
        return relevant[:3]

    def _generate_alternative_paths(
        self,
        symptom: str,
        pattern: dict[str, Any] | None,
        evidence: list[Evidence],
    ) -> list[list[FiveWhy]]:
        """Generate alternative causal paths."""
        alternatives = []

        alt1 = [
            FiveWhy(
                level=1,
                question=f"Why did {symptom}?",
                answer="Load spike triggered latent issue",
                evidence=[],
                confidence=0.30,
            ),
            FiveWhy(
                level=2,
                question="Why did load spike cause failure?",
                answer="System not scaled for peak load",
                evidence=[],
                confidence=0.25,
            ),
        ]
        alternatives.append(alt1)

        return alternatives

    def _calculate_confidence(
        self,
        whys: list[FiveWhy],
        evidence: list[Evidence],
        alternatives: list[list[FiveWhy]],
    ) -> AnalysisConfidence:
        """Calculate confidence in the analysis."""
        if not whys:
            return AnalysisConfidence()

        avg_why_confidence = sum(w.confidence for w in whys) / len(whys)

        evidence_score = min(1.0, len(evidence) / 10)

        evidence_for_whys = sum(1 for w in whys if w.evidence)
        hypothesis_strength = evidence_for_whys / len(whys)

        overall = (avg_why_confidence * 0.5 +
                   evidence_score * 0.3 +
                   hypothesis_strength * 0.2)

        return AnalysisConfidence(
            overall=overall,
            evidence_quality=evidence_score,
            hypothesis_strength=hypothesis_strength,
            alternative_considered=len(alternatives) > 0,
        )

    def analyze_contributing_factors(
        self,
        incident_data: dict[str, Any],
        evidence: list[Evidence],
        timeline: list[dict[str, Any]] | None = None,
    ) -> ContributingFactorResult:
        """Analyze contributing factors."""
        factors = []

        process_factors = self._analyze_process_factors(incident_data, timeline)
        factors.extend(process_factors)

        technical_factors = self._analyze_technical_factors(incident_data, evidence)
        factors.extend(technical_factors)

        human_factors = self._analyze_human_factors(incident_data)
        factors.extend(human_factors)

        pattern_detected = self._detect_recurring_pattern(factors)

        similar_factors = self._find_similar_incident_factors(factors)

        return ContributingFactorResult(
            factors=factors,
            process_factors=process_factors,
            technical_factors=technical_factors,
            human_factors=human_factors,
            pattern_detected=pattern_detected,
            similar_incident_factors=similar_factors,
        )

    def _analyze_process_factors(
        self,
        incident_data: dict[str, Any],
        timeline: list[dict[str, Any]] | None,
    ) -> list[ContributingFactor]:
        """Analyze process-related contributing factors."""
        factors = []
        self._factor_counter += 1

        deploy_type = incident_data.get("deploy_type", "full")
        if deploy_type in ["full", "100%"]:
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.PROCESS,
                impact=FactorImpact.HIGH,
                description="100% rollout without canary deployment",
                evidence=[Evidence(
                    evidence_type=EvidenceType.MANUAL,
                    source="deploy_config",
                    content=f"Deploy type: {deploy_type}",
                    timestamp=datetime.now(timezone.utc),
                )],
                recommendation="Implement gradual rollout (5% -> 25% -> 100%)",
            ))
            self._factor_counter += 1

        created_at = incident_data.get("created_at")
        if created_at:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))

            if created_at.weekday() == 4 and created_at.hour >= 14:
                factors.append(ContributingFactor(
                    factor_id=f"CF-{self._factor_counter}",
                    category=FactorCategory.PROCESS,
                    impact=FactorImpact.MEDIUM,
                    description="Friday afternoon deploy",
                    evidence=[Evidence(
                        evidence_type=EvidenceType.MANUAL,
                        source="timestamp",
                        content=f"Deploy at {created_at.strftime('%A %H:%M')}",
                        timestamp=datetime.now(timezone.utc),
                    )],
                    recommendation="Consider deploy freeze Friday after 2pm",
                ))
                self._factor_counter += 1

        if not incident_data.get("pre_deploy_checklist_completed"):
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.PROCESS,
                impact=FactorImpact.MEDIUM,
                description="No pre-deploy checklist followed",
                recommendation="Implement mandatory pre-deploy checklist",
            ))
            self._factor_counter += 1

        return factors

    def _analyze_technical_factors(
        self,
        incident_data: dict[str, Any],
        evidence: list[Evidence],
    ) -> list[ContributingFactor]:
        """Analyze technical contributing factors."""
        factors = []

        error_message = incident_data.get("error_message", "").lower()

        if "connection" in error_message or "pool" in error_message:
            self._factor_counter += 1
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.TECHNICAL,
                impact=FactorImpact.HIGH,
                description="Manual resource management pattern",
                evidence=[e for e in evidence if "connection" in e.content.lower()],
                recommendation="Use context managers for all resource handling",
            ))

        if not incident_data.get("has_pool_monitoring"):
            self._factor_counter += 1
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.TECHNICAL,
                impact=FactorImpact.MEDIUM,
                description="No resource pool monitoring",
                recommendation="Add pool utilization metrics and alerting",
            ))

        if not incident_data.get("has_integration_tests"):
            self._factor_counter += 1
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.TECHNICAL,
                impact=FactorImpact.MEDIUM,
                description="Missing integration test coverage",
                recommendation="Add integration tests for resource lifecycle",
            ))

        return factors

    def _analyze_human_factors(
        self,
        incident_data: dict[str, Any],
    ) -> list[ContributingFactor]:
        """Analyze human contributing factors."""
        factors = []

        if incident_data.get("author_first_time_contributor"):
            self._factor_counter += 1
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.HUMAN,
                impact=FactorImpact.LOW,
                description="PR author new to this code area",
                recommendation="Pair new contributors with experienced reviewers",
            ))

        if incident_data.get("reviewer_unfamiliar"):
            self._factor_counter += 1
            factors.append(ContributingFactor(
                factor_id=f"CF-{self._factor_counter}",
                category=FactorCategory.HUMAN,
                impact=FactorImpact.LOW,
                description="Code reviewer unfamiliar with subsystem",
                recommendation="Require domain expert review for critical paths",
            ))

        return factors

    def _detect_recurring_pattern(
        self,
        factors: list[ContributingFactor],
    ) -> bool:
        """Detect if factors indicate a recurring pattern."""
        high_impact_count = sum(
            1 for f in factors if f.impact == FactorImpact.HIGH
        )

        return high_impact_count >= 2 or len(factors) >= 5

    def _find_similar_incident_factors(
        self,
        factors: list[ContributingFactor],
    ) -> list[str]:
        """Find factors that appeared in similar incidents."""
        common_factors = []

        for factor in factors:
            if factor.similar_incidents:
                common_factors.extend(factor.similar_incidents)

        return list(set(common_factors))

    def generate_hypotheses(
        self,
        incident_data: dict[str, Any],
        evidence: list[Evidence],
    ) -> list[Hypothesis]:
        """Generate alternative hypotheses for root cause."""
        hypotheses = []

        error_message = incident_data.get("error_message", "")

        if "connection" in error_message.lower():
            hypotheses.append(Hypothesis(
                description="Connection leak in error handling path",
                likelihood=0.60,
                evidence_for=[
                    "Error pattern matches resource exhaustion",
                    "Pool metrics show linear growth",
                ],
                evidence_against=[],
            ))

            hypotheses.append(Hypothesis(
                description="Load spike exhausted normal capacity",
                likelihood=0.25,
                evidence_for=["Traffic was elevated during incident"],
                evidence_against=[
                    "Connection count grew linearly, not with traffic",
                    "Pool never exhausted before this deploy",
                ],
            ))

            hypotheses.append(Hypothesis(
                description="Pool size configured too small",
                likelihood=0.15,
                evidence_for=["Pool was at capacity"],
                evidence_against=[
                    "Same pool size worked before",
                    "Issue started with specific deploy",
                ],
            ))

        return sorted(hypotheses, key=lambda h: h.likelihood, reverse=True)


_analyzer: RootCauseAnalyzer | None = None


def get_root_cause_analyzer() -> RootCauseAnalyzer:
    """Get the default root cause analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = RootCauseAnalyzer()
    return _analyzer
