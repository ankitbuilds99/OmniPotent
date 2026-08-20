#!/usr/bin/env bash
# Pre-commit hook: refuse to commit staged content containing a credential.
#
# Install with:  scripts/install-git-hooks.sh
#
# The scan itself lives in scripts/scan_staged_secrets.py. It used to live in a
# `python3 -c "..."` string here, which meant it could not be imported and so
# could not be tested -- and it was broken for its whole life (`__file__` is
# undefined under `-c`). Keep the logic in the .py file so the tests can reach it.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "🔍 Scanning staged content for secrets..."

# Exit code passes straight through: 0 clean, 1 secrets found, 2 scan failed.
# All non-zero block the commit -- including 2. A scanner that could not run has
# not told you the diff is clean, and this hook must not say so on its behalf.
exec python3 "$REPO_ROOT/scripts/scan_staged_secrets.py" "$REPO_ROOT"
