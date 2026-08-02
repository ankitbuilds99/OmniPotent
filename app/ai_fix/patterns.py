"""Code pattern matching for fix generation.

Matches codebase patterns to generate context-aware fixes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("omnipotent.ai_fix.patterns")


@dataclass
class CodePattern:
    """A code pattern in the codebase."""

    pattern_id: str
    name: str
    description: str
    pattern_type: str
    occurrences: int
    example_locations: list[str]
    code_template: str
    usage_context: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "description": self.description,
            "pattern_type": self.pattern_type,
            "occurrences": self.occurrences,
            "example_locations": self.example_locations,
            "code_template": self.code_template,
            "usage_context": self.usage_context,
        }


@dataclass
class PatternMatch:
    """A match of a pattern in the code."""

    pattern: CodePattern
    file_path: str
    line_number: int
    match_confidence: float
    context_lines: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.to_dict(),
            "file_path": self.file_path,
            "line_number": self.line_number,
            "match_confidence": round(self.match_confidence, 2),
            "context_lines": self.context_lines,
        }


class PatternMatcher:
    """Matches code patterns in the codebase."""

    def __init__(self) -> None:
        self._patterns: dict[str, CodePattern] = {}
        self._pattern_counter = 0
        self._setup_default_patterns()

    def _setup_default_patterns(self) -> None:
        """Set up default patterns."""
        patterns = [
            CodePattern(
                pattern_id="pattern_null_check",
                name="Null Check Pattern",
                description="Standard null check before attribute access",
                pattern_type="defensive",
                occurrences=47,
                example_locations=[
                    "billing/checkout.py:89",
                    "payment/processor.py:156",
                    "user/profile.py:234",
                ],
                code_template="""if {var} is None:
    raise {Exception}(
        {error_param}={error_value},
        message="{error_message}"
    )""",
                usage_context="Used before accessing attributes on optional values",
            ),
            CodePattern(
                pattern_id="pattern_exception_type",
                name="Custom Exception Pattern",
                description="Standard custom exception with context",
                pattern_type="error_handling",
                occurrences=23,
                example_locations=[
                    "exceptions.py:45",
                    "billing/exceptions.py:12",
                ],
                code_template="""class {Name}Error(AppException):
    def __init__(self, {param}: {type}, message: str):
        self.{param} = {param}
        super().__init__(message)""",
                usage_context="Used for domain-specific exceptions",
            ),
            CodePattern(
                pattern_id="pattern_retry_decorator",
                name="Retry Decorator Pattern",
                description="Standard retry with exponential backoff",
                pattern_type="resilience",
                occurrences=15,
                example_locations=[
                    "integrations/stripe.py:34",
                    "integrations/sendgrid.py:56",
                ],
                code_template="""@retry(
    max_attempts=3,
    backoff=exponential(base=2, max_delay=30),
    exceptions=({ExceptionType},),
    on_retry=lambda e, attempt: log.warning(f"Retry {attempt}: {e}")
)""",
                usage_context="Used for external service calls",
            ),
            CodePattern(
                pattern_id="pattern_context_manager",
                name="Context Manager Pattern",
                description="Resource cleanup with context manager",
                pattern_type="resource_management",
                occurrences=31,
                example_locations=[
                    "database/connection.py:78",
                    "cache/redis.py:45",
                ],
                code_template="""@contextmanager
def {name}() -> Generator[{ResourceType}, None, None]:
    resource = {acquire}()
    try:
        yield resource
    finally:
        {release}(resource)""",
                usage_context="Used for database connections, file handles",
            ),
            CodePattern(
                pattern_id="pattern_validation",
                name="Input Validation Pattern",
                description="Standard input validation at function entry",
                pattern_type="validation",
                occurrences=56,
                example_locations=[
                    "api/handlers.py:112",
                    "services/user.py:45",
                ],
                code_template="""def {function}({params}) -> {ReturnType}:
    if not {required_param}:
        raise ValidationError("{param_name} is required")

    {validation_checks}

    # actual implementation""",
                usage_context="Used at API boundaries and service entry points",
            ),
            CodePattern(
                pattern_id="pattern_logging",
                name="Structured Logging Pattern",
                description="Standard structured logging with context",
                pattern_type="observability",
                occurrences=89,
                example_locations=[
                    "billing/checkout.py:145",
                    "auth/login.py:67",
                ],
                code_template="""log.{level}(
    "{action_description}",
    extra={{
        "{context_key}": {context_value},
        "duration_ms": duration_ms,
    }},
)""",
                usage_context="Used for operation logging with context",
            ),
            CodePattern(
                pattern_id="pattern_cache_aside",
                name="Cache-Aside Pattern",
                description="Standard cache-aside with TTL",
                pattern_type="caching",
                occurrences=18,
                example_locations=[
                    "services/user.py:89",
                    "services/config.py:34",
                ],
                code_template="""def get_{entity}({key_param}: {KeyType}) -> {EntityType} | None:
    cache_key = f"{entity}:{{key_param}}"

    cached = cache.get(cache_key)
    if cached:
        return {EntityType}.from_cache(cached)

    entity = db.query({Entity}).get({key_param})
    if entity:
        cache.set(cache_key, entity.to_cache(), ttl={ttl})

    return entity""",
                usage_context="Used for frequently accessed entities",
            ),
        ]

        for pattern in patterns:
            self._patterns[pattern.pattern_id] = pattern

    def find_patterns_for_fix(
        self,
        error_type: str,
        file_path: str,
        context: str | None = None,
    ) -> list[CodePattern]:
        """Find relevant patterns for fixing an error."""
        relevant = []

        type_mapping = {
            "AttributeError": ["pattern_null_check", "pattern_validation"],
            "NoneType": ["pattern_null_check", "pattern_validation"],
            "ConnectionError": ["pattern_retry_decorator", "pattern_context_manager"],
            "TimeoutError": ["pattern_retry_decorator", "pattern_cache_aside"],
            "ValidationError": ["pattern_validation", "pattern_null_check"],
        }

        pattern_ids = type_mapping.get(error_type, [])
        for pid in pattern_ids:
            if pid in self._patterns:
                relevant.append(self._patterns[pid])

        if "billing" in file_path or "payment" in file_path:
            if "pattern_logging" in self._patterns:
                relevant.append(self._patterns["pattern_logging"])

        return relevant

    def match_pattern(
        self,
        pattern_id: str,
        code_snippet: str,
    ) -> PatternMatch | None:
        """Check if code matches a pattern."""
        pattern = self._patterns.get(pattern_id)
        if not pattern:
            return None

        indicators = {
            "pattern_null_check": ["is None", "is not None"],
            "pattern_exception_type": ["class ", "Exception", "Error"],
            "pattern_retry_decorator": ["@retry", "max_attempts"],
            "pattern_context_manager": ["@contextmanager", "yield"],
            "pattern_validation": ["ValidationError", "is required"],
            "pattern_logging": ["log.", "extra={"],
            "pattern_cache_aside": ["cache.get", "cache.set"],
        }

        pattern_indicators = indicators.get(pattern_id, [])
        matches = sum(1 for ind in pattern_indicators if ind in code_snippet)
        confidence = matches / len(pattern_indicators) if pattern_indicators else 0

        if confidence > 0.3:
            return PatternMatch(
                pattern=pattern,
                file_path="",
                line_number=0,
                match_confidence=confidence,
                context_lines=code_snippet.split("\n")[:5],
            )

        return None

    def suggest_pattern_for_context(
        self,
        file_path: str,
        function_name: str,
        error_context: str,
    ) -> CodePattern | None:
        """Suggest a pattern based on context."""
        context_lower = error_context.lower()

        if "none" in context_lower or "null" in context_lower:
            return self._patterns.get("pattern_null_check")
        elif "connection" in context_lower or "timeout" in context_lower:
            return self._patterns.get("pattern_retry_decorator")
        elif "validation" in context_lower or "invalid" in context_lower:
            return self._patterns.get("pattern_validation")
        elif "cache" in context_lower:
            return self._patterns.get("pattern_cache_aside")

        return self._patterns.get("pattern_null_check")

    def get_pattern(self, pattern_id: str) -> CodePattern | None:
        """Get a pattern by ID."""
        return self._patterns.get(pattern_id)

    def get_all_patterns(self) -> list[CodePattern]:
        """Get all patterns."""
        return list(self._patterns.values())

    def add_pattern(self, pattern: CodePattern) -> None:
        """Add a new pattern."""
        self._patterns[pattern.pattern_id] = pattern

    def get_pattern_statistics(self) -> dict[str, Any]:
        """Get statistics about patterns."""
        by_type: dict[str, int] = {}
        total_occurrences = 0

        for pattern in self._patterns.values():
            ptype = pattern.pattern_type
            by_type[ptype] = by_type.get(ptype, 0) + 1
            total_occurrences += pattern.occurrences

        return {
            "total_patterns": len(self._patterns),
            "total_occurrences": total_occurrences,
            "by_type": by_type,
            "most_common": max(
                self._patterns.values(),
                key=lambda p: p.occurrences,
            ).name if self._patterns else None,
        }


_matcher: PatternMatcher | None = None


def get_pattern_matcher() -> PatternMatcher:
    """Get the default pattern matcher."""
    global _matcher
    if _matcher is None:
        _matcher = PatternMatcher()
    return _matcher
