"""Reviewer Suggester.

Suggests reviewers based on code ownership, expertise, and availability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("devcopilot.github_app.reviewer_suggester")


@dataclass
class CodeOwnership:
    """Code ownership for a path."""

    path_pattern: str
    owners: list[str]
    commit_count: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path_pattern,
            "owners": self.owners,
            "commit_count": self.commit_count,
        }


@dataclass
class ReviewerProfile:
    """Profile of a potential reviewer."""

    username: str
    email: str = ""
    expertise_areas: list[str] = field(default_factory=list)
    recent_reviews: int = 0
    avg_review_time_hours: float = 24.0
    is_available: bool = True
    timezone: str = "UTC"
    resolved_incidents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "expertise": self.expertise_areas,
            "recent_reviews": self.recent_reviews,
            "avg_review_time": self.avg_review_time_hours,
            "available": self.is_available,
        }


@dataclass
class ReviewerSuggestion:
    """A suggested reviewer with reasons."""

    reviewer: ReviewerProfile
    score: float
    reasons: list[str]
    is_primary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.reviewer.username,
            "score": round(self.score, 2),
            "reasons": self.reasons,
            "is_primary": self.is_primary,
            "profile": self.reviewer.to_dict(),
        }


class ReviewerSuggester:
    """Suggests reviewers for PRs."""

    def __init__(self) -> None:
        self._ownership: dict[str, CodeOwnership] = {}
        self._reviewers: dict[str, ReviewerProfile] = {}
        self._incident_resolvers: dict[str, list[str]] = {}

    def register_ownership(
        self,
        path_pattern: str,
        owners: list[str],
    ) -> CodeOwnership:
        """Register code ownership for a path."""
        ownership = CodeOwnership(
            path_pattern=path_pattern,
            owners=owners,
        )
        self._ownership[path_pattern] = ownership
        return ownership

    def register_reviewer(self, profile: ReviewerProfile) -> None:
        """Register a reviewer profile."""
        self._reviewers[profile.username] = profile

    def record_commit(
        self,
        path: str,
        author: str,
    ) -> None:
        """Record a commit for ownership tracking."""
        for pattern, ownership in self._ownership.items():
            if pattern in path:
                ownership.commit_count[author] = (
                    ownership.commit_count.get(author, 0) + 1
                )

    def record_incident_resolution(
        self,
        incident_id: str,
        resolver: str,
        files_involved: list[str],
    ) -> None:
        """Record who resolved an incident."""
        if resolver in self._reviewers:
            self._reviewers[resolver].resolved_incidents.append(incident_id)

        for f in files_involved:
            if f not in self._incident_resolvers:
                self._incident_resolvers[f] = []
            if resolver not in self._incident_resolvers[f]:
                self._incident_resolvers[f].append(resolver)

    def suggest_reviewers(
        self,
        files: list[str],
        pr_author: str,
        num_suggestions: int = 3,
    ) -> list[ReviewerSuggestion]:
        """Suggest reviewers for a PR."""
        candidates: dict[str, dict[str, Any]] = {}

        for f in files:
            for pattern, ownership in self._ownership.items():
                if pattern in f:
                    for owner in ownership.owners:
                        if owner != pr_author:
                            if owner not in candidates:
                                candidates[owner] = {
                                    "score": 0,
                                    "reasons": [],
                                }
                            candidates[owner]["score"] += 3.0
                            reason = f"Code owner of {pattern}"
                            if reason not in candidates[owner]["reasons"]:
                                candidates[owner]["reasons"].append(reason)

            for pattern, ownership in self._ownership.items():
                if pattern in f:
                    for author, commits in ownership.commit_count.items():
                        if author != pr_author and commits >= 5:
                            if author not in candidates:
                                candidates[author] = {
                                    "score": 0,
                                    "reasons": [],
                                }
                            candidates[author]["score"] += min(2.0, commits * 0.2)
                            candidates[author]["reasons"].append(
                                f"{commits} commits in {pattern}"
                            )

            resolvers = self._incident_resolvers.get(f, [])
            for resolver in resolvers:
                if resolver != pr_author:
                    if resolver not in candidates:
                        candidates[resolver] = {
                            "score": 0,
                            "reasons": [],
                        }
                    candidates[resolver]["score"] += 4.0
                    candidates[resolver]["reasons"].append(
                        f"Resolved incident involving {f}"
                    )

        for username, data in candidates.items():
            profile = self._reviewers.get(username)
            if profile:
                if profile.avg_review_time_hours < 12:
                    data["score"] += 1.0
                    data["reasons"].append("Fast reviewer")

                if not profile.is_available:
                    data["score"] -= 2.0
                    data["reasons"].append("Currently unavailable")

                if profile.recent_reviews > 10:
                    data["score"] -= 0.5

        suggestions: list[ReviewerSuggestion] = []
        sorted_candidates = sorted(
            candidates.items(),
            key=lambda x: float(x[1]["score"]),
            reverse=True
        )
        for username, data in sorted_candidates[:num_suggestions]:
            profile = self._reviewers.get(username)
            if not profile:
                profile = ReviewerProfile(username=username)

            suggestions.append(ReviewerSuggestion(
                reviewer=profile,
                score=data["score"],
                reasons=data["reasons"][:3],
                is_primary=len(suggestions) == 0,
            ))

        return suggestions

    def get_ownership_summary(self) -> list[dict[str, Any]]:
        """Get summary of code ownership."""
        return [o.to_dict() for o in self._ownership.values()]

    def get_reviewer_stats(self, username: str) -> dict[str, Any] | None:
        """Get stats for a reviewer."""
        profile = self._reviewers.get(username)
        if not profile:
            return None

        return {
            "profile": profile.to_dict(),
            "incidents_resolved": len(profile.resolved_incidents),
            "recent_incidents": profile.resolved_incidents[-5:],
        }


_suggester: ReviewerSuggester | None = None


def get_reviewer_suggester() -> ReviewerSuggester:
    """Get the default reviewer suggester."""
    global _suggester
    if _suggester is None:
        _suggester = ReviewerSuggester()
    return _suggester
