"""Task Classifier - Analyzes task complexity for routing decisions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.router.classifier")


class TaskComplexity(str, Enum):
    """Complexity levels for tasks."""
    TRIVIAL = "trivial"      # Simple queries, status checks
    SIMPLE = "simple"        # Single-step operations
    MODERATE = "moderate"    # Multi-step with clear path
    COMPLEX = "complex"      # Multi-step, requires reasoning
    EXPERT = "expert"        # Novel situations, creative solutions


class TaskType(str, Enum):
    """Types of tasks the agent handles."""
    STATUS_CHECK = "status_check"
    LOG_ANALYSIS = "log_analysis"
    METRIC_QUERY = "metric_query"
    ROOT_CAUSE = "root_cause"
    REMEDIATION = "remediation"
    CODE_REVIEW = "code_review"
    DEPLOYMENT = "deployment"
    SYNTHESIS = "synthesis"
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    """Result of task classification."""
    complexity: TaskComplexity
    task_type: TaskType
    confidence: float
    reasoning: str
    features: dict[str, Any]


class TaskClassifier:
    """Classifies tasks by complexity and type for routing decisions.

    Uses heuristics and patterns to estimate task complexity:
    - Keyword analysis
    - Query structure
    - Tool requirements
    - Context size
    """

    # Keywords indicating complexity levels
    COMPLEXITY_KEYWORDS = {
        TaskComplexity.TRIVIAL: [
            "status", "health", "ping", "version", "uptime",
        ],
        TaskComplexity.SIMPLE: [
            "show", "list", "get", "fetch", "count", "latest",
        ],
        TaskComplexity.MODERATE: [
            "analyze", "compare", "find", "search", "filter",
            "last hour", "recent", "trend",
        ],
        TaskComplexity.COMPLEX: [
            "why", "root cause", "investigate", "debug", "correlate",
            "regression", "impact", "blast radius",
        ],
        TaskComplexity.EXPERT: [
            "fix", "remediate", "resolve", "prevent", "architect",
            "redesign", "optimize", "postmortem",
        ],
    }

    # Keywords indicating task types
    TYPE_KEYWORDS = {
        TaskType.STATUS_CHECK: ["status", "health", "running", "alive", "ready"],
        TaskType.LOG_ANALYSIS: ["log", "logs", "error", "exception", "trace", "stack"],
        TaskType.METRIC_QUERY: ["metric", "latency", "throughput", "rate", "p99", "p95"],
        TaskType.ROOT_CAUSE: ["why", "cause", "reason", "root cause", "investigate"],
        TaskType.REMEDIATION: ["fix", "resolve", "rollback", "restart", "scale"],
        TaskType.CODE_REVIEW: ["code", "commit", "diff", "change", "pr", "merge"],
        TaskType.DEPLOYMENT: ["deploy", "release", "rollout", "canary", "version"],
        TaskType.SYNTHESIS: ["summary", "report", "postmortem", "timeline", "recap"],
    }

    def __init__(self):
        """Initialize the classifier."""
        # Compile patterns for efficiency
        self._complexity_patterns = {
            level: [re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in keywords]
            for level, keywords in self.COMPLEXITY_KEYWORDS.items()
        }
        self._type_patterns = {
            task_type: [re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in keywords]
            for task_type, keywords in self.TYPE_KEYWORDS.items()
        }

    def classify(
        self,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        """Classify a task query.

        Args:
            query: The user's query/task
            context: Optional context (conversation history, etc.)

        Returns:
            ClassificationResult with complexity and type
        """
        features = self._extract_features(query, context)

        # Determine complexity
        complexity, complexity_score = self._classify_complexity(query, features)

        # Determine type
        task_type, type_score = self._classify_type(query, features)

        # Calculate overall confidence
        confidence = (complexity_score + type_score) / 2

        reasoning = self._generate_reasoning(query, features, complexity, task_type)

        return ClassificationResult(
            complexity=complexity,
            task_type=task_type,
            confidence=confidence,
            reasoning=reasoning,
            features=features,
        )

    def _extract_features(
        self,
        query: str,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Extract features from the query for classification."""
        features = {
            "query_length": len(query),
            "word_count": len(query.split()),
            "has_question_mark": "?" in query,
            "has_numbers": bool(re.search(r"\d+", query)),
            "has_time_reference": bool(re.search(r"(hour|minute|day|week|ago|since|between)", query, re.IGNORECASE)),
            "has_service_name": bool(re.search(r"(service|server|pod|container|deployment)", query, re.IGNORECASE)),
            "sentence_count": query.count(".") + query.count("?") + query.count("!") + 1,
        }

        # Add context features
        if context:
            features["has_prior_context"] = bool(context.get("messages"))
            features["context_turns"] = len(context.get("messages", []))
            features["has_incident"] = bool(context.get("incident"))

        return features

    def _classify_complexity(
        self,
        query: str,
        features: dict[str, Any],
    ) -> tuple[TaskComplexity, float]:
        """Classify the complexity of a task."""
        scores: dict[TaskComplexity, float] = {level: 0.0 for level in TaskComplexity}

        # Keyword matching
        for level, patterns in self._complexity_patterns.items():
            for pattern in patterns:
                if pattern.search(query):
                    scores[level] += 1.0

        # Feature-based adjustments
        if features["query_length"] < 20:
            scores[TaskComplexity.TRIVIAL] += 0.5
        elif features["query_length"] > 200:
            scores[TaskComplexity.COMPLEX] += 0.5

        if features["word_count"] > 30:
            scores[TaskComplexity.COMPLEX] += 0.5

        if features["sentence_count"] > 3:
            scores[TaskComplexity.MODERATE] += 0.3

        if features.get("has_prior_context"):
            scores[TaskComplexity.MODERATE] += 0.3

        # Find best match
        best_level = max(scores, key=lambda k: scores[k])
        total = sum(scores.values()) or 1
        confidence = scores[best_level] / total

        # Default to moderate if no strong signal
        if max(scores.values()) < 0.5:
            return TaskComplexity.MODERATE, 0.5

        return best_level, min(0.95, confidence)

    def _classify_type(
        self,
        query: str,
        features: dict[str, Any],
    ) -> tuple[TaskType, float]:
        """Classify the type of task."""
        scores: dict[TaskType, float] = {t: 0.0 for t in TaskType}

        # Keyword matching
        for task_type, patterns in self._type_patterns.items():
            for pattern in patterns:
                if pattern.search(query):
                    scores[task_type] += 1.0

        # Feature-based adjustments
        if features["has_question_mark"]:
            scores[TaskType.ROOT_CAUSE] += 0.3
            scores[TaskType.STATUS_CHECK] += 0.2

        if features.get("has_incident"):
            scores[TaskType.ROOT_CAUSE] += 0.5
            scores[TaskType.REMEDIATION] += 0.3

        # Find best match
        best_type = max(scores, key=lambda k: scores[k])
        total = sum(scores.values()) or 1
        confidence = scores[best_type] / total

        # Default to unknown if no strong signal
        if max(scores.values()) < 0.3:
            return TaskType.UNKNOWN, 0.3

        return best_type, min(0.95, confidence)

    def _generate_reasoning(
        self,
        query: str,
        features: dict[str, Any],
        complexity: TaskComplexity,
        task_type: TaskType,
    ) -> str:
        """Generate human-readable reasoning for the classification."""
        reasons = []

        # Complexity reasoning
        if complexity == TaskComplexity.TRIVIAL:
            reasons.append("Simple status/health check query")
        elif complexity == TaskComplexity.SIMPLE:
            reasons.append("Single-step data retrieval")
        elif complexity == TaskComplexity.MODERATE:
            reasons.append("Multi-step analysis required")
        elif complexity == TaskComplexity.COMPLEX:
            reasons.append("Requires deep investigation and correlation")
        else:
            reasons.append("Expert-level problem solving needed")

        # Type reasoning
        type_descriptions = {
            TaskType.STATUS_CHECK: "checking service status",
            TaskType.LOG_ANALYSIS: "analyzing logs",
            TaskType.METRIC_QUERY: "querying metrics",
            TaskType.ROOT_CAUSE: "finding root cause",
            TaskType.REMEDIATION: "performing remediation",
            TaskType.CODE_REVIEW: "reviewing code changes",
            TaskType.DEPLOYMENT: "deployment operation",
            TaskType.SYNTHESIS: "synthesizing information",
        }
        if task_type in type_descriptions:
            reasons.append(f"Task involves {type_descriptions[task_type]}")

        # Feature-based reasoning
        if features["word_count"] > 30:
            reasons.append("Long query suggests complex requirements")
        if features.get("has_prior_context"):
            reasons.append("Building on prior conversation context")

        return "; ".join(reasons)

    def estimate_tokens(self, query: str, complexity: TaskComplexity) -> int:
        """Estimate token usage for a task.

        Args:
            query: The query
            complexity: Classified complexity

        Returns:
            Estimated token count
        """
        base_tokens = len(query) // 4  # Rough estimate

        multipliers = {
            TaskComplexity.TRIVIAL: 2,
            TaskComplexity.SIMPLE: 4,
            TaskComplexity.MODERATE: 8,
            TaskComplexity.COMPLEX: 15,
            TaskComplexity.EXPERT: 25,
        }

        return base_tokens * multipliers.get(complexity, 10)
