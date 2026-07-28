"""Advanced Code-Level Root Cause Analysis.

Goes beyond basic RCA with:
- Semantic diff understanding (not just +/- lines)
- Multi-file impact analysis
- Blame chain tracking through multiple commits
- Auto-generated fix PRs with tests
- Historical pattern matching for recurring bugs
- PR comment auto-posting
"""

from app.rca_advanced.semantic_diff import (
    SemanticChange,
    ChangeType,
    SemanticDiffAnalyzer,
    get_semantic_diff_analyzer,
)
from app.rca_advanced.multi_file_impact import (
    FileImpact,
    ImpactChain,
    MultiFileAnalyzer,
    get_multi_file_analyzer,
)
from app.rca_advanced.blame_chain import (
    BlameEntry,
    BlameChain,
    BlameChainTracker,
    get_blame_chain_tracker,
)
from app.rca_advanced.auto_fix_pr import (
    FixSuggestion,
    GeneratedTest,
    AutoFixPR,
    AutoFixGenerator,
    get_auto_fix_generator,
)
from app.rca_advanced.pattern_detector import (
    BugPattern,
    PatternMatch,
    HistoricalPatternDetector,
    get_pattern_detector,
)

__all__ = [
    # Semantic Diff
    "SemanticChange",
    "ChangeType",
    "SemanticDiffAnalyzer",
    "get_semantic_diff_analyzer",
    # Multi-File Impact
    "FileImpact",
    "ImpactChain",
    "MultiFileAnalyzer",
    "get_multi_file_analyzer",
    # Blame Chain
    "BlameEntry",
    "BlameChain",
    "BlameChainTracker",
    "get_blame_chain_tracker",
    # Auto Fix PR
    "FixSuggestion",
    "GeneratedTest",
    "AutoFixPR",
    "AutoFixGenerator",
    "get_auto_fix_generator",
    # Pattern Detector
    "BugPattern",
    "PatternMatch",
    "HistoricalPatternDetector",
    "get_pattern_detector",
]
