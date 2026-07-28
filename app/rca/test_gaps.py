"""Test gap detection for root cause analysis.

Identifies missing tests that would have caught bugs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("devcopilot.rca.test_gaps")


class GapSeverity(Enum):
    """Severity of a test gap."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GapType(Enum):
    """Type of test gap."""

    NULL_CHECK = "null_check"
    EDGE_CASE = "edge_case"
    ERROR_HANDLING = "error_handling"
    BOUNDARY = "boundary"
    CONCURRENCY = "concurrency"
    INTEGRATION = "integration"
    PERFORMANCE = "performance"
    SECURITY = "security"


@dataclass
class TestGap:
    """A detected gap in test coverage."""

    gap_id: str
    gap_type: GapType
    severity: GapSeverity
    file_path: str
    line_number: int
    function_name: str
    description: str
    scenario: str
    would_have_caught: str
    suggested_test: str
    test_generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "gap_type": self.gap_type.value,
            "severity": self.severity.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "function_name": self.function_name,
            "description": self.description,
            "scenario": self.scenario,
            "would_have_caught": self.would_have_caught,
            "suggested_test": self.suggested_test,
            "test_generated": self.test_generated,
        }


@dataclass
class LineCoverage:
    """Coverage information for a line."""

    line_number: int
    is_covered: bool
    hit_count: int = 0
    branch_coverage: float | None = None


@dataclass
class TestCoverage:
    """Test coverage for a file."""

    file_path: str
    total_lines: int
    covered_lines: int
    coverage_percentage: float
    line_coverage: list[LineCoverage]
    uncovered_functions: list[str]
    gaps: list[TestGap]
    analyzed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "total_lines": self.total_lines,
            "covered_lines": self.covered_lines,
            "coverage_percentage": round(self.coverage_percentage, 1),
            "uncovered_functions": self.uncovered_functions,
            "gaps": [g.to_dict() for g in self.gaps],
            "analyzed_at": self.analyzed_at.isoformat(),
        }


class TestGapDetector:
    """Detects gaps in test coverage that lead to bugs."""

    def __init__(self) -> None:
        self._gaps: dict[str, TestGap] = {}
        self._coverage: dict[str, TestCoverage] = {}
        self._gap_counter = 0
        self._gap_patterns = self._setup_patterns()

    def _setup_patterns(self) -> dict[str, dict[str, Any]]:
        """Set up patterns that indicate test gaps."""
        return {
            "null_check": {
                "type": GapType.NULL_CHECK,
                "severity": GapSeverity.CRITICAL,
                "indicators": ["is None", "is not None", ".get(", "or None"],
                "test_template": """
def test_{function}_with_none():
    \"\"\"Test {function} when input is None.\"\"\"
    with pytest.raises({exception}):
        {function}(None)
""",
            },
            "empty_collection": {
                "type": GapType.EDGE_CASE,
                "severity": GapSeverity.HIGH,
                "indicators": ["for ", "len(", "if not ", "[]", "{}"],
                "test_template": """
def test_{function}_with_empty():
    \"\"\"Test {function} with empty input.\"\"\"
    result = {function}([])
    assert result == {expected}
""",
            },
            "boundary": {
                "type": GapType.BOUNDARY,
                "severity": GapSeverity.HIGH,
                "indicators": ["> 0", "< 0", ">= ", "<= ", "== 0"],
                "test_template": """
def test_{function}_boundary_zero():
    \"\"\"Test {function} at boundary value.\"\"\"
    result = {function}(0)
    assert result == {expected}
""",
            },
            "exception_handling": {
                "type": GapType.ERROR_HANDLING,
                "severity": GapSeverity.MEDIUM,
                "indicators": ["try:", "except ", "raise "],
                "test_template": """
def test_{function}_exception_handling():
    \"\"\"Test {function} exception handling.\"\"\"
    with pytest.raises({exception}):
        {function}({invalid_input})
""",
            },
            "concurrent_access": {
                "type": GapType.CONCURRENCY,
                "severity": GapSeverity.HIGH,
                "indicators": ["Lock(", "async ", "await ", "threading", "asyncio"],
                "test_template": """
@pytest.mark.asyncio
async def test_{function}_concurrent():
    \"\"\"Test {function} under concurrent access.\"\"\"
    tasks = [asyncio.create_task({function}()) for _ in range(10)]
    results = await asyncio.gather(*tasks)
    assert all(r is not None for r in results)
""",
            },
        }

    def analyze_for_incident(
        self,
        file_path: str,
        function_name: str,
        error_type: str,
        error_message: str,
        line_number: int,
    ) -> list[TestGap]:
        """Analyze code for test gaps based on an incident."""
        gaps = []

        if "none" in error_message.lower() or "null" in error_type.lower():
            self._gap_counter += 1
            gap = TestGap(
                gap_id=f"gap_{self._gap_counter}",
                gap_type=GapType.NULL_CHECK,
                severity=GapSeverity.CRITICAL,
                file_path=file_path,
                line_number=line_number,
                function_name=function_name,
                description=f"No test for {function_name} with None input",
                scenario=f"{function_name} = None",
                would_have_caught=f"This {error_type} when input is None",
                suggested_test=self._generate_null_test(function_name),
            )
            gaps.append(gap)
            self._gaps[gap.gap_id] = gap

        if "index" in error_message.lower() or "empty" in error_message.lower():
            self._gap_counter += 1
            gap = TestGap(
                gap_id=f"gap_{self._gap_counter}",
                gap_type=GapType.EDGE_CASE,
                severity=GapSeverity.HIGH,
                file_path=file_path,
                line_number=line_number,
                function_name=function_name,
                description=f"No test for {function_name} with empty collection",
                scenario=f"{function_name}([])",
                would_have_caught=f"This {error_type} with empty input",
                suggested_test=self._generate_empty_test(function_name),
            )
            gaps.append(gap)
            self._gaps[gap.gap_id] = gap

        self._gap_counter += 1
        gap = TestGap(
            gap_id=f"gap_{self._gap_counter}",
            gap_type=GapType.ERROR_HANDLING,
            severity=GapSeverity.MEDIUM,
            file_path=file_path,
            line_number=line_number,
            function_name=function_name,
            description=f"No test for {function_name} error path",
            scenario=f"{function_name} raises {error_type}",
            would_have_caught=f"The {error_type} error handling",
            suggested_test=self._generate_error_test(function_name, error_type),
        )
        gaps.append(gap)
        self._gaps[gap.gap_id] = gap

        return gaps

    def _generate_null_test(self, function_name: str) -> str:
        """Generate a test for null input."""
        return f"""def test_{function_name}_with_none():
    \"\"\"Test {function_name} when input is None.\"\"\"
    user = create_user(payment_method=None)
    with pytest.raises(PaymentMethodRequired):
        {function_name}(user.id, Decimal("10.00"))"""

    def _generate_empty_test(self, function_name: str) -> str:
        """Generate a test for empty input."""
        return f"""def test_{function_name}_with_empty():
    \"\"\"Test {function_name} with empty collection.\"\"\"
    result = {function_name}([])
    assert result == []  # or appropriate empty result"""

    def _generate_error_test(
        self,
        function_name: str,
        error_type: str,
    ) -> str:
        """Generate a test for error handling."""
        return f"""def test_{function_name}_error_handling():
    \"\"\"Test {function_name} error handling.\"\"\"
    with pytest.raises({error_type}):
        {function_name}(invalid_input)"""

    def analyze_coverage(
        self,
        file_path: str,
        source_lines: list[str],
        covered_lines: set[int],
    ) -> TestCoverage:
        """Analyze test coverage for a file."""
        total_lines = len(source_lines)
        covered_count = len(covered_lines)

        line_coverage = []
        for i, _line in enumerate(source_lines, 1):
            is_covered = i in covered_lines
            line_coverage.append(
                LineCoverage(
                    line_number=i,
                    is_covered=is_covered,
                    hit_count=1 if is_covered else 0,
                )
            )

        uncovered_functions = []
        for i, line in enumerate(source_lines, 1):
            if line.strip().startswith("def ") and i not in covered_lines:
                func_name = line.split("def ")[1].split("(")[0]
                uncovered_functions.append(func_name)

        gaps = self._detect_coverage_gaps(file_path, source_lines, covered_lines)

        coverage = TestCoverage(
            file_path=file_path,
            total_lines=total_lines,
            covered_lines=covered_count,
            coverage_percentage=(covered_count / total_lines * 100) if total_lines > 0 else 0,
            line_coverage=line_coverage,
            uncovered_functions=uncovered_functions,
            gaps=gaps,
            analyzed_at=datetime.now(timezone.utc),
        )

        self._coverage[file_path] = coverage
        return coverage

    def _detect_coverage_gaps(
        self,
        file_path: str,
        source_lines: list[str],
        covered_lines: set[int],
    ) -> list[TestGap]:
        """Detect test gaps from coverage analysis."""
        gaps = []
        current_function = None

        for i, line in enumerate(source_lines, 1):
            if line.strip().startswith("def "):
                current_function = line.split("def ")[1].split("(")[0]
                continue

            if i not in covered_lines and current_function:
                line_lower = line.lower()

                for pattern_name, pattern in self._gap_patterns.items():
                    for indicator in pattern["indicators"]:
                        if indicator.lower() in line_lower:
                            self._gap_counter += 1
                            gap = TestGap(
                                gap_id=f"gap_{self._gap_counter}",
                                gap_type=pattern["type"],
                                severity=pattern["severity"],
                                file_path=file_path,
                                line_number=i,
                                function_name=current_function,
                                description=f"Uncovered {pattern_name} in {current_function}",
                                scenario=f"Line {i}: {line.strip()[:50]}",
                                would_have_caught="Potential bug in this code path",
                                suggested_test=pattern["test_template"].format(
                                    function=current_function,
                                    exception="Exception",
                                    expected="expected_value",
                                    invalid_input="invalid_input",
                                ),
                            )
                            gaps.append(gap)
                            self._gaps[gap.gap_id] = gap
                            break

        return gaps

    def generate_missing_tests(
        self,
        gaps: list[TestGap],
    ) -> str:
        """Generate test code for all gaps."""
        tests = [
            "import pytest",
            "from decimal import Decimal",
            "",
            "",
        ]

        for gap in gaps:
            tests.append(f"# Test for gap: {gap.description}")
            tests.append(gap.suggested_test)
            tests.append("")
            gap.test_generated = True

        return "\n".join(tests)

    def get_gap(self, gap_id: str) -> TestGap | None:
        """Get a gap by ID."""
        return self._gaps.get(gap_id)

    def get_gaps_for_file(self, file_path: str) -> list[TestGap]:
        """Get all gaps for a file."""
        return [g for g in self._gaps.values() if g.file_path == file_path]

    def get_coverage(self, file_path: str) -> TestCoverage | None:
        """Get coverage for a file."""
        return self._coverage.get(file_path)

    def get_critical_gaps(self) -> list[TestGap]:
        """Get all critical severity gaps."""
        return [
            g for g in self._gaps.values()
            if g.severity == GapSeverity.CRITICAL
        ]

    def get_summary(self) -> dict[str, Any]:
        """Get a summary of all test gaps."""
        by_severity: dict[str, int] = {}
        by_type: dict[str, int] = {}

        for gap in self._gaps.values():
            sev = gap.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1

            typ = gap.gap_type.value
            by_type[typ] = by_type.get(typ, 0) + 1

        return {
            "total_gaps": len(self._gaps),
            "by_severity": by_severity,
            "by_type": by_type,
            "generated_tests": sum(
                1 for g in self._gaps.values() if g.test_generated
            ),
        }


_detector: TestGapDetector | None = None


def get_test_gap_detector() -> TestGapDetector:
    """Get the default test gap detector."""
    global _detector
    if _detector is None:
        _detector = TestGapDetector()
    return _detector
