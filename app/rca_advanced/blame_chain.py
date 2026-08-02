"""Blame Chain Tracking.

Tracks the full chain of changes that led to an incident.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.rca_advanced.blame_chain")


class ChangeRole(str, Enum):
    ROOT_CAUSE = "root_cause"
    CONTRIBUTING = "contributing"
    RELATED = "related"
    CLEANUP = "cleanup"


class AuthorExpertise(str, Enum):
    EXPERT = "expert"
    FAMILIAR = "familiar"
    NEW = "new"
    FIRST_TIME = "first_time"


@dataclass
class BlameEntry:
    """An entry in the blame chain."""

    commit_sha: str
    author: str
    author_email: str
    committed_at: datetime
    message: str
    file_path: str
    line_start: int
    line_end: int
    change_description: str
    role: ChangeRole
    pr_number: int | None = None
    pr_title: str = ""
    reviewers: list[str] = field(default_factory=list)
    tests_added: int = 0
    tests_removed: int = 0
    author_expertise: AuthorExpertise = AuthorExpertise.FAMILIAR
    time_since_last_change_hours: float = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit": self.commit_sha[:8],
            "author": self.author,
            "email": self.author_email,
            "committed_at": self.committed_at.isoformat(),
            "message": self.message[:100],
            "file": self.file_path,
            "lines": {"start": self.line_start, "end": self.line_end},
            "change": self.change_description,
            "role": self.role.value,
            "pr": {
                "number": self.pr_number,
                "title": self.pr_title,
            } if self.pr_number else None,
            "reviewers": self.reviewers,
            "tests": {"added": self.tests_added, "removed": self.tests_removed},
            "author_expertise": self.author_expertise.value,
            "hours_since_last_change": round(self.time_since_last_change_hours, 1),
        }


@dataclass
class BlameChain:
    """A complete blame chain for an incident."""

    chain_id: str
    incident_id: str
    file_path: str
    incident_line: int
    entries: list[BlameEntry]
    root_cause_commit: str
    root_cause_author: str
    contributing_authors: list[str]
    total_commits_analyzed: int
    time_span_days: int
    ai_analysis: str
    recommendations: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def root_cause_entry(self) -> BlameEntry | None:
        for entry in self.entries:
            if entry.role == ChangeRole.ROOT_CAUSE:
                return entry
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "incident_id": self.incident_id,
            "file": self.file_path,
            "incident_line": self.incident_line,
            "entries": [e.to_dict() for e in self.entries],
            "root_cause": {
                "commit": self.root_cause_commit[:8],
                "author": self.root_cause_author,
            },
            "contributing_authors": self.contributing_authors,
            "stats": {
                "commits_analyzed": self.total_commits_analyzed,
                "time_span_days": self.time_span_days,
            },
            "ai_analysis": self.ai_analysis,
            "recommendations": self.recommendations,
            "created_at": self.created_at.isoformat(),
        }


class BlameChainTracker:
    """Tracks blame chains through git history."""

    def __init__(self) -> None:
        self._chains: dict[str, BlameChain] = {}
        self._commit_history: dict[str, list[dict[str, Any]]] = {}
        self._author_stats: dict[str, dict[str, Any]] = {}

    def record_commit(
        self,
        org_id: str,
        commit_sha: str,
        author: str,
        author_email: str,
        committed_at: datetime,
        message: str,
        files_changed: list[dict[str, Any]],
        pr_number: int | None = None,
        pr_title: str = "",
        reviewers: list[str] | None = None,
    ) -> None:
        """Record a commit for later blame analysis."""
        if org_id not in self._commit_history:
            self._commit_history[org_id] = []

        self._commit_history[org_id].append({
            "commit_sha": commit_sha,
            "author": author,
            "author_email": author_email,
            "committed_at": committed_at,
            "message": message,
            "files": files_changed,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "reviewers": reviewers or [],
        })

        self._update_author_stats(org_id, author, files_changed)

    def _update_author_stats(
        self,
        org_id: str,
        author: str,
        files_changed: list[dict[str, Any]],
    ) -> None:
        """Update statistics for an author."""
        key = f"{org_id}:{author}"

        if key not in self._author_stats:
            self._author_stats[key] = {
                "total_commits": 0,
                "files_touched": {},
                "incidents_caused": 0,
            }

        self._author_stats[key]["total_commits"] += 1

        for file_info in files_changed:
            file_path = file_info.get("path", "")
            if file_path not in self._author_stats[key]["files_touched"]:
                self._author_stats[key]["files_touched"][file_path] = 0
            self._author_stats[key]["files_touched"][file_path] += 1

    def build_blame_chain(
        self,
        org_id: str,
        incident_id: str,
        file_path: str,
        incident_line: int,
        suspect_commit: str | None = None,
    ) -> BlameChain:
        """Build a blame chain for an incident."""
        chain_id = f"blame_{incident_id}"

        history = self._commit_history.get(org_id, [])

        relevant_commits = [
            c for c in history
            if any(f.get("path") == file_path for f in c.get("files", []))
        ]

        relevant_commits.sort(key=lambda c: c["committed_at"], reverse=True)

        entries: list[BlameEntry] = []
        root_cause_found = False
        root_cause_commit = ""
        root_cause_author = ""
        contributing_authors: set[str] = set()

        for i, commit in enumerate(relevant_commits[:20]):
            file_info: dict[str, Any] = next(
                (f for f in commit["files"] if f.get("path") == file_path),
                {},
            )

            is_root_cause = (
                (suspect_commit and commit["commit_sha"] == suspect_commit) or
                (not root_cause_found and i == 0)
            )

            if is_root_cause:
                role = ChangeRole.ROOT_CAUSE
                root_cause_found = True
                root_cause_commit = commit["commit_sha"]
                root_cause_author = commit["author"]
            elif i < 3:
                role = ChangeRole.CONTRIBUTING
                contributing_authors.add(commit["author"])
            else:
                role = ChangeRole.RELATED

            expertise = self._get_author_expertise(org_id, commit["author"], file_path)

            time_since_last = 0.0
            if i < len(relevant_commits) - 1:
                next_commit = relevant_commits[i + 1]
                time_diff = commit["committed_at"] - next_commit["committed_at"]
                time_since_last = time_diff.total_seconds() / 3600

            entries.append(BlameEntry(
                commit_sha=commit["commit_sha"],
                author=commit["author"],
                author_email=commit["author_email"],
                committed_at=commit["committed_at"],
                message=commit["message"],
                file_path=file_path,
                line_start=file_info.get("line_start", incident_line),
                line_end=file_info.get("line_end", incident_line),
                change_description=self._generate_change_description(commit, file_info),
                role=role,
                pr_number=commit.get("pr_number"),
                pr_title=commit.get("pr_title", ""),
                reviewers=commit.get("reviewers", []),
                tests_added=file_info.get("tests_added", 0),
                tests_removed=file_info.get("tests_removed", 0),
                author_expertise=expertise,
                time_since_last_change_hours=time_since_last,
            ))

        time_span = 0
        if len(relevant_commits) >= 2:
            first = relevant_commits[-1]["committed_at"]
            last = relevant_commits[0]["committed_at"]
            time_span = (last - first).days

        ai_analysis = self._generate_analysis(entries, root_cause_author)
        recommendations = self._generate_recommendations(entries)

        chain = BlameChain(
            chain_id=chain_id,
            incident_id=incident_id,
            file_path=file_path,
            incident_line=incident_line,
            entries=entries,
            root_cause_commit=root_cause_commit,
            root_cause_author=root_cause_author,
            contributing_authors=list(contributing_authors),
            total_commits_analyzed=len(relevant_commits),
            time_span_days=time_span,
            ai_analysis=ai_analysis,
            recommendations=recommendations,
        )

        self._chains[chain_id] = chain

        key = f"{org_id}:{root_cause_author}"
        if key in self._author_stats:
            self._author_stats[key]["incidents_caused"] += 1

        log.info(
            "Built blame chain: incident=%s root=%s commits=%d",
            incident_id,
            root_cause_commit[:8] if root_cause_commit else "unknown",
            len(entries),
        )

        return chain

    def _get_author_expertise(
        self,
        org_id: str,
        author: str,
        file_path: str,
    ) -> AuthorExpertise:
        """Determine author's expertise level for a file."""
        key = f"{org_id}:{author}"
        stats = self._author_stats.get(key)

        if not stats:
            return AuthorExpertise.FIRST_TIME

        file_commits = stats["files_touched"].get(file_path, 0)

        if file_commits >= 10:
            return AuthorExpertise.EXPERT
        elif file_commits >= 3:
            return AuthorExpertise.FAMILIAR
        elif file_commits >= 1:
            return AuthorExpertise.NEW
        else:
            return AuthorExpertise.FIRST_TIME

    def _generate_change_description(
        self,
        commit: dict[str, Any],
        file_info: dict[str, Any],
    ) -> str:
        """Generate a description of what was changed."""
        message = commit["message"].split("\n")[0]
        additions = file_info.get("additions", 0)
        deletions = file_info.get("deletions", 0)

        return f"{message} (+{additions}/-{deletions})"

    def _generate_analysis(
        self,
        entries: list[BlameEntry],
        root_author: str,
    ) -> str:
        """Generate AI analysis of the blame chain."""
        if not entries:
            return "No relevant commits found."

        root_entry = entries[0] if entries else None

        analysis_parts = []

        if root_entry:
            analysis_parts.append(
                f"Root cause introduced by @{root_author} in commit "
                f"{root_entry.commit_sha[:8]}: {root_entry.message[:50]}"
            )

            if root_entry.reviewers:
                analysis_parts.append(
                    f"PR was reviewed by {', '.join(root_entry.reviewers)}"
                )

            if root_entry.tests_removed > 0:
                analysis_parts.append(
                    f"WARNING: {root_entry.tests_removed} tests were removed in this change"
                )

            if root_entry.author_expertise == AuthorExpertise.FIRST_TIME:
                analysis_parts.append(
                    "Note: This was the author's first change to this file"
                )

        contributing = [e for e in entries if e.role == ChangeRole.CONTRIBUTING]
        if contributing:
            authors = list(set(e.author for e in contributing))
            analysis_parts.append(
                f"Contributing changes by: {', '.join(authors)}"
            )

        return ". ".join(analysis_parts)

    def _generate_recommendations(
        self,
        entries: list[BlameEntry],
    ) -> list[str]:
        """Generate recommendations based on the blame chain."""
        recommendations = []

        root = next((e for e in entries if e.role == ChangeRole.ROOT_CAUSE), None)

        if root:
            if root.tests_removed > 0:
                recommendations.append(
                    "Add tests that cover the removed scenarios"
                )

            if root.author_expertise in (
                AuthorExpertise.NEW,
                AuthorExpertise.FIRST_TIME,
            ):
                recommendations.append(
                    "Require additional review for first-time contributors to this file"
                )

            if not root.reviewers:
                recommendations.append(
                    "Enforce code review for changes to critical files"
                )

            if root.time_since_last_change_hours < 24:
                recommendations.append(
                    "Consider a cooldown period between changes to the same code"
                )

        recent_incidents = sum(
            1 for key, stats in self._author_stats.items()
            if stats.get("incidents_caused", 0) > 2
        )
        if recent_incidents > 0:
            recommendations.append(
                "Pattern detected: Consider additional training or pairing"
            )

        return recommendations

    def get_blame_chain(self, chain_id: str) -> BlameChain | None:
        """Get a blame chain by ID."""
        return self._chains.get(chain_id)

    def get_author_incident_history(
        self,
        org_id: str,
        author: str,
    ) -> dict[str, Any]:
        """Get incident history for an author."""
        key = f"{org_id}:{author}"
        stats = self._author_stats.get(key, {})

        related_chains = [
            c for c in self._chains.values()
            if c.root_cause_author == author or author in c.contributing_authors
        ]

        return {
            "author": author,
            "total_commits": stats.get("total_commits", 0),
            "incidents_caused": stats.get("incidents_caused", 0),
            "files_touched": len(stats.get("files_touched", {})),
            "recent_incidents": [
                {
                    "incident_id": c.incident_id,
                    "role": "root_cause" if c.root_cause_author == author else "contributing",
                    "file": c.file_path,
                }
                for c in related_chains[-5:]
            ],
        }

    def get_file_incident_history(
        self,
        org_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        """Get incident history for a file."""
        related_chains = [
            c for c in self._chains.values()
            if c.file_path == file_path
        ]

        authors = {}
        for chain in related_chains:
            if chain.root_cause_author not in authors:
                authors[chain.root_cause_author] = 0
            authors[chain.root_cause_author] += 1

        return {
            "file": file_path,
            "total_incidents": len(related_chains),
            "authors_involved": authors,
            "recent_incidents": [
                {
                    "incident_id": c.incident_id,
                    "author": c.root_cause_author,
                    "date": c.created_at.isoformat(),
                }
                for c in related_chains[-10:]
            ],
        }


_tracker: BlameChainTracker | None = None


def get_blame_chain_tracker() -> BlameChainTracker:
    """Get the default blame chain tracker."""
    global _tracker
    if _tracker is None:
        _tracker = BlameChainTracker()
    return _tracker
