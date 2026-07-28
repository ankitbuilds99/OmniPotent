"""AI-powered fix generation.

Advanced features:
- Context-aware fixes using codebase patterns
- Multiple fix strategies
- Test generation with fixes
- Performance impact analysis
- Breaking change detection
- Fix validation
"""

from app.ai_fix.generator import (
    FixStrategy,
    GeneratedFix,
    FixValidation,
    AIFixGenerator,
    get_ai_fix_generator,
)
from app.ai_fix.patterns import (
    CodePattern,
    PatternMatcher,
    get_pattern_matcher,
)

__all__ = [
    "FixStrategy",
    "GeneratedFix",
    "FixValidation",
    "AIFixGenerator",
    "get_ai_fix_generator",
    "CodePattern",
    "PatternMatcher",
    "get_pattern_matcher",
]
