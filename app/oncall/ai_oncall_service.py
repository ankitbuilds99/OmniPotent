"""AI-Powered On-Call Intelligence Service.

Enterprise-grade AI features for on-call management:
- Smart alert routing based on skills and context
- AI-powered coverage finder
- Alert fatigue detection
- Workload balancing recommendations
- Optimal schedule generation
- Incident pattern analysis
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta, time, timezone
from typing import Any
from collections import defaultdict
from uuid import UUID

import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.oncall.models import (
    OncallTeamMember,
    OncallSchedule,
    OncallShift,
    OncallAlert,
    OncallTimeOff,
)
from app.oncall.schedule_service import ScheduleService

log = logging.getLogger("omnipotent.oncall.ai")


class AIOncallService:
    """AI-powered on-call intelligence."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.schedule_service = ScheduleService(db)

    # =========================================================================
    # SMART ALERT ROUTING
    # =========================================================================

    async def route_alert(
        self,
        alert: OncallAlert,
        team_id: UUID,
    ) -> dict[str, Any]:
        """
        Intelligently route an alert to the best responder.

        Considers:
        - User skills vs alert type/tags
        - Current workload
        - Response time history
        - Time of day (working hours)
        - Recent alert handling success
        """
        members = await self._get_team_members_with_metrics(team_id)

        current_oncall = await self.schedule_service.get_current_oncall(team_id)

        candidates = []
        for member in members:
            score = await self._calculate_routing_score(alert, member)
            candidates.append({
                "user_id": member.user_id,
                "score": score["total"],
                "breakdown": score,
                "is_current_oncall": member.user_id == current_oncall.get("primary"),
            })

        def get_score(x: dict[str, Any]) -> float:
            val = x.get("score", 0)
            return float(val) if val is not None else 0.0

        candidates.sort(key=get_score, reverse=True)

        best_candidate = candidates[0] if candidates else None
        default_oncall = current_oncall.get("primary")

        should_reroute = False
        routing_reason = "Default on-call"

        if best_candidate and default_oncall:
            if best_candidate["user_id"] != default_oncall:
                default_score = next(
                    (get_score(c) for c in candidates if c["user_id"] == default_oncall),
                    0.0
                )
                best_score = get_score(best_candidate)
                if best_score > default_score * 1.2:
                    should_reroute = True
                    breakdown = best_candidate.get("breakdown")
                    if isinstance(breakdown, dict):
                        routing_reason = self._generate_routing_reason(
                            breakdown,
                            alert,
                        )

        recommended_user = best_candidate.get("user_id") if (should_reroute and best_candidate) else default_oncall
        return {
            "recommended_user_id": recommended_user,
            "should_reroute": should_reroute,
            "routing_reason": routing_reason,
            "candidates": candidates[:5],
            "default_oncall": default_oncall,
        }

    async def _calculate_routing_score(
        self,
        alert: OncallAlert,
        member: OncallTeamMember,
    ) -> dict[str, float]:
        """Calculate routing score for a team member."""
        scores: dict[str, float] = {}

        alert_tags = set(alert.tags or [])
        member_skills = set(member.skills or [])
        if alert_tags and member_skills:
            overlap = len(alert_tags.intersection(member_skills))
            scores["skills"] = min(40.0, overlap * 20.0)
        else:
            scores["skills"] = 20.0

        recent_alerts = await self._get_user_recent_alerts(
            member.user_id,
            hours_back=24,
        )
        if len(recent_alerts) == 0:
            scores["workload"] = 20.0
        elif len(recent_alerts) < 3:
            scores["workload"] = 15.0
        elif len(recent_alerts) < 5:
            scores["workload"] = 10.0
        else:
            scores["workload"] = 5.0

        avg_response_time = await self._get_avg_response_time(member.user_id)
        if avg_response_time is None:
            scores["response_time"] = 10.0
        elif avg_response_time < 60:
            scores["response_time"] = 20.0
        elif avg_response_time < 180:
            scores["response_time"] = 15.0
        elif avg_response_time < 300:
            scores["response_time"] = 10.0
        else:
            scores["response_time"] = 5.0

        if member.working_hours:
            in_hours = self._is_in_working_hours(member)
            scores["availability"] = 10.0 if in_hours else 5.0
        else:
            scores["availability"] = 7.0

        success_rate = await self._get_resolution_success_rate(member.user_id)
        scores["success_rate"] = int(success_rate * 10) if success_rate else 7.0

        scores["total"] = sum(scores.values())
        return scores

    def _generate_routing_reason(
        self,
        score_breakdown: dict[str, float],
        alert: OncallAlert,
    ) -> str:
        """Generate human-readable routing reason."""
        reasons = []

        if score_breakdown.get("skills", 0) > 30:
            reasons.append("has relevant skills for this alert type")

        if score_breakdown.get("workload", 0) > 15:
            reasons.append("has lower current workload")

        if score_breakdown.get("response_time", 0) > 15:
            reasons.append("has faster historical response times")

        if not reasons:
            reasons.append("is the best available responder")

        return f"Routed because this person {', '.join(reasons)}"

    # =========================================================================
    # AI COVERAGE FINDER
    # =========================================================================

    async def find_optimal_coverage(
        self,
        schedule_id: UUID,
        start_time: datetime,
        end_time: datetime,
        exclude_user_id: UUID | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        AI-powered coverage finder with natural language reasoning.
        """
        result = await self.db.execute(
            select(OncallSchedule)
            .options(selectinload(OncallSchedule.team))
            .where(OncallSchedule.id == schedule_id)
        )
        schedule = result.scalar_one_or_none()
        if not schedule:
            return {"error": "Schedule not found"}

        members = await self._get_team_members_with_metrics(schedule.team_id)

        candidates_data = []
        for member in members:
            if exclude_user_id and member.user_id == exclude_user_id:
                continue

            has_pto = await self._has_pto_overlap(
                member.user_id,
                start_time.date(),
                end_time.date(),
            )
            if has_pto:
                continue

            recent_hours = await self._get_recent_oncall_hours(member.user_id, 30)
            days_since_oncall = await self._days_since_oncall(member.user_id)

            score = 100.0
            if recent_hours > 168:
                score -= 30
            elif recent_hours > 84:
                score -= 15

            if days_since_oncall is not None:
                if days_since_oncall > 14:
                    score += 20
                elif days_since_oncall > 7:
                    score += 10

            candidates_data.append({
                "user_id": str(member.user_id),
                "score": score,
                "recent_oncall_hours": recent_hours,
                "days_since_oncall": days_since_oncall,
                "skills": member.skills,
                "timezone": member.timezone,
            })

        if not candidates_data:
            return {
                "candidates": [],
                "recommendation": None,
                "reason": "No available team members found",
            }

        def get_coverage_score(x: dict[str, Any]) -> float:
            val = x.get("score", 0)
            return float(val) if val is not None else 0.0

        candidates_data.sort(key=get_coverage_score, reverse=True)

        best = candidates_data[0]
        explanation = f"Recommended because they have {best['recent_oncall_hours']:.0f} hours in last 30 days"
        if best.get("days_since_oncall"):
            explanation += f" and {best['days_since_oncall']} days since last on-call"

        return {
            "candidates": candidates_data[:5],
            "recommendation": best["user_id"],
            "explanation": explanation,
            "coverage_period": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
            },
        }

    # =========================================================================
    # ALERT FATIGUE DETECTION
    # =========================================================================

    async def detect_alert_fatigue(
        self,
        team_id: UUID,
        window_hours: int = 24,
    ) -> dict[str, Any]:
        """
        Detect alert fatigue patterns.

        Indicators:
        - High volume of alerts
        - Decreasing acknowledgment rates
        - Increasing response times
        - Repeated alerts from same source
        """
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=window_hours)

        result = await self.db.execute(
            select(OncallAlert)
            .where(
                OncallAlert.team_id == team_id,
                OncallAlert.triggered_at >= start_time,
                OncallAlert.triggered_at <= end_time,
            )
            .order_by(OncallAlert.triggered_at)
        )
        alerts = list(result.scalars().all())

        if len(alerts) < 5:
            return {
                "fatigue_detected": False,
                "alert_count": len(alerts),
                "reason": "Insufficient data (less than 5 alerts)",
            }

        total_alerts = len(alerts)
        acknowledged = sum(1 for a in alerts if a.acknowledged_at)
        ack_rate = acknowledged / total_alerts if total_alerts > 0 else 0

        response_times = []
        for alert in alerts:
            if alert.acknowledged_at and alert.triggered_at:
                delta = (alert.acknowledged_at - alert.triggered_at).total_seconds()
                response_times.append(delta)

        avg_response_time = sum(response_times) / len(response_times) if response_times else None

        source_counts: dict[str, int] = defaultdict(int)
        for alert in alerts:
            source_counts[alert.source or "unknown"] += 1

        indicators = []
        fatigue_score = 0

        if total_alerts > 20:
            indicators.append(f"High alert volume: {total_alerts} alerts in {window_hours}h")
            fatigue_score += 30

        if ack_rate < 0.7:
            indicators.append(f"Low acknowledgment rate: {ack_rate:.0%}")
            fatigue_score += 25

        if avg_response_time and avg_response_time > 300:
            indicators.append(f"Slow average response: {avg_response_time/60:.1f} minutes")
            fatigue_score += 20

        top_source = max(source_counts.items(), key=lambda x: x[1])
        if top_source[1] > total_alerts * 0.5:
            indicators.append(f"Noisy source: {top_source[0]} ({top_source[1]} alerts)")
            fatigue_score += 25

        fatigue_detected = fatigue_score >= 50

        recommendations = []
        if fatigue_detected:
            recommendations = self._get_fatigue_recommendations(
                indicators,
                dict(source_counts),
                total_alerts,
            )

        return {
            "fatigue_detected": fatigue_detected,
            "fatigue_score": fatigue_score,
            "metrics": {
                "total_alerts": total_alerts,
                "acknowledged": acknowledged,
                "ack_rate": round(ack_rate, 2),
                "avg_response_time_seconds": round(avg_response_time, 1) if avg_response_time else None,
            },
            "indicators": indicators,
            "top_sources": dict(sorted(source_counts.items(), key=lambda x: -x[1])[:5]),
            "recommendations": recommendations,
        }

    def _get_fatigue_recommendations(
        self,
        indicators: list[str],
        source_counts: dict[str, int],
        total_alerts: int,
    ) -> list[str]:
        """Get recommendations for reducing alert fatigue."""
        recommendations = []

        for indicator in indicators:
            if "High alert volume" in indicator:
                recommendations.append("Consider adjusting alert thresholds to reduce noise")
            elif "Low acknowledgment" in indicator:
                recommendations.append("Review escalation policies and notification channels")
            elif "Slow average response" in indicator:
                recommendations.append("Consider adding more team members to the rotation")
            elif "Noisy source" in indicator:
                top_source = max(source_counts.items(), key=lambda x: x[1])
                recommendations.append(f"Investigate and tune alerts from '{top_source[0]}'")

        if not recommendations:
            recommendations.append("Monitor trends and consider alert consolidation")

        return recommendations

    # =========================================================================
    # WORKLOAD BALANCING
    # =========================================================================

    async def analyze_workload_balance(
        self,
        team_id: UUID,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """Analyze on-call workload distribution across team."""
        start_date = date.today() - timedelta(days=period_days)
        end_date = date.today()

        result = await self.db.execute(
            select(OncallShift)
            .join(OncallSchedule, OncallShift.schedule_id == OncallSchedule.id)
            .where(
                OncallSchedule.team_id == team_id,
                OncallShift.start_time >= datetime.combine(start_date, time.min),
                OncallShift.end_time <= datetime.combine(end_date, time.max),
            )
        )
        shifts = list(result.scalars().all())

        alerts_result = await self.db.execute(
            select(OncallAlert)
            .where(
                OncallAlert.team_id == team_id,
                OncallAlert.triggered_at >= datetime.combine(start_date, time.min),
                OncallAlert.triggered_at <= datetime.combine(end_date, time.max),
            )
        )
        alerts = list(alerts_result.scalars().all())

        user_metrics: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "hours": 0.0,
            "alerts_received": 0,
            "alerts_acknowledged": 0,
            "weekend_hours": 0.0,
            "night_hours": 0.0,
        })

        for shift in shifts:
            hours = (shift.end_time - shift.start_time).total_seconds() / 3600
            user_metrics[str(shift.user_id)]["hours"] += hours

            if shift.start_time.weekday() >= 5:
                user_metrics[str(shift.user_id)]["weekend_hours"] += hours

        for alert in alerts:
            for shift in shifts:
                if (shift.user_id and
                    shift.start_time <= alert.triggered_at <= shift.end_time):
                    user_metrics[str(shift.user_id)]["alerts_received"] += 1
                    if alert.acknowledged_by == shift.user_id:
                        user_metrics[str(shift.user_id)]["alerts_acknowledged"] += 1
                    break

        total_hours = sum(m["hours"] for m in user_metrics.values())
        num_users = len(user_metrics)
        expected_hours = total_hours / num_users if num_users > 0 else 0

        imbalances = []
        for user_id, metrics in user_metrics.items():
            deviation = (metrics["hours"] - expected_hours) / expected_hours if expected_hours > 0 else 0
            if abs(deviation) > 0.2:
                direction = "overloaded" if deviation > 0 else "underutilized"
                imbalances.append({
                    "user_id": user_id,
                    "hours": round(metrics["hours"], 1),
                    "expected": round(expected_hours, 1),
                    "deviation": round(deviation * 100, 1),
                    "status": direction,
                })

        return {
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": period_days,
            },
            "summary": {
                "total_hours": round(total_hours, 1),
                "total_users": num_users,
                "expected_per_user": round(expected_hours, 1),
                "total_alerts": len(alerts),
            },
            "user_breakdown": dict(user_metrics),
            "imbalances": imbalances,
            "is_balanced": len(imbalances) == 0,
        }

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _get_team_members_with_metrics(
        self,
        team_id: UUID,
    ) -> list[OncallTeamMember]:
        """Get team members."""
        result = await self.db.execute(
            select(OncallTeamMember)
            .where(
                OncallTeamMember.team_id == team_id,
                OncallTeamMember.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def _get_user_recent_alerts(
        self,
        user_id: UUID,
        hours_back: int = 24,
    ) -> list[OncallAlert]:
        """Get user's recent alerts."""
        since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        result = await self.db.execute(
            select(OncallAlert)
            .where(
                (OncallAlert.acknowledged_by == user_id) |
                (OncallAlert.resolved_by == user_id),
                OncallAlert.triggered_at >= since,
            )
        )
        return list(result.scalars().all())

    async def _get_avg_response_time(
        self,
        user_id: UUID,
        days_back: int = 30,
    ) -> float | None:
        """Get average acknowledgment time."""
        since = datetime.now(timezone.utc) - timedelta(days=days_back)

        result = await self.db.execute(
            select(OncallAlert)
            .where(
                OncallAlert.acknowledged_by == user_id,
                OncallAlert.acknowledged_at.isnot(None),
                OncallAlert.triggered_at >= since,
            )
        )
        alerts = result.scalars().all()

        if not alerts:
            return None

        times = [
            (a.acknowledged_at - a.triggered_at).total_seconds()
            for a in alerts
            if a.acknowledged_at and a.triggered_at
        ]

        return sum(times) / len(times) if times else None

    async def _get_resolution_success_rate(
        self,
        user_id: UUID,
        days_back: int = 30,
    ) -> float | None:
        """Get resolution success rate."""
        since = datetime.now(timezone.utc) - timedelta(days=days_back)

        result = await self.db.execute(
            select(OncallAlert)
            .where(
                (OncallAlert.acknowledged_by == user_id) |
                (OncallAlert.resolved_by == user_id),
                OncallAlert.triggered_at >= since,
            )
        )
        alerts = list(result.scalars().all())

        if not alerts:
            return None

        resolved = sum(1 for a in alerts if a.status == "resolved")
        return resolved / len(alerts) if alerts else None

    def _is_in_working_hours(self, member: OncallTeamMember) -> bool:
        """Check if current time is in member's working hours."""
        if not member.working_hours or not member.timezone:
            return True

        tz = pytz.timezone(member.timezone)
        local_now = datetime.now(tz)
        day_name = local_now.strftime("%A").lower()

        day_hours = member.working_hours.get(day_name)
        if not day_hours:
            return False

        current_time = local_now.time()
        start = time.fromisoformat(day_hours["start"])
        end = time.fromisoformat(day_hours["end"])

        return start <= current_time <= end

    async def _has_pto_overlap(
        self,
        user_id: UUID,
        start_date: date,
        end_date: date,
    ) -> bool:
        """Check if user has PTO overlap."""
        result = await self.db.execute(
            select(OncallTimeOff)
            .where(
                OncallTimeOff.user_id == user_id,
                OncallTimeOff.status == "approved",
                OncallTimeOff.start_date <= end_date,
                OncallTimeOff.end_date >= start_date,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _get_recent_oncall_hours(
        self,
        user_id: UUID,
        days_back: int = 30,
    ) -> float:
        """Get total on-call hours in recent period."""
        since = datetime.now(timezone.utc) - timedelta(days=days_back)

        result = await self.db.execute(
            select(OncallShift)
            .where(
                OncallShift.user_id == user_id,
                OncallShift.start_time >= since,
            )
        )
        shifts = result.scalars().all()

        total = 0.0
        for shift in shifts:
            hours = (shift.end_time - shift.start_time).total_seconds() / 3600
            total += hours

        return total

    async def _days_since_oncall(self, user_id: UUID) -> int | None:
        """Get days since last on-call."""
        result = await self.db.execute(
            select(OncallShift)
            .where(
                OncallShift.user_id == user_id,
                OncallShift.end_time < datetime.now(timezone.utc),
            )
            .order_by(OncallShift.end_time.desc())
            .limit(1)
        )
        last_shift = result.scalar_one_or_none()

        if not last_shift:
            return None

        delta = datetime.now(timezone.utc) - last_shift.end_time
        return delta.days


async def get_ai_oncall_service(db: AsyncSession) -> AIOncallService:
    """Get AI on-call service instance."""
    return AIOncallService(db)
