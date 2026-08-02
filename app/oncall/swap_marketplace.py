"""On-call swap marketplace for coverage trading.

Enables engineers to:
- Request coverage for their shifts
- Offer to cover shifts
- Track swap history and "debt"
- Find volunteers automatically
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("omnipotent.oncall.swap")


class SwapStatus(Enum):
    """Status of a swap request."""

    PENDING = "pending"
    OFFERED = "offered"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXPIRED = "expired"


@dataclass
class SwapRequest:
    """A request to swap on-call coverage."""

    request_id: str
    requester_id: str
    requester_name: str
    shift_start: datetime
    shift_end: datetime
    reason: str
    status: SwapStatus = SwapStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    accepted_by: str | None = None
    accepted_at: datetime | None = None
    urgency: str = "normal"
    broadcast_to_team: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "requester_id": self.requester_id,
            "requester_name": self.requester_name,
            "shift_start": self.shift_start.isoformat(),
            "shift_end": self.shift_end.isoformat(),
            "duration_hours": (self.shift_end - self.shift_start).total_seconds() / 3600,
            "reason": self.reason,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "accepted_by": self.accepted_by,
            "urgency": self.urgency,
        }


@dataclass
class SwapOffer:
    """An offer to cover a swap request."""

    offer_id: str
    request_id: str
    offerer_id: str
    offerer_name: str
    message: str | None = None
    offered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: SwapStatus = SwapStatus.OFFERED
    swap_debt: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "request_id": self.request_id,
            "offerer_id": self.offerer_id,
            "offerer_name": self.offerer_name,
            "message": self.message,
            "offered_at": self.offered_at.isoformat(),
            "status": self.status.value,
            "swap_debt": round(self.swap_debt, 2),
        }


@dataclass
class EngineerSwapProfile:
    """Swap profile for an engineer."""

    engineer_id: str
    name: str
    swap_debt: float = 0.0
    swaps_given: int = 0
    swaps_received: int = 0
    hours_given: float = 0.0
    hours_received: float = 0.0
    availability_preferences: dict[str, bool] = field(default_factory=dict)
    auto_accept: bool = False

    @property
    def net_hours(self) -> float:
        return self.hours_given - self.hours_received


class SwapMarketplace:
    """Marketplace for on-call swap requests."""

    def __init__(
        self,
        auto_match: bool = True,
        max_swap_debt_hours: float = 24.0,
    ):
        self.auto_match = auto_match
        self.max_swap_debt_hours = max_swap_debt_hours

        self._requests: dict[str, SwapRequest] = {}
        self._offers: dict[str, list[SwapOffer]] = {}
        self._profiles: dict[str, EngineerSwapProfile] = {}
        self._request_counter = 0
        self._offer_counter = 0
        self._notification_handlers: list[Any] = []

    def get_profile(self, engineer_id: str) -> EngineerSwapProfile:
        """Get or create an engineer's swap profile."""
        if engineer_id not in self._profiles:
            self._profiles[engineer_id] = EngineerSwapProfile(
                engineer_id=engineer_id,
                name=f"Engineer {engineer_id}",
            )
        return self._profiles[engineer_id]

    def create_request(
        self,
        requester_id: str,
        requester_name: str,
        shift_start: datetime,
        shift_end: datetime,
        reason: str,
        urgency: str = "normal",
        expires_hours: int = 48,
    ) -> SwapRequest:
        """Create a swap request."""
        self._request_counter += 1
        request_id = f"swap_{self._request_counter}"

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=expires_hours)

        request = SwapRequest(
            request_id=request_id,
            requester_id=requester_id,
            requester_name=requester_name,
            shift_start=shift_start,
            shift_end=shift_end,
            reason=reason,
            expires_at=expires_at,
            urgency=urgency,
        )

        self._requests[request_id] = request
        self._offers[request_id] = []

        profile = self.get_profile(requester_id)
        profile.name = requester_name

        log.info(
            "swap request created",
            extra={
                "request_id": request_id,
                "requester": requester_id,
                "shift_start": shift_start.isoformat(),
            },
        )

        self._notify_new_request(request)

        if self.auto_match:
            self._auto_find_volunteers(request)

        return request

    def make_offer(
        self,
        request_id: str,
        offerer_id: str,
        offerer_name: str,
        message: str | None = None,
    ) -> SwapOffer | None:
        """Make an offer to cover a swap request."""
        request = self._requests.get(request_id)
        if not request or request.status != SwapStatus.PENDING:
            return None

        profile = self.get_profile(offerer_id)
        profile.name = offerer_name

        self._offer_counter += 1
        offer_id = f"offer_{self._offer_counter}"

        offer = SwapOffer(
            offer_id=offer_id,
            request_id=request_id,
            offerer_id=offerer_id,
            offerer_name=offerer_name,
            message=message,
            swap_debt=profile.swap_debt,
        )

        self._offers[request_id].append(offer)
        request.status = SwapStatus.OFFERED

        log.info(
            "swap offer made",
            extra={
                "offer_id": offer_id,
                "request_id": request_id,
                "offerer": offerer_id,
            },
        )

        self._notify_new_offer(request, offer)

        return offer

    def accept_offer(
        self,
        request_id: str,
        offer_id: str,
    ) -> bool:
        """Accept a swap offer."""
        request = self._requests.get(request_id)
        if not request:
            return False

        offers = self._offers.get(request_id, [])
        offer = next((o for o in offers if o.offer_id == offer_id), None)
        if not offer:
            return False

        request.status = SwapStatus.ACCEPTED
        request.accepted_by = offer.offerer_id
        request.accepted_at = datetime.now(timezone.utc)

        offer.status = SwapStatus.ACCEPTED

        for other in offers:
            if other.offer_id != offer_id:
                other.status = SwapStatus.DECLINED

        hours = (request.shift_end - request.shift_start).total_seconds() / 3600
        self._update_swap_debt(request.requester_id, offer.offerer_id, hours)

        log.info(
            "swap accepted",
            extra={
                "request_id": request_id,
                "offer_id": offer_id,
                "hours": hours,
            },
        )

        self._notify_swap_accepted(request, offer)

        return True

    def decline_offer(self, request_id: str, offer_id: str) -> bool:
        """Decline a swap offer."""
        offers = self._offers.get(request_id, [])
        offer = next((o for o in offers if o.offer_id == offer_id), None)
        if offer:
            offer.status = SwapStatus.DECLINED
            return True
        return False

    def cancel_request(self, request_id: str) -> bool:
        """Cancel a swap request."""
        request = self._requests.get(request_id)
        if request and request.status in (SwapStatus.PENDING, SwapStatus.OFFERED):
            request.status = SwapStatus.CANCELLED
            return True
        return False

    def complete_swap(self, request_id: str) -> bool:
        """Mark a swap as completed."""
        request = self._requests.get(request_id)
        if request and request.status == SwapStatus.ACCEPTED:
            request.status = SwapStatus.COMPLETED
            return True
        return False

    def _update_swap_debt(
        self,
        requester_id: str,
        offerer_id: str,
        hours: float,
    ) -> None:
        """Update swap debt between engineers."""
        requester_profile = self.get_profile(requester_id)
        offerer_profile = self.get_profile(offerer_id)

        requester_profile.swap_debt += hours
        requester_profile.hours_received += hours
        requester_profile.swaps_received += 1

        offerer_profile.swap_debt -= hours
        offerer_profile.hours_given += hours
        offerer_profile.swaps_given += 1

    def get_requests(
        self,
        status: SwapStatus | None = None,
        requester_id: str | None = None,
    ) -> list[SwapRequest]:
        """Get swap requests with optional filtering."""
        requests = list(self._requests.values())

        if status:
            requests = [r for r in requests if r.status == status]

        if requester_id:
            requests = [r for r in requests if r.requester_id == requester_id]

        return sorted(requests, key=lambda r: r.created_at, reverse=True)

    def get_offers(self, request_id: str) -> list[SwapOffer]:
        """Get offers for a swap request."""
        return self._offers.get(request_id, [])

    def get_available_swaps(self, engineer_id: str) -> list[SwapRequest]:
        """Get swap requests available for an engineer to take."""
        requests = self.get_requests(status=SwapStatus.PENDING)
        profile = self.get_profile(engineer_id)

        available = []
        for req in requests:
            if req.requester_id == engineer_id:
                continue

            existing_offer = any(
                o.offerer_id == engineer_id
                for o in self._offers.get(req.request_id, [])
            )
            if existing_offer:
                continue

            hours = (req.shift_end - req.shift_start).total_seconds() / 3600
            if profile.swap_debt + hours > self.max_swap_debt_hours:
                continue

            available.append(req)

        return available

    def _auto_find_volunteers(self, request: SwapRequest) -> list[str]:
        """Automatically find volunteers for a swap request."""
        volunteers = []
        hours = (request.shift_end - request.shift_start).total_seconds() / 3600

        for eng_id, profile in self._profiles.items():
            if eng_id == request.requester_id:
                continue

            if profile.swap_debt < 0:
                volunteers.append(eng_id)
                continue

            if profile.net_hours > hours:
                volunteers.append(eng_id)

        return volunteers[:5]

    def get_leaderboard(self) -> list[dict[str, Any]]:
        """Get swap leaderboard (most helpful engineers)."""
        profiles = list(self._profiles.values())
        profiles.sort(key=lambda p: p.hours_given, reverse=True)

        return [
            {
                "engineer_id": p.engineer_id,
                "name": p.name,
                "hours_given": round(p.hours_given, 1),
                "hours_received": round(p.hours_received, 1),
                "net_hours": round(p.net_hours, 1),
                "swaps_given": p.swaps_given,
                "swaps_received": p.swaps_received,
                "swap_debt": round(p.swap_debt, 1),
            }
            for p in profiles[:10]
        ]

    def get_dashboard_data(self) -> dict[str, Any]:
        """Get swap marketplace dashboard data."""
        pending = self.get_requests(status=SwapStatus.PENDING)
        recent = self.get_requests()[:10]

        return {
            "pending_requests": len(pending),
            "total_requests": len(self._requests),
            "pending": [r.to_dict() for r in pending[:5]],
            "recent": [r.to_dict() for r in recent],
            "leaderboard": self.get_leaderboard()[:5],
        }

    def _notify_new_request(self, request: SwapRequest) -> None:
        """Notify about new swap request."""
        for handler in self._notification_handlers:
            try:
                handler("new_request", request.to_dict())
            except Exception as e:
                log.error("notification failed", extra={"error": str(e)})

    def _notify_new_offer(self, request: SwapRequest, offer: SwapOffer) -> None:
        """Notify about new offer."""
        for handler in self._notification_handlers:
            try:
                handler("new_offer", {
                    "request": request.to_dict(),
                    "offer": offer.to_dict(),
                })
            except Exception as e:
                log.error("notification failed", extra={"error": str(e)})

    def _notify_swap_accepted(self, request: SwapRequest, offer: SwapOffer) -> None:
        """Notify about accepted swap."""
        for handler in self._notification_handlers:
            try:
                handler("swap_accepted", {
                    "request": request.to_dict(),
                    "offer": offer.to_dict(),
                })
            except Exception as e:
                log.error("notification failed", extra={"error": str(e)})

    def add_notification_handler(self, handler: Any) -> None:
        """Add a notification handler."""
        self._notification_handlers.append(handler)


_marketplace: SwapMarketplace | None = None


def get_swap_marketplace() -> SwapMarketplace:
    """Get the default swap marketplace."""
    global _marketplace
    if _marketplace is None:
        _marketplace = SwapMarketplace()
    return _marketplace
