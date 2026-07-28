"""Multi-commit bisection for root cause identification.

Uses binary search to find the exact commit that introduced a bug.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

log = logging.getLogger("devcopilot.rca.bisection")


class CommitStatus(Enum):
    """Status of a commit in bisection."""

    UNTESTED = "untested"
    GOOD = "good"
    BAD = "bad"
    SKIP = "skip"


@dataclass
class CommitInfo:
    """Information about a commit."""

    commit_hash: str
    short_hash: str
    author: str
    author_email: str
    date: datetime
    message: str
    parents: list[str]
    files_changed: list[str] = field(default_factory=list)
    status: CommitStatus = CommitStatus.UNTESTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_hash": self.commit_hash,
            "short_hash": self.short_hash,
            "author": self.author,
            "date": self.date.isoformat(),
            "message": self.message,
            "files_changed": self.files_changed,
            "status": self.status.value,
        }


@dataclass
class BisectionStep:
    """A step in the bisection process."""

    step_number: int
    commit: CommitInfo
    test_result: CommitStatus
    remaining_commits: int
    tested_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "commit": self.commit.to_dict(),
            "test_result": self.test_result.value,
            "remaining_commits": self.remaining_commits,
            "tested_at": self.tested_at.isoformat(),
        }


@dataclass
class BisectionResult:
    """Result of a bisection search."""

    bisection_id: str
    first_bad_commit: CommitInfo
    last_good_commit: CommitInfo | None
    total_commits_in_range: int
    commits_tested: int
    steps: list[BisectionStep]
    started_at: datetime
    completed_at: datetime
    confidence: float
    analysis_notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bisection_id": self.bisection_id,
            "first_bad_commit": self.first_bad_commit.to_dict(),
            "last_good_commit": (
                self.last_good_commit.to_dict() if self.last_good_commit else None
            ),
            "total_commits_in_range": self.total_commits_in_range,
            "commits_tested": self.commits_tested,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "confidence": self.confidence,
            "analysis_notes": self.analysis_notes,
        }


class CommitBisector:
    """Performs binary search to find the commit that introduced a bug."""

    def __init__(self) -> None:
        self._bisections: dict[str, BisectionResult] = {}
        self._bisection_counter = 0
        self._active_bisection: dict[str, Any] | None = None

    def start_bisection(
        self,
        commits: list[CommitInfo],
        known_good: str | None = None,
        known_bad: str | None = None,
    ) -> str:
        """Start a new bisection process."""
        self._bisection_counter += 1
        bisection_id = f"bisect_{self._bisection_counter}"

        good_idx = 0
        bad_idx = len(commits) - 1

        if known_good:
            for i, c in enumerate(commits):
                if c.commit_hash.startswith(known_good) or c.short_hash == known_good:
                    good_idx = i
                    commits[i].status = CommitStatus.GOOD
                    break

        if known_bad:
            for i, c in enumerate(commits):
                if c.commit_hash.startswith(known_bad) or c.short_hash == known_bad:
                    bad_idx = i
                    commits[i].status = CommitStatus.BAD
                    break

        self._active_bisection = {
            "id": bisection_id,
            "commits": commits,
            "good_idx": good_idx,
            "bad_idx": bad_idx,
            "steps": [],
            "started_at": datetime.now(timezone.utc),
        }

        log.info(
            "bisection started",
            extra={
                "bisection_id": bisection_id,
                "total_commits": len(commits),
                "good_idx": good_idx,
                "bad_idx": bad_idx,
            },
        )

        return bisection_id

    def get_next_commit(self) -> CommitInfo | None:
        """Get the next commit to test (middle of range)."""
        if not self._active_bisection:
            return None

        good_idx = self._active_bisection["good_idx"]
        bad_idx = self._active_bisection["bad_idx"]

        if bad_idx - good_idx <= 1:
            return None

        mid_idx = (good_idx + bad_idx) // 2
        return self._active_bisection["commits"][mid_idx]

    def report_result(
        self,
        commit_hash: str,
        status: CommitStatus,
    ) -> CommitInfo | None:
        """Report the result of testing a commit and get the next one."""
        if not self._active_bisection:
            return None

        commits = self._active_bisection["commits"]
        good_idx = self._active_bisection["good_idx"]
        bad_idx = self._active_bisection["bad_idx"]

        commit_idx = None
        for i, c in enumerate(commits):
            if c.commit_hash.startswith(commit_hash) or c.short_hash == commit_hash:
                commit_idx = i
                c.status = status
                break

        if commit_idx is None:
            return None

        step = BisectionStep(
            step_number=len(self._active_bisection["steps"]) + 1,
            commit=commits[commit_idx],
            test_result=status,
            remaining_commits=bad_idx - good_idx - 1,
            tested_at=datetime.now(timezone.utc),
        )
        self._active_bisection["steps"].append(step)

        if status == CommitStatus.GOOD:
            self._active_bisection["good_idx"] = commit_idx
        elif status == CommitStatus.BAD:
            self._active_bisection["bad_idx"] = commit_idx

        return self.get_next_commit()

    def complete_bisection(self) -> BisectionResult | None:
        """Complete the bisection and return the result."""
        if not self._active_bisection:
            return None

        commits = self._active_bisection["commits"]
        good_idx = self._active_bisection["good_idx"]
        bad_idx = self._active_bisection["bad_idx"]

        first_bad = commits[bad_idx]
        last_good = commits[good_idx] if good_idx < bad_idx else None

        result = BisectionResult(
            bisection_id=self._active_bisection["id"],
            first_bad_commit=first_bad,
            last_good_commit=last_good,
            total_commits_in_range=len(commits),
            commits_tested=len(self._active_bisection["steps"]),
            steps=self._active_bisection["steps"],
            started_at=self._active_bisection["started_at"],
            completed_at=datetime.now(timezone.utc),
            confidence=self._calculate_confidence(
                self._active_bisection["steps"]
            ),
            analysis_notes=self._generate_analysis_notes(
                first_bad,
                last_good,
            ),
        )

        self._bisections[result.bisection_id] = result
        self._active_bisection = None

        log.info(
            "bisection completed",
            extra={
                "bisection_id": result.bisection_id,
                "first_bad_commit": first_bad.short_hash,
                "commits_tested": result.commits_tested,
            },
        )

        return result

    def _calculate_confidence(self, steps: list[BisectionStep]) -> float:
        """Calculate confidence in the bisection result."""
        if not steps:
            return 0.5

        good_count = sum(1 for s in steps if s.test_result == CommitStatus.GOOD)
        bad_count = sum(1 for s in steps if s.test_result == CommitStatus.BAD)

        if good_count == 0 or bad_count == 0:
            return 0.7

        return min(0.99, 0.8 + (len(steps) * 0.02))

    def _generate_analysis_notes(
        self,
        first_bad: CommitInfo,
        last_good: CommitInfo | None,
    ) -> str:
        """Generate analysis notes for the bisection result."""
        notes = [
            f"The bug was introduced in commit {first_bad.short_hash}",
            f"Author: {first_bad.author}",
            f"Date: {first_bad.date.strftime('%Y-%m-%d %H:%M')}",
            f"Message: {first_bad.message}",
        ]

        if first_bad.files_changed:
            notes.append(f"Files changed: {', '.join(first_bad.files_changed[:5])}")

        if last_good:
            notes.append(f"\nLast known good commit: {last_good.short_hash}")

        return "\n".join(notes)

    def auto_bisect(
        self,
        commits: list[CommitInfo],
        test_function: Callable[[CommitInfo], bool],
        known_good: str | None = None,
        known_bad: str | None = None,
    ) -> BisectionResult | None:
        """Automatically run bisection with a test function."""
        bisection_id = self.start_bisection(commits, known_good, known_bad)

        while True:
            next_commit = self.get_next_commit()
            if next_commit is None:
                break

            try:
                is_good = test_function(next_commit)
                status = CommitStatus.GOOD if is_good else CommitStatus.BAD
            except Exception:
                status = CommitStatus.SKIP

            self.report_result(next_commit.commit_hash, status)

        return self.complete_bisection()

    def get_bisection(self, bisection_id: str) -> BisectionResult | None:
        """Get a completed bisection result."""
        return self._bisections.get(bisection_id)

    def get_remaining_steps(self) -> int:
        """Get estimated remaining steps in current bisection."""
        if not self._active_bisection:
            return 0

        good_idx = self._active_bisection["good_idx"]
        bad_idx = self._active_bisection["bad_idx"]
        remaining = bad_idx - good_idx - 1

        if remaining <= 0:
            return 0

        import math
        return int(math.ceil(math.log2(remaining + 1)))

    def get_bisection_status(self) -> dict[str, Any] | None:
        """Get the status of the current bisection."""
        if not self._active_bisection:
            return None

        commits = self._active_bisection["commits"]
        good_idx = self._active_bisection["good_idx"]
        bad_idx = self._active_bisection["bad_idx"]

        return {
            "bisection_id": self._active_bisection["id"],
            "total_commits": len(commits),
            "commits_tested": len(self._active_bisection["steps"]),
            "remaining_range": bad_idx - good_idx - 1,
            "estimated_remaining_steps": self.get_remaining_steps(),
            "current_good_idx": good_idx,
            "current_bad_idx": bad_idx,
            "started_at": self._active_bisection["started_at"].isoformat(),
        }


_bisector: CommitBisector | None = None


def get_commit_bisector() -> CommitBisector:
    """Get the default commit bisector."""
    global _bisector
    if _bisector is None:
        _bisector = CommitBisector()
    return _bisector
