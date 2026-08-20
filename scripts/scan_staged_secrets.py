#!/usr/bin/env python3
"""Block a commit that stages a credential. Entry point for the pre-commit hook.

This is a real module rather than a string passed to ``python3 -c`` because the
string version could not be tested, and duly did not work: it opened with
``Path(__file__).parent.parent``, and ``__file__`` is undefined under ``-c``, so
every invocation died with a ``NameError`` before reaching its own ``try``. The
scan never ran once. Nothing noticed, because nothing could import it to check.

The other half of that failure was policy, not syntax. The old script ended:

    except Exception as e:
        print(f'Secrets scan error: {e}')
        sys.exit(0)   # Don't block commit on scanner errors

A security control that exits 0 when it breaks is not a control. It is worse
than nothing, because it prints an all-clear over an outcome it does not know:
the one moment the scanner most needs to be heard -- something unexpected in the
staged content -- is the moment it stayed quiet. So an error here exits 2 and
blocks. If it is a false alarm the escape is one flag (``--no-verify``), typed
deliberately, by someone who has read what broke.

Exit codes: 0 clean (or only low/medium findings), 1 secrets found, 2 scan
failed. Anything non-zero blocks the commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

# A real file, so this works -- and it is what lets the hook run from a checkout
# that was never pip-installed.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟡"}


def main(argv: list[str] | None = None) -> int:
    """Scan staged content and return the process exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    repo_path = args[0] if args else str(REPO_ROOT)

    try:
        from app.security.secrets import SecretsScanError, is_safe_to_commit
    except ImportError as exc:
        # Formerly exit 0 with "scanner not available, skipping check". In this
        # repository the scanner is always available, so an ImportError means
        # something is broken -- a syntax error in app/security, a half-finished
        # rename -- and skipping is how it stays broken indefinitely.
        print(f"❌ Secrets scanner could not be imported: {exc}")
        print(f"   Looked in: {REPO_ROOT}")
        print("   The scan did NOT run. Fix the import, or use --no-verify if")
        print("   you have checked the diff yourself.")
        return 2

    try:
        is_safe, findings = is_safe_to_commit(repo_path)
    except SecretsScanError as exc:
        print(f"❌ Secrets scan could not complete: {exc}")
        print("   The scan did NOT run, so nothing here says your diff is clean.")
        print("   Re-run after fixing, or use --no-verify if you have checked it.")
        return 2

    for finding in findings:
        icon = _SEVERITY_ICON.get(finding.severity, "🟡")
        print()
        print(f"{icon} [{finding.severity.upper()}] {finding.description}")
        print(f"   File: {finding.file_path}:{finding.line_number}")
        # Masked: printing the credential itself would copy it into terminal
        # scrollback and CI logs, which is more places than it started.
        print(f"   Preview: {finding._masked_preview()[:80]}")
        print(f"   Fix: {finding.remediation}")

    if not is_safe:
        print()
        print("❌ Commit blocked: high or critical severity secrets in staged content")
        print()
        print("  1. Remove the secret from the file, then re-stage it")
        print("  2. Move it to an environment variable or AWS Secrets Manager")
        print("  3. Add the file to .gitignore if it should never be committed")
        print()
        print("If the secret is already real, rotate it -- removing it from the")
        print("diff does not un-issue a key that has been on your disk.")
        print()
        print("To commit anyway (only if this is a false positive):")
        print("  git commit --no-verify")
        return 1

    if findings:
        print()
        print(f"⚠️  {len(findings)} low/medium finding(s) above; commit allowed.")
    else:
        print("✅ No secrets detected in staged content")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
