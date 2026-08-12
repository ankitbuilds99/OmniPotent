"""Centralized defaults used across runtime surfaces.

Keep demo-friendly fallbacks in one place so they are easy to audit and replace
without scattering literal values throughout the codebase.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_REPO_PATH = str(ROOT / "sample_repo")
DEFAULT_LOGS_DATA_PATH = str(ROOT / "app" / "mcp" / "servers" / "logs_metrics" / "sample_data")
DEFAULT_FRONTEND_URL = "http://localhost:3000"
DEFAULT_BACKEND_URL = "http://localhost:8000"
DEFAULT_DEV_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
DEFAULT_PROJECT_REPO_URL = "https://github.com/ankit25bcs10610/OmniPotent"
DEFAULT_THEME_ID = "cosmic"
