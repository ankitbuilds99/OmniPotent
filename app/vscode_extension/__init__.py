"""VS Code Extension Backend.

Features:
- Inline incident annotations
- AI fix suggestions in editor
- Code-to-incident linking
- PR risk preview
- Log streaming
- Command palette integration
"""

from app.vscode_extension.annotations import (
    IncidentAnnotation,
    AnnotationService,
)
from app.vscode_extension.code_linking import (
    CodeIncidentLink,
    CodeLinkingService,
)
from app.vscode_extension.pr_risk import (
    PRRiskAnalysis,
    PRRiskPreview,
)
from app.vscode_extension.commands import (
    VSCodeCommand,
    CommandPalette,
)

__all__ = [
    "IncidentAnnotation",
    "AnnotationService",
    "CodeIncidentLink",
    "CodeLinkingService",
    "PRRiskAnalysis",
    "PRRiskPreview",
    "VSCodeCommand",
    "CommandPalette",
]
