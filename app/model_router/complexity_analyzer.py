"""Complexity Analyzer for Model Routing.

Analyzes query complexity to determine appropriate model tier.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.model_router.complexity")


class ComplexityLevel(str, Enum):
    """Query complexity levels."""
    TRIVIAL = "trivial"      # Simple lookups, status checks
    LOW = "low"              # Basic questions, simple extraction
    MEDIUM = "medium"        # Multi-step analysis, synthesis
    HIGH = "high"            # Complex reasoning, multi-domain
    CRITICAL = "critical"    # Production incidents, high stakes


@dataclass
class ComplexityScore:
    """Detailed complexity scoring."""
    level: ComplexityLevel
    overall_score: float  # 0-1 normalized

    # Component scores (0-1 each)
    semantic_complexity: float = 0.0
    context_requirements: float = 0.0
    reasoning_depth: float = 0.0
    domain_breadth: float = 0.0
    stakes_factor: float = 0.0

    # Analysis details
    detected_patterns: list[str] = field(default_factory=list)
    requires_tools: list[str] = field(default_factory=list)
    estimated_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "overall_score": round(self.overall_score, 3),
            "components": {
                "semantic_complexity": round(self.semantic_complexity, 3),
                "context_requirements": round(self.context_requirements, 3),
                "reasoning_depth": round(self.reasoning_depth, 3),
                "domain_breadth": round(self.domain_breadth, 3),
                "stakes_factor": round(self.stakes_factor, 3),
            },
            "detected_patterns": self.detected_patterns,
            "requires_tools": self.requires_tools,
            "estimated_tokens": self.estimated_tokens,
        }


class ComplexityAnalyzer:
    """Analyzes query complexity for model routing decisions.

    Evaluates:
    - Semantic complexity (linguistic and structural)
    - Context requirements (how much context needed)
    - Reasoning depth (steps of inference)
    - Domain breadth (multi-domain knowledge)
    - Stakes factor (production impact)
    """

    # High-stakes indicators
    HIGH_STAKES_PATTERNS = [
        r"\b(production|prod)\b",
        r"\b(incident|outage|emergency)\b",
        r"\b(critical|urgent|immediately)\b",
        r"\b(customer[s]?\s+impact|user[s]?\s+affected)\b",
        r"\b(p[0-1]|sev[0-1]|severity\s*[0-1])\b",
        r"\b(revenue|sla|breach)\b",
        r"\b(rollback|revert|emergency\s+fix)\b",
    ]

    # Complex reasoning indicators
    COMPLEX_REASONING_PATTERNS = [
        r"\bwhy\b.*\bwhat\b|\bwhat\b.*\bwhy\b",
        r"\broot\s+cause\b",
        r"\bcorrelat(e|ion)\b",
        r"\bimpact\s+analysis\b",
        r"\bdependenc(y|ies)\b.*\b(trace|map|analyze)\b",
        r"\bcompare\b.*\band\b.*\bdecide\b",
        r"\boptimiz(e|ation)\b",
        r"\btrade-?off\b",
        r"\barchitect(ure)?\s+(decision|review)\b",
    ]

    # Multi-step task indicators
    MULTI_STEP_PATTERNS = [
        r"\b(then|after|next|finally)\b",
        r"\b(step[s]?|phase[s]?|stage[s]?)\b",
        r"\bfirst\b.*\bthen\b",
        r"\b(debug|troubleshoot|investigate)\b",
        r"\b(implement|create|build)\b.*\band\b.*\b(test|verify)\b",
    ]

    # Domain breadth indicators
    MULTI_DOMAIN_PATTERNS = [
        r"\b(kubernetes|k8s)\b.*\b(database|db|postgres)\b",
        r"\b(network|dns)\b.*\b(service|pod)\b",
        r"\b(metrics|monitor)\b.*\b(logs|trace)\b",
        r"\b(security|auth)\b.*\b(performance|latency)\b",
        r"\b(frontend|backend|fullstack)\b",
        r"\b(infrastructure|infra)\b.*\b(application|app)\b",
    ]

    # Tool requirement indicators
    TOOL_INDICATORS = {
        "kubernetes": [r"\bkubectl\b", r"\bk8s\b", r"\bpod[s]?\b", r"\bdeployment[s]?\b"],
        "database": [r"\b(sql|query|database|db|postgres|mysql)\b"],
        "monitoring": [r"\b(prometheus|grafana|metric[s]?|alert[s]?)\b"],
        "logs": [r"\b(log[s]?|loki|elasticsearch|kibana)\b"],
        "git": [r"\b(git|commit|branch|merge|pr|pull\s+request)\b"],
        "documentation": [r"\b(doc[s]?|runbook|playbook|wiki)\b"],
    }

    # Simple query indicators
    SIMPLE_PATTERNS = [
        r"^(what|which|how|who|where|when)\s+(is|are|was|were)\s+\w+\??\s*$",
        r"^(show|list|get|display)\s+\w+\s*$",
        r"^(status|health|check)\s+(of\s+)?\w+\s*$",
    ]

    def __init__(self):
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for efficiency."""
        self._high_stakes_re = [
            re.compile(p, re.IGNORECASE) for p in self.HIGH_STAKES_PATTERNS
        ]
        self._complex_reasoning_re = [
            re.compile(p, re.IGNORECASE) for p in self.COMPLEX_REASONING_PATTERNS
        ]
        self._multi_step_re = [
            re.compile(p, re.IGNORECASE) for p in self.MULTI_STEP_PATTERNS
        ]
        self._multi_domain_re = [
            re.compile(p, re.IGNORECASE) for p in self.MULTI_DOMAIN_PATTERNS
        ]
        self._simple_re = [
            re.compile(p, re.IGNORECASE) for p in self.SIMPLE_PATTERNS
        ]
        self._tool_indicators_re = {
            tool: [re.compile(p, re.IGNORECASE) for p in patterns]
            for tool, patterns in self.TOOL_INDICATORS.items()
        }

    def analyze(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        incident_severity: str | None = None,
    ) -> ComplexityScore:
        """Analyze query complexity.

        Args:
            query: The user query to analyze
            context: Optional context (alert data, service info, etc.)
            incident_severity: If part of an incident (P0-P4)

        Returns:
            ComplexityScore with detailed breakdown
        """
        detected_patterns: list[str] = []
        requires_tools: list[str] = []

        # Check for simple queries first
        is_simple = any(p.match(query) for p in self._simple_re)
        if is_simple:
            return ComplexityScore(
                level=ComplexityLevel.TRIVIAL,
                overall_score=0.1,
                semantic_complexity=0.1,
                context_requirements=0.1,
                reasoning_depth=0.1,
                domain_breadth=0.1,
                stakes_factor=0.0,
                detected_patterns=["simple_query"],
                estimated_tokens=len(query.split()) * 2,
            )

        # Calculate semantic complexity
        semantic_score = self._score_semantic_complexity(query)

        # Calculate context requirements
        context_score = self._score_context_requirements(query, context)

        # Calculate reasoning depth
        reasoning_score = 0.0
        for pattern in self._complex_reasoning_re:
            if pattern.search(query):
                reasoning_score += 0.15
                detected_patterns.append(f"complex_reasoning:{pattern.pattern}")
        for pattern in self._multi_step_re:
            if pattern.search(query):
                reasoning_score += 0.1
                detected_patterns.append(f"multi_step:{pattern.pattern}")
        reasoning_score = min(1.0, reasoning_score)

        # Calculate domain breadth
        domain_score = 0.0
        for pattern in self._multi_domain_re:
            if pattern.search(query):
                domain_score += 0.2
                detected_patterns.append(f"multi_domain:{pattern.pattern}")
        domain_score = min(1.0, domain_score)

        # Calculate stakes factor
        stakes_score = 0.0
        for pattern in self._high_stakes_re:
            if pattern.search(query):
                stakes_score += 0.15
                detected_patterns.append(f"high_stakes:{pattern.pattern}")
        if incident_severity:
            sev = incident_severity.lower()
            if sev in ("p0", "sev0", "critical"):
                stakes_score += 0.5
            elif sev in ("p1", "sev1", "major"):
                stakes_score += 0.3
            elif sev in ("p2", "sev2", "minor"):
                stakes_score += 0.1
        stakes_score = min(1.0, stakes_score)

        # Detect tool requirements
        for tool, patterns in self._tool_indicators_re.items():
            if any(p.search(query) for p in patterns):
                requires_tools.append(tool)

        # Calculate overall score (weighted average)
        overall = (
            semantic_score * 0.15
            + context_score * 0.2
            + reasoning_score * 0.25
            + domain_score * 0.15
            + stakes_score * 0.25
        )

        # Determine level
        level = self._score_to_level(overall, stakes_score)

        # Estimate token usage
        estimated_tokens = self._estimate_tokens(query, context, requires_tools)

        return ComplexityScore(
            level=level,
            overall_score=overall,
            semantic_complexity=semantic_score,
            context_requirements=context_score,
            reasoning_depth=reasoning_score,
            domain_breadth=domain_score,
            stakes_factor=stakes_score,
            detected_patterns=detected_patterns,
            requires_tools=requires_tools,
            estimated_tokens=estimated_tokens,
        )

    def _score_semantic_complexity(self, query: str) -> float:
        """Score the semantic complexity of the query."""
        score = 0.0

        # Word count
        words = query.split()
        if len(words) > 50:
            score += 0.3
        elif len(words) > 20:
            score += 0.15

        # Sentence count
        sentences = re.split(r"[.!?]", query)
        if len(sentences) > 3:
            score += 0.2

        # Question complexity
        question_words = len(re.findall(r"\b(what|why|how|when|where|which|who)\b", query, re.I))
        if question_words > 2:
            score += 0.25
        elif question_words > 0:
            score += 0.1

        # Conditional language
        if re.search(r"\b(if|when|unless|although|however|but)\b", query, re.I):
            score += 0.1

        # Technical terms density
        tech_terms = len(re.findall(
            r"\b(api|service|pod|container|deployment|database|query|"
            r"metric|alert|log|trace|latency|throughput|error|exception)\b",
            query, re.I
        ))
        if tech_terms > 5:
            score += 0.2
        elif tech_terms > 2:
            score += 0.1

        return min(1.0, score)

    def _score_context_requirements(
        self,
        query: str,
        context: dict[str, Any] | None,
    ) -> float:
        """Score the context requirements of the query."""
        score = 0.0

        # References to specific entities
        if re.search(r"\b(this|that|these|those)\s+\w+", query, re.I):
            score += 0.2

        # Named entities
        named_entities = len(re.findall(r"\b[A-Z][a-z]+(?:-[A-Z][a-z]+)*\b", query))
        if named_entities > 3:
            score += 0.2
        elif named_entities > 0:
            score += 0.1

        # Time references
        if re.search(r"\b(yesterday|today|last\s+\w+|recent|past\s+\w+)\b", query, re.I):
            score += 0.15

        # Historical context needed
        if re.search(r"\b(history|previous|before|earlier|changed|difference)\b", query, re.I):
            score += 0.2

        # Context provided
        if context:
            if "alert_data" in context:
                score += 0.15
            if "service_context" in context:
                score += 0.1
            if "incident_history" in context:
                score += 0.2

        return min(1.0, score)

    def _score_to_level(self, overall: float, stakes: float) -> ComplexityLevel:
        """Convert overall score to complexity level."""
        # Stakes override: high stakes always bumps up.
        #
        # These comparisons are inclusive because the thresholds are exactly
        # the numbers the severity contributes: a declared P0 adds 0.50 and a
        # P1 adds 0.30, so `stakes > 0.5` and `stakes > 0.3` both missed the
        # case they were written for unless the query text happened to also
        # match a high-stakes keyword. A P0 with no such keyword scored
        # MEDIUM, which COMPLEXITY_TIER_MAP routes to the *balanced* model
        # instead of premium; a P1 scored TRIVIAL — indistinguishable from
        # passing no severity at all — and went to the *fast* one. The worst
        # incidents got the cheapest models.
        if stakes >= 0.5:
            return ComplexityLevel.CRITICAL
        if stakes >= 0.3:
            if overall > 0.4:
                return ComplexityLevel.HIGH
            return ComplexityLevel.MEDIUM

        # Score-based levels
        if overall >= 0.7:
            return ComplexityLevel.HIGH
        if overall >= 0.4:
            return ComplexityLevel.MEDIUM
        if overall >= 0.2:
            return ComplexityLevel.LOW
        return ComplexityLevel.TRIVIAL

    def _estimate_tokens(
        self,
        query: str,
        context: dict[str, Any] | None,
        tools: list[str],
    ) -> int:
        """Estimate token usage for the query."""
        # Base estimate from query
        estimate = len(query.split()) * 2

        # Context tokens
        if context:
            estimate += len(str(context)) // 4

        # Tool overhead
        estimate += len(tools) * 100

        # Response estimate (varies by complexity)
        estimate *= 3  # Rough multiplier for response

        return estimate
