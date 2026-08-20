"""The pre-commit secrets hook: does it run, and does it block?

Every test here would have failed before this file existed, because the hook did
not work at all. Its scan lived in a ``python3 -c`` string that opened with
``Path(__file__).parent.parent``; ``__file__`` is undefined under ``-c``, so the
script raised ``NameError`` on its first statement, every time, since the day it
was written. Nothing caught it: a string passed to an interpreter cannot be
imported, so there was nothing to test, so nobody tested it. The scan never once
ran, and the hook was never installed in `.git/hooks` either -- its install
command sat in a comment in its own header.

That is why the logic now lives in `scripts/scan_staged_secrets.py`: not for
tidiness, but because a control nobody can exercise is a control nobody can
trust. These tests exercise it.

The fixture key below is assembled from two halves rather than written out. That
is not squeamishness: the scanner these tests exercise is installed as this
repository's pre-commit hook, and it would flag the literal in this file and
block the commit that adds it. Splitting the string breaks the `AKIA[0-9A-Z]{16}`
match at the quote, while the constant still holds the whole key at runtime -- so
this file is a small proof that the control it tests actually works.

The key ends in `C` because the scanner skips any line containing "example", and
the canonical AWS documentation key ends in `EXAMPLE`; a fixture the detector
skipped would make these tests pass for the wrong reason.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.security.secrets import SecretsScanError, SecretsScanner, is_safe_to_commit

REPO_ROOT = Path(__file__).resolve().parent.parent
SCANNER = REPO_ROOT / "scripts" / "scan_staged_secrets.py"
HOOK = REPO_ROOT / "scripts" / "pre-commit-secrets-scan.sh"

# Shaped like an AWS access key, deliberately not one. Split so that this file
# does not trip the very hook it is testing -- see the module docstring.
FIXTURE_KEY = "AKIA" + "IOSFODNN7EXAMPLC"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit, so the index has a HEAD to diff."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "hook@test.invalid")
    _git(tmp_path, "config", "user.name", "Hook Test")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


def _run_scanner(repo: Path) -> subprocess.CompletedProcess[str]:
    """Run the hook's entry point the way the hook runs it."""
    return subprocess.run(
        [sys.executable, str(SCANNER), str(repo)],
        capture_output=True,
        text=True,
    )


class TestTheHookRunsAtAll:
    def test_the_entry_point_executes_without_crashing(self, repo: Path):
        # The whole bug, in one assertion. The old script died here with
        # `NameError: name '__file__' is not defined` before reaching any of
        # its own error handling.
        result = _run_scanner(repo)
        assert "NameError" not in result.stderr, result.stderr
        assert result.returncode == 0, result.stdout + result.stderr
        assert "No secrets detected" in result.stdout

    def test_the_shell_wrapper_executes_too(self):
        # Run against this repository, whose index is normally clean; the point
        # is that the wrapper resolves paths and reaches the scanner.
        result = subprocess.run(
            ["bash", str(HOOK)], cwd=REPO_ROOT, capture_output=True, text=True
        )
        assert "NameError" not in result.stderr, result.stderr
        assert result.returncode in (0, 1), result.stdout + result.stderr


class TestItBlocks:
    def test_a_staged_credential_blocks_the_commit(self, repo: Path):
        (repo / "config.py").write_text(f'AWS_ACCESS_KEY = "{FIXTURE_KEY}"\n')
        _git(repo, "add", "config.py")

        result = _run_scanner(repo)

        assert result.returncode == 1, result.stdout
        assert "Commit blocked" in result.stdout
        assert "config.py" in result.stdout

    def test_the_credential_is_masked_in_the_output(self, repo: Path):
        (repo / "config.py").write_text(f'AWS_ACCESS_KEY = "{FIXTURE_KEY}"\n')
        _git(repo, "add", "config.py")

        result = _run_scanner(repo)

        # Printing the key verbatim would copy it into terminal scrollback and
        # CI logs -- more places than it started, from a tool warning you about
        # exactly that.
        assert FIXTURE_KEY not in result.stdout
        assert "AKIA" in result.stdout  # enough to recognise which line

    def test_unstaged_files_are_not_scanned(self, repo: Path):
        # Not staged: not being committed, so not this hook's business. A hook
        # that blocks on working-tree noise gets disabled within a week.
        (repo / "scratch.py").write_text(f'key = "{FIXTURE_KEY}"\n')

        result = _run_scanner(repo)

        assert result.returncode == 0, result.stdout


class TestItScansWhatIsActuallyBeingCommitted:
    def test_the_staged_blob_is_scanned_not_the_working_tree_copy(self, repo: Path):
        """Stage a key, then clean the working copy. The commit still has it.

        The old scanner listed staged filenames and then read those paths off
        disk, so this sequence -- which is exactly what "oops, let me take that
        out" looks like when you forget to re-stage -- scanned the cleaned file,
        found nothing, and let the key through into the commit.
        """
        secret_file = repo / "config.py"
        secret_file.write_text(f'AWS_ACCESS_KEY = "{FIXTURE_KEY}"\n')
        _git(repo, "add", "config.py")
        secret_file.write_text('AWS_ACCESS_KEY = os.environ["AWS_ACCESS_KEY"]\n')

        result = _run_scanner(repo)

        assert result.returncode == 1, (
            "the staged blob still contains the key; scanning the working tree "
            "copy is scanning the wrong bytes\n" + result.stdout
        )

    def test_a_staged_deletion_does_not_break_the_scan(self, repo: Path):
        # Deleted paths appear in --name-only but have no staged blob to read.
        # Reading them as files used to produce a silent empty result.
        _git(repo, "rm", "-q", "README.md")

        result = _run_scanner(repo)

        assert result.returncode == 0, result.stdout + result.stderr
        assert "could not read staged content" not in result.stdout

    def test_a_path_needing_quoting_is_still_scanned(self, repo: Path):
        """git quotes odd paths on output; splitting those on newlines loses them.

        Without `-z`, a path containing a newline or a quote comes back escaped
        and wrapped in quotes, so the resulting string names no file on disk --
        and a file that cannot be read scanned as clean. A path is attacker- or
        accident-controlled, so it must not be able to hide content from review.
        """
        odd = repo / 'we"ird name.py'
        odd.write_text(f'key = "{FIXTURE_KEY}"\n')
        _git(repo, "add", str(odd))

        result = _run_scanner(repo)

        assert result.returncode == 1, result.stdout + result.stderr


class TestItIsQuietEnoughToBeRead:
    """A hook nobody reads is a hook nobody has.

    On its first real run this reported 70 findings on a commit containing no
    secrets, because the PagerDuty pattern was `[a-zA-Z0-9+/]{20}` -- twenty
    alphanumerics in a row and nothing else. Every file path matched. That is
    the same failure mode as the audit chain reporting `valid: false` forever:
    an alarm that is always on carries no information, and the first true
    positive arrives looking exactly like the 70 before it.
    """

    def test_ordinary_source_paths_are_not_flagged_as_credentials(self, repo: Path):
        (repo / "notes.md").write_text(
            "| Latency | Complete | `app/observability/metrics.py` |\n"
            "See https://github.com/some-org/some-repo for details.\n"
            "from app.security.headers import SecurityHeadersMiddleware\n"
        )
        _git(repo, "add", "notes.md")

        result = _run_scanner(repo)

        assert result.returncode == 0, result.stdout
        assert "No secrets detected" in result.stdout

    def test_a_real_looking_pagerduty_key_is_still_caught(self):
        # Narrowing the pattern must not silence it: with the keyword and a
        # 32-character value present, it still fires.
        findings = SecretsScanner().scan_content(
            'PAGERDUTY_ROUTING_KEY = "' + "r02" + "a1b2c3d4e5f60718293a4b5c6d7e8f" + '"'
        )
        assert any(f.secret_type.value == "pagerduty_key" for f in findings), [
            f.description for f in findings
        ]


class TestItFailsClosed:
    def test_a_scan_that_cannot_run_blocks_rather_than_passing(self, tmp_path: Path):
        """Not a git repo -> the scan cannot run -> it must not report clean.

        The old script ended with `except Exception: sys.exit(0)` under the
        comment "Don't block commit on scanner errors". That converts an unknown
        into an all-clear at precisely the moment the answer is unknown.
        """
        not_a_repo = tmp_path / "nope"
        not_a_repo.mkdir()

        result = subprocess.run(
            [sys.executable, str(SCANNER), str(not_a_repo)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2, result.stdout + result.stderr
        assert "did NOT run" in result.stdout
        assert "No secrets detected" not in result.stdout

    def test_scan_git_staged_raises_instead_of_returning_empty(self, tmp_path: Path):
        # The library-level half of the same rule: "found nothing" and "never
        # looked" must not be the same return value.
        with pytest.raises(SecretsScanError):
            SecretsScanner().scan_git_staged(tmp_path)

    def test_is_safe_to_commit_propagates_the_failure(self, tmp_path: Path):
        # It must not answer `(True, [])` -- that reads as "safe to commit".
        with pytest.raises(SecretsScanError):
            is_safe_to_commit(tmp_path)


class TestTheInstaller:
    def test_git_actually_refuses_the_commit_end_to_end(self, repo: Path):
        """Install the hook, stage a key, run a real `git commit`, be refused.

        Everything above tests the scanner directly. This tests the part that
        was missing in production: that git invokes it. The hook was never in
        `.git/hooks` -- its install command lived in a comment -- so a scanner
        that worked perfectly would still have caught nothing.
        """
        # Give the throwaway repo the pieces the hook resolves relative to
        # itself: the scripts, and the `app` package the scanner imports.
        shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
        (repo / "app").symlink_to(REPO_ROOT / "app", target_is_directory=True)

        subprocess.run(
            ["bash", str(repo / "scripts" / "install-git-hooks.sh")],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        hook = repo / ".git" / "hooks" / "pre-commit"
        assert hook.stat().st_mode & 0o111, "hook is not executable; git skips it"

        (repo / "config.py").write_text(f'AWS_ACCESS_KEY = "{FIXTURE_KEY}"\n')
        _git(repo, "add", "config.py")

        blocked = subprocess.run(
            ["git", "commit", "-m", "add config"],
            cwd=repo, capture_output=True, text=True,
        )
        assert blocked.returncode != 0, "git created the commit anyway"
        # git relays hook output on stderr, so check both streams.
        assert "Commit blocked" in blocked.stdout + blocked.stderr

        # And the commit genuinely does not exist.
        log = _git(repo, "log", "--oneline")
        assert "add config" not in log.stdout

        # --no-verify remains the deliberate escape hatch.
        forced = subprocess.run(
            ["git", "commit", "--no-verify", "-m", "add config"],
            cwd=repo, capture_output=True, text=True,
        )
        assert forced.returncode == 0, forced.stdout + forced.stderr

    def test_a_clean_commit_is_not_obstructed(self, repo: Path):
        shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
        (repo / "app").symlink_to(REPO_ROOT / "app", target_is_directory=True)
        subprocess.run(
            ["bash", str(repo / "scripts" / "install-git-hooks.sh")],
            cwd=repo, capture_output=True, text=True, check=True,
        )

        (repo / "config.py").write_text('AWS_ACCESS_KEY = os.environ["AWS_KEY"]\n')
        _git(repo, "add", "config.py")

        result = subprocess.run(
            ["git", "commit", "-m", "read the key from the environment"],
            cwd=repo, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_it_refuses_to_clobber_somebody_elses_hook(self, repo: Path):
        installer = REPO_ROOT / "scripts" / "install-git-hooks.sh"
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho mine\n")

        result = subprocess.run(
            ["bash", str(installer)], cwd=repo, capture_output=True, text=True
        )

        assert "mine" in hook.read_text(), "an existing hook was overwritten"
        assert "leaving it alone" in result.stdout
