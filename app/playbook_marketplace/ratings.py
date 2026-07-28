"""Playbook Ratings and Reviews.

Manages ratings, reviews, and quality scores.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("devcopilot.playbook_marketplace.ratings")


@dataclass
class PlaybookRating:
    """A rating for a playbook."""

    rating_id: str
    playbook_id: str
    user_id: str
    org_id: str
    stars: int
    title: str = ""
    review_text: str = ""
    helpful_count: int = 0
    verified_run: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.rating_id,
            "playbook_id": self.playbook_id,
            "stars": self.stars,
            "title": self.title,
            "review": self.review_text,
            "helpful": self.helpful_count,
            "verified_run": self.verified_run,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class RatingSummary:
    """Summary of ratings for a playbook."""

    playbook_id: str
    avg_rating: float
    total_reviews: int
    star_distribution: dict[int, int]
    verified_run_percent: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "playbook_id": self.playbook_id,
            "avg_rating": round(self.avg_rating, 1),
            "total_reviews": self.total_reviews,
            "distribution": self.star_distribution,
            "verified_percent": round(self.verified_run_percent, 1),
        }


class RatingsEngine:
    """Manages playbook ratings and reviews."""

    def __init__(self) -> None:
        self._ratings: dict[str, list[PlaybookRating]] = {}
        self._user_runs: dict[str, set[str]] = {}

    def record_run(self, user_id: str, playbook_id: str) -> None:
        """Record that a user ran a playbook."""
        if user_id not in self._user_runs:
            self._user_runs[user_id] = set()
        self._user_runs[user_id].add(playbook_id)

    def has_run(self, user_id: str, playbook_id: str) -> bool:
        """Check if user has run a playbook."""
        return playbook_id in self._user_runs.get(user_id, set())

    def submit_rating(
        self,
        playbook_id: str,
        user_id: str,
        org_id: str,
        stars: int,
        title: str = "",
        review_text: str = "",
    ) -> PlaybookRating:
        """Submit a rating for a playbook."""
        stars = max(1, min(5, stars))

        if playbook_id not in self._ratings:
            self._ratings[playbook_id] = []

        existing = next(
            (r for r in self._ratings[playbook_id] if r.user_id == user_id),
            None,
        )
        if existing:
            existing.stars = stars
            existing.title = title
            existing.review_text = review_text
            return existing

        rating = PlaybookRating(
            rating_id=f"rating_{datetime.now(timezone.utc).timestamp()}",
            playbook_id=playbook_id,
            user_id=user_id,
            org_id=org_id,
            stars=stars,
            title=title,
            review_text=review_text,
            verified_run=self.has_run(user_id, playbook_id),
        )

        self._ratings[playbook_id].append(rating)

        log.info(
            "Rating submitted: playbook=%s user=%s stars=%d",
            playbook_id,
            user_id,
            stars,
        )

        return rating

    def get_ratings(
        self,
        playbook_id: str,
        limit: int = 20,
        verified_only: bool = False,
    ) -> list[PlaybookRating]:
        """Get ratings for a playbook."""
        ratings = self._ratings.get(playbook_id, [])

        if verified_only:
            ratings = [r for r in ratings if r.verified_run]

        ratings.sort(key=lambda r: r.helpful_count, reverse=True)

        return ratings[:limit]

    def get_summary(self, playbook_id: str) -> RatingSummary:
        """Get rating summary for a playbook."""
        ratings = self._ratings.get(playbook_id, [])

        if not ratings:
            return RatingSummary(
                playbook_id=playbook_id,
                avg_rating=0,
                total_reviews=0,
                star_distribution={1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
                verified_run_percent=0,
            )

        total_stars = sum(r.stars for r in ratings)
        avg = total_stars / len(ratings)

        distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for r in ratings:
            distribution[r.stars] += 1

        verified_count = sum(1 for r in ratings if r.verified_run)
        verified_percent = (verified_count / len(ratings)) * 100

        return RatingSummary(
            playbook_id=playbook_id,
            avg_rating=avg,
            total_reviews=len(ratings),
            star_distribution=distribution,
            verified_run_percent=verified_percent,
        )

    def mark_helpful(
        self,
        rating_id: str,
        user_id: str,
    ) -> bool:
        """Mark a rating as helpful."""
        for ratings in self._ratings.values():
            for rating in ratings:
                if rating.rating_id == rating_id:
                    rating.helpful_count += 1
                    return True
        return False

    def get_top_reviewers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get top reviewers by helpful count."""
        user_stats: dict[str, dict[str, Any]] = {}

        for ratings in self._ratings.values():
            for rating in ratings:
                if rating.user_id not in user_stats:
                    user_stats[rating.user_id] = {
                        "user_id": rating.user_id,
                        "total_reviews": 0,
                        "total_helpful": 0,
                        "verified_reviews": 0,
                    }

                user_stats[rating.user_id]["total_reviews"] += 1
                user_stats[rating.user_id]["total_helpful"] += rating.helpful_count
                if rating.verified_run:
                    user_stats[rating.user_id]["verified_reviews"] += 1

        reviewers = list(user_stats.values())
        reviewers.sort(key=lambda x: x["total_helpful"], reverse=True)

        return reviewers[:limit]


_engine: RatingsEngine | None = None


def get_ratings_engine() -> RatingsEngine:
    """Get the default ratings engine."""
    global _engine
    if _engine is None:
        _engine = RatingsEngine()
    return _engine
