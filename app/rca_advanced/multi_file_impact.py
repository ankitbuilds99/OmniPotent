"""Multi-File Impact Analysis.

Analyzes how a change in one file affects other files in the codebase.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.rca_advanced.multi_file_impact")


class ImpactType(str, Enum):
    BROKEN = "broken"
    POTENTIALLY_BROKEN = "potentially_broken"
    SAFE = "safe"
    UNKNOWN = "unknown"


class RelationshipType(str, Enum):
    IMPORTS = "imports"
    IMPORTED_BY = "imported_by"
    CALLS = "calls"
    CALLED_BY = "called_by"
    INHERITS = "inherits"
    INHERITED_BY = "inherited_by"
    USES_TYPE = "uses_type"
    TYPE_USED_BY = "type_used_by"
    TEST_FOR = "test_for"
    TESTED_BY = "tested_by"


@dataclass
class FileImpact:
    """Impact assessment for a single file."""

    file_path: str
    impact_type: ImpactType
    relationship: RelationshipType
    source_file: str
    affected_lines: list[int]
    reason: str
    confidence: float
    test_coverage: float
    risk_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "impact": self.impact_type.value,
            "relationship": self.relationship.value,
            "source": self.source_file,
            "lines": self.affected_lines[:10],
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
            "test_coverage": round(self.test_coverage, 2),
            "risk_score": round(self.risk_score, 1),
        }


@dataclass
class ImpactChain:
    """A chain of impacts through the codebase."""

    chain_id: str
    root_file: str
    root_change: str
    impacts: list[FileImpact]
    depth: int
    total_files_affected: int
    broken_files: int
    potentially_broken: int
    safe_files: int
    overall_risk: str
    ai_summary: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "root": {
                "file": self.root_file,
                "change": self.root_change,
            },
            "impacts": [i.to_dict() for i in self.impacts],
            "depth": self.depth,
            "summary": {
                "total_files": self.total_files_affected,
                "broken": self.broken_files,
                "potentially_broken": self.potentially_broken,
                "safe": self.safe_files,
            },
            "overall_risk": self.overall_risk,
            "ai_summary": self.ai_summary,
            "created_at": self.created_at.isoformat(),
        }


class MultiFileAnalyzer:
    """Analyzes impact of changes across multiple files."""

    def __init__(self) -> None:
        self._file_dependencies: dict[str, dict[str, set[str]]] = {}
        self._file_contents: dict[str, str] = {}
        self._test_mappings: dict[str, list[str]] = {}
        self._analyses: dict[str, ImpactChain] = {}

    def register_file(
        self,
        org_id: str,
        file_path: str,
        content: str,
    ) -> None:
        """Register a file's content for analysis."""
        key = f"{org_id}:{file_path}"
        self._file_contents[key] = content

        self._extract_dependencies(org_id, file_path, content)

    def _extract_dependencies(
        self,
        org_id: str,
        file_path: str,
        content: str,
    ) -> None:
        """Extract dependencies from file content."""
        key = f"{org_id}:{file_path}"

        if key not in self._file_dependencies:
            self._file_dependencies[key] = {
                "imports": set(),
                "calls": set(),
                "inherits": set(),
            }

        import_patterns = [
            r"from\s+([\w.]+)\s+import",
            r"import\s+([\w.]+)",
            r"require\s*\(['\"](.+?)['\"]\)",
            r"from\s+['\"](.+?)['\"]",
        ]

        for pattern in import_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                self._file_dependencies[key]["imports"].add(match)

        class_patterns = [
            r"class\s+\w+\s*\(\s*(\w+)\s*\)",
            r"extends\s+(\w+)",
        ]

        for pattern in class_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                self._file_dependencies[key]["inherits"].add(match)

    def register_test_mapping(
        self,
        org_id: str,
        source_file: str,
        test_file: str,
    ) -> None:
        """Register a test file for a source file."""
        key = f"{org_id}:{source_file}"
        if key not in self._test_mappings:
            self._test_mappings[key] = []
        self._test_mappings[key].append(test_file)

    def analyze_impact(
        self,
        org_id: str,
        changed_file: str,
        change_description: str,
        changed_symbols: list[str] | None = None,
    ) -> ImpactChain:
        """Analyze the impact of a file change across the codebase."""
        chain_id = f"impact_{org_id}_{datetime.now(timezone.utc).timestamp()}"

        impacts: list[FileImpact] = []

        direct_impacts = self._find_direct_impacts(
            org_id,
            changed_file,
            changed_symbols or [],
        )
        impacts.extend(direct_impacts)

        indirect_impacts = self._find_indirect_impacts(
            org_id,
            [i.file_path for i in direct_impacts],
        )
        impacts.extend(indirect_impacts)

        test_impacts = self._find_test_impacts(org_id, changed_file)
        impacts.extend(test_impacts)

        broken = sum(1 for i in impacts if i.impact_type == ImpactType.BROKEN)
        potentially_broken = sum(
            1 for i in impacts if i.impact_type == ImpactType.POTENTIALLY_BROKEN
        )
        safe = sum(1 for i in impacts if i.impact_type == ImpactType.SAFE)

        if broken > 0:
            overall_risk = "critical"
        elif potentially_broken > 2:
            overall_risk = "high"
        elif potentially_broken > 0:
            overall_risk = "medium"
        else:
            overall_risk = "low"

        max_depth = 0
        for impact in impacts:
            depth = 1
            if impact.relationship in (
                RelationshipType.CALLED_BY,
                RelationshipType.IMPORTED_BY,
            ):
                depth = 2
            max_depth = max(max_depth, depth)

        summary = self._generate_summary(
            changed_file,
            change_description,
            impacts,
        )

        chain = ImpactChain(
            chain_id=chain_id,
            root_file=changed_file,
            root_change=change_description,
            impacts=impacts,
            depth=max_depth,
            total_files_affected=len(impacts),
            broken_files=broken,
            potentially_broken=potentially_broken,
            safe_files=safe,
            overall_risk=overall_risk,
            ai_summary=summary,
        )

        self._analyses[chain_id] = chain

        log.info(
            "Analyzed impact: file=%s affected=%d broken=%d risk=%s",
            changed_file,
            len(impacts),
            broken,
            overall_risk,
        )

        return chain

    def _find_direct_impacts(
        self,
        org_id: str,
        changed_file: str,
        changed_symbols: list[str],
    ) -> list[FileImpact]:
        """Find files directly impacted by the change."""
        impacts: list[FileImpact] = []

        module_name = self._file_to_module(changed_file)

        for key, deps in self._file_dependencies.items():
            if not key.startswith(f"{org_id}:"):
                continue

            file_path = key.split(":")[1]
            if file_path == changed_file:
                continue

            if module_name in deps.get("imports", set()):
                impact_type = ImpactType.POTENTIALLY_BROKEN
                reason = f"Imports {module_name} which was changed"

                if changed_symbols:
                    content = self._file_contents.get(key, "")
                    for symbol in changed_symbols:
                        if symbol in content:
                            impact_type = ImpactType.BROKEN
                            reason = f"Uses {symbol} which was modified"
                            break

                impacts.append(FileImpact(
                    file_path=file_path,
                    impact_type=impact_type,
                    relationship=RelationshipType.IMPORTS,
                    source_file=changed_file,
                    affected_lines=self._find_usage_lines(key, module_name),
                    reason=reason,
                    confidence=0.85,
                    test_coverage=self._get_test_coverage(org_id, file_path),
                    risk_score=8.0 if impact_type == ImpactType.BROKEN else 5.0,
                ))

            class_name = self._extract_class_name(changed_file)
            if class_name and class_name in deps.get("inherits", set()):
                impacts.append(FileImpact(
                    file_path=file_path,
                    impact_type=ImpactType.POTENTIALLY_BROKEN,
                    relationship=RelationshipType.INHERITS,
                    source_file=changed_file,
                    affected_lines=[],
                    reason=f"Inherits from {class_name} which was changed",
                    confidence=0.9,
                    test_coverage=self._get_test_coverage(org_id, file_path),
                    risk_score=7.0,
                ))

        return impacts

    def _find_indirect_impacts(
        self,
        org_id: str,
        directly_affected: list[str],
    ) -> list[FileImpact]:
        """Find files indirectly impacted through dependencies."""
        impacts: list[FileImpact] = []

        for affected_file in directly_affected:
            module_name = self._file_to_module(affected_file)

            for key, deps in self._file_dependencies.items():
                if not key.startswith(f"{org_id}:"):
                    continue

                file_path = key.split(":")[1]
                if file_path in directly_affected:
                    continue

                if module_name in deps.get("imports", set()):
                    impacts.append(FileImpact(
                        file_path=file_path,
                        impact_type=ImpactType.SAFE,
                        relationship=RelationshipType.IMPORTED_BY,
                        source_file=affected_file,
                        affected_lines=[],
                        reason=f"Imports {module_name} (secondary impact)",
                        confidence=0.6,
                        test_coverage=self._get_test_coverage(org_id, file_path),
                        risk_score=3.0,
                    ))

        return impacts[:10]

    def _find_test_impacts(
        self,
        org_id: str,
        changed_file: str,
    ) -> list[FileImpact]:
        """Find test files that should be run."""
        impacts: list[FileImpact] = []

        key = f"{org_id}:{changed_file}"
        test_files = self._test_mappings.get(key, [])

        for test_file in test_files:
            impacts.append(FileImpact(
                file_path=test_file,
                impact_type=ImpactType.SAFE,
                relationship=RelationshipType.TEST_FOR,
                source_file=changed_file,
                affected_lines=[],
                reason="Test file for changed code",
                confidence=1.0,
                test_coverage=1.0,
                risk_score=0.0,
            ))

        inferred_test = changed_file.replace(".py", "_test.py")
        if inferred_test not in test_files:
            test_key = f"{org_id}:{inferred_test}"
            if test_key in self._file_contents:
                impacts.append(FileImpact(
                    file_path=inferred_test,
                    impact_type=ImpactType.SAFE,
                    relationship=RelationshipType.TEST_FOR,
                    source_file=changed_file,
                    affected_lines=[],
                    reason="Inferred test file",
                    confidence=0.8,
                    test_coverage=1.0,
                    risk_score=0.0,
                ))

        return impacts

    def _file_to_module(self, file_path: str) -> str:
        """Convert file path to module name."""
        module = file_path.replace("/", ".").replace("\\", ".")
        if module.endswith(".py"):
            module = module[:-3]
        return module

    def _extract_class_name(self, file_path: str) -> str | None:
        """Extract likely class name from file path."""
        name = file_path.split("/")[-1]
        if name.endswith(".py"):
            name = name[:-3]
        parts = name.split("_")
        return "".join(p.capitalize() for p in parts)

    def _find_usage_lines(
        self,
        file_key: str,
        module_name: str,
    ) -> list[int]:
        """Find lines where a module is used."""
        content = self._file_contents.get(file_key, "")
        lines = []

        for i, line in enumerate(content.split("\n"), 1):
            if module_name in line:
                lines.append(i)

        return lines[:10]

    def _get_test_coverage(
        self,
        org_id: str,
        file_path: str,
    ) -> float:
        """Get test coverage for a file."""
        key = f"{org_id}:{file_path}"
        if key in self._test_mappings and self._test_mappings[key]:
            return 0.8
        return 0.0

    def _generate_summary(
        self,
        changed_file: str,
        change_description: str,
        impacts: list[FileImpact],
    ) -> str:
        """Generate AI summary of impact analysis."""
        broken = [i for i in impacts if i.impact_type == ImpactType.BROKEN]
        potentially_broken = [
            i for i in impacts if i.impact_type == ImpactType.POTENTIALLY_BROKEN
        ]

        if broken:
            files = [i.file_path.split("/")[-1] for i in broken[:3]]
            return (
                f"CRITICAL: Change to {changed_file} ({change_description}) "
                f"breaks {len(broken)} files: {', '.join(files)}. "
                f"Immediate action required."
            )
        elif potentially_broken:
            return (
                f"Change to {changed_file} potentially affects "
                f"{len(potentially_broken)} files. Review recommended before merge."
            )
        else:
            return f"Change to {changed_file} has minimal impact on other files."

    def get_impact_chain(self, chain_id: str) -> ImpactChain | None:
        """Get an impact chain by ID."""
        return self._analyses.get(chain_id)

    def get_files_needing_review(
        self,
        chain_id: str,
    ) -> list[dict[str, Any]]:
        """Get list of files that need review based on impact."""
        chain = self._analyses.get(chain_id)
        if not chain:
            return []

        files = []
        for impact in chain.impacts:
            if impact.impact_type in (
                ImpactType.BROKEN,
                ImpactType.POTENTIALLY_BROKEN,
            ):
                files.append({
                    "file": impact.file_path,
                    "impact": impact.impact_type.value,
                    "reason": impact.reason,
                    "priority": 1 if impact.impact_type == ImpactType.BROKEN else 2,
                })

        files.sort(key=lambda x: x["priority"])
        return files


_analyzer: MultiFileAnalyzer | None = None


def get_multi_file_analyzer() -> MultiFileAnalyzer:
    """Get the default multi-file analyzer."""
    global _analyzer
    if _analyzer is None:
        _analyzer = MultiFileAnalyzer()
    return _analyzer
