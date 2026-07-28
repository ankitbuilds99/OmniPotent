"""Session Intelligence with anomaly detection and risk scoring.

Detects suspicious login patterns:
- New device fingerprints
- Unusual locations
- Abnormal login times
- Impossible travel
- Multiple concurrent sessions
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Any

log = logging.getLogger("devcopilot.sso.session_intelligence")


class AnomalyType(Enum):
    """Types of session anomalies."""

    NEW_DEVICE = auto()
    NEW_LOCATION = auto()
    UNUSUAL_TIME = auto()
    IMPOSSIBLE_TRAVEL = auto()
    CONCURRENT_SESSIONS = auto()
    FAILED_ATTEMPTS = auto()
    IP_REPUTATION = auto()
    USER_AGENT_MISMATCH = auto()
    TOR_EXIT_NODE = auto()
    VPN_DETECTED = auto()
    KNOWN_BAD_IP = auto()


class RiskLevel(Enum):
    """Risk levels for session assessment."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendedAction(Enum):
    """Recommended actions based on risk assessment."""

    ALLOW = "allow"
    ALLOW_WITH_LOGGING = "allow_with_logging"
    STEP_UP_AUTH = "step_up_auth"
    REQUIRE_MFA = "require_mfa"
    BLOCK = "block"
    NOTIFY_SECURITY = "notify_security"


@dataclass
class DeviceInfo:
    """Device fingerprint information."""

    user_agent: str
    platform: str | None = None
    browser: str | None = None
    browser_version: str | None = None
    os: str | None = None
    os_version: str | None = None
    device_type: str | None = None
    screen_resolution: str | None = None
    timezone: str | None = None
    language: str | None = None
    canvas_fingerprint: str | None = None
    webgl_fingerprint: str | None = None
    audio_fingerprint: str | None = None

    @property
    def fingerprint_hash(self) -> str:
        """Generate a hash of the device fingerprint."""
        components = [
            self.user_agent,
            self.platform or "",
            self.screen_resolution or "",
            self.timezone or "",
            self.language or "",
            self.canvas_fingerprint or "",
            self.webgl_fingerprint or "",
        ]
        return hashlib.sha256("|".join(components).encode()).hexdigest()[:32]

    def similarity_score(self, other: DeviceInfo) -> float:
        """Calculate similarity score with another device (0-1)."""
        score = 0.0
        total_weight = 0.0

        checks = [
            (self.user_agent, other.user_agent, 3.0),
            (self.platform, other.platform, 2.0),
            (self.browser, other.browser, 2.0),
            (self.os, other.os, 2.0),
            (self.screen_resolution, other.screen_resolution, 1.0),
            (self.timezone, other.timezone, 1.5),
            (self.language, other.language, 1.0),
            (self.canvas_fingerprint, other.canvas_fingerprint, 2.5),
        ]

        for val1, val2, weight in checks:
            if val1 is not None and val2 is not None:
                total_weight += weight
                if val1 == val2:
                    score += weight

        return score / total_weight if total_weight > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_agent": self.user_agent,
            "platform": self.platform,
            "browser": self.browser,
            "browser_version": self.browser_version,
            "os": self.os,
            "os_version": self.os_version,
            "device_type": self.device_type,
            "screen_resolution": self.screen_resolution,
            "timezone": self.timezone,
            "language": self.language,
            "fingerprint_hash": self.fingerprint_hash,
        }


@dataclass
class LocationInfo:
    """Geographic location information."""

    ip_address: str
    country: str | None = None
    country_code: str | None = None
    region: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    isp: str | None = None
    organization: str | None = None
    is_vpn: bool = False
    is_tor: bool = False
    is_proxy: bool = False
    is_hosting: bool = False
    threat_score: float = 0.0

    def distance_km(self, other: LocationInfo) -> float | None:
        """Calculate distance to another location in kilometers."""
        if (
            self.latitude is None
            or self.longitude is None
            or other.latitude is None
            or other.longitude is None
        ):
            return None

        R = 6371.0

        lat1 = math.radians(self.latitude)
        lat2 = math.radians(other.latitude)
        dlat = math.radians(other.latitude - self.latitude)
        dlon = math.radians(other.longitude - self.longitude)

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip_address": self.ip_address,
            "country": self.country,
            "country_code": self.country_code,
            "region": self.region,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "is_vpn": self.is_vpn,
            "is_tor": self.is_tor,
            "threat_score": self.threat_score,
        }


@dataclass
class SessionRiskScore:
    """Risk assessment for a session."""

    score: float
    level: RiskLevel
    anomalies: list[tuple[AnomalyType, float, str]]
    recommended_action: RecommendedAction
    requires_step_up: bool
    device_trusted: bool
    location_trusted: bool
    factors: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "level": self.level.value,
            "anomalies": [
                {
                    "type": a[0].name,
                    "severity": round(a[1], 2),
                    "description": a[2],
                }
                for a in self.anomalies
            ],
            "recommended_action": self.recommended_action.value,
            "requires_step_up": self.requires_step_up,
            "device_trusted": self.device_trusted,
            "location_trusted": self.location_trusted,
            "factors": {k: round(v, 2) for k, v in self.factors.items()},
        }


@dataclass
class UserSessionHistory:
    """Historical session data for a user."""

    user_id: str
    known_devices: list[DeviceInfo] = field(default_factory=list)
    known_locations: list[LocationInfo] = field(default_factory=list)
    login_times: list[datetime] = field(default_factory=list)
    failed_attempts: list[datetime] = field(default_factory=list)
    active_sessions: list[str] = field(default_factory=list)
    trusted_device_hashes: set[str] = field(default_factory=set)
    trusted_ip_ranges: list[str] = field(default_factory=list)
    typical_login_hours: tuple[int, int] = (6, 22)


class SessionIntelligence:
    """Analyzes sessions for security anomalies."""

    def __init__(
        self,
        max_concurrent_sessions: int = 5,
        impossible_travel_speed_kmh: float = 900.0,
        device_trust_threshold: float = 0.8,
        max_failed_attempts: int = 5,
        failed_attempts_window_minutes: int = 30,
    ):
        self.max_concurrent_sessions = max_concurrent_sessions
        self.impossible_travel_speed_kmh = impossible_travel_speed_kmh
        self.device_trust_threshold = device_trust_threshold
        self.max_failed_attempts = max_failed_attempts
        self.failed_attempts_window = timedelta(minutes=failed_attempts_window_minutes)
        self._user_histories: dict[str, UserSessionHistory] = {}
        self._bad_ip_list: set[str] = set()
        self._tor_exit_nodes: set[str] = set()

    def get_user_history(self, user_id: str) -> UserSessionHistory:
        """Get or create user session history."""
        if user_id not in self._user_histories:
            self._user_histories[user_id] = UserSessionHistory(user_id=user_id)
        return self._user_histories[user_id]

    def assess_risk(
        self,
        user_id: str,
        device: DeviceInfo,
        location: LocationInfo,
        timestamp: datetime | None = None,
    ) -> SessionRiskScore:
        """Assess risk for a new session."""
        timestamp = timestamp or datetime.now(timezone.utc)
        history = self.get_user_history(user_id)
        anomalies: list[tuple[AnomalyType, float, str]] = []
        factors: dict[str, float] = {}

        device_score, device_trusted = self._assess_device(device, history)
        factors["device"] = device_score
        if not device_trusted:
            anomalies.append((
                AnomalyType.NEW_DEVICE,
                device_score,
                f"Unrecognized device: {device.browser} on {device.os}",
            ))

        location_score, location_trusted = self._assess_location(location, history)
        factors["location"] = location_score
        if not location_trusted:
            anomalies.append((
                AnomalyType.NEW_LOCATION,
                location_score,
                f"New location: {location.city}, {location.country}",
            ))

        time_score = self._assess_time(timestamp, history)
        factors["time"] = time_score
        if time_score > 0.3:
            local_hour = timestamp.hour
            anomalies.append((
                AnomalyType.UNUSUAL_TIME,
                time_score,
                f"Unusual login time: {local_hour}:00 local",
            ))

        travel_score = self._assess_travel(location, timestamp, history)
        factors["travel"] = travel_score
        if travel_score > 0.5:
            anomalies.append((
                AnomalyType.IMPOSSIBLE_TRAVEL,
                travel_score,
                "Impossible travel detected from previous location",
            ))

        concurrent_score = self._assess_concurrent_sessions(history)
        factors["concurrent"] = concurrent_score
        if concurrent_score > 0.5:
            anomalies.append((
                AnomalyType.CONCURRENT_SESSIONS,
                concurrent_score,
                f"Multiple active sessions: {len(history.active_sessions)}",
            ))

        failed_score = self._assess_failed_attempts(timestamp, history)
        factors["failed_attempts"] = failed_score
        if failed_score > 0.3:
            anomalies.append((
                AnomalyType.FAILED_ATTEMPTS,
                failed_score,
                f"Recent failed login attempts detected",
            ))

        ip_score = self._assess_ip_reputation(location)
        factors["ip_reputation"] = ip_score
        if location.is_tor:
            anomalies.append((
                AnomalyType.TOR_EXIT_NODE,
                0.8,
                "Login from Tor exit node",
            ))
        if location.is_vpn:
            anomalies.append((
                AnomalyType.VPN_DETECTED,
                0.3,
                "VPN detected",
            ))
        if location.ip_address in self._bad_ip_list:
            anomalies.append((
                AnomalyType.KNOWN_BAD_IP,
                0.9,
                "Known malicious IP address",
            ))

        weights = {
            "device": 2.0,
            "location": 1.5,
            "time": 0.8,
            "travel": 2.5,
            "concurrent": 1.0,
            "failed_attempts": 1.5,
            "ip_reputation": 2.0,
        }

        total_score = sum(factors[k] * weights.get(k, 1.0) for k in factors)
        max_score = sum(weights.values())
        normalized_score = (total_score / max_score) * 10

        if normalized_score < 2.0:
            level = RiskLevel.LOW
        elif normalized_score < 4.0:
            level = RiskLevel.MEDIUM
        elif normalized_score < 7.0:
            level = RiskLevel.HIGH
        else:
            level = RiskLevel.CRITICAL

        if level == RiskLevel.LOW:
            action = RecommendedAction.ALLOW if device_trusted else RecommendedAction.ALLOW_WITH_LOGGING
        elif level == RiskLevel.MEDIUM:
            action = RecommendedAction.STEP_UP_AUTH
        elif level == RiskLevel.HIGH:
            action = RecommendedAction.REQUIRE_MFA
        else:
            action = RecommendedAction.BLOCK

        requires_step_up = level in (RiskLevel.MEDIUM, RiskLevel.HIGH)

        return SessionRiskScore(
            score=normalized_score,
            level=level,
            anomalies=anomalies,
            recommended_action=action,
            requires_step_up=requires_step_up,
            device_trusted=device_trusted,
            location_trusted=location_trusted,
            factors=factors,
        )

    def _assess_device(
        self,
        device: DeviceInfo,
        history: UserSessionHistory,
    ) -> tuple[float, bool]:
        """Assess device risk and trust status."""
        if device.fingerprint_hash in history.trusted_device_hashes:
            return 0.0, True

        if not history.known_devices:
            return 0.5, False

        max_similarity = 0.0
        for known in history.known_devices:
            similarity = device.similarity_score(known)
            max_similarity = max(max_similarity, similarity)

        if max_similarity >= self.device_trust_threshold:
            return 0.1, True

        return 1.0 - max_similarity, False

    def _assess_location(
        self,
        location: LocationInfo,
        history: UserSessionHistory,
    ) -> tuple[float, bool]:
        """Assess location risk and trust status."""
        if not history.known_locations:
            return 0.3, False

        for known in history.known_locations:
            if known.country_code == location.country_code:
                if known.city == location.city:
                    return 0.0, True
                return 0.1, True

        return 0.6, False

    def _assess_time(
        self,
        timestamp: datetime,
        history: UserSessionHistory,
    ) -> float:
        """Assess if login time is unusual."""
        hour = timestamp.hour
        start, end = history.typical_login_hours

        if start <= end:
            if start <= hour <= end:
                return 0.0
        else:
            if hour >= start or hour <= end:
                return 0.0

        if start <= end:
            distance = min(abs(hour - start), abs(hour - end))
        else:
            distance = min(
                abs(hour - start) if hour < start else 0,
                abs(hour - end) if hour > end else 0,
                24 - abs(hour - start),
                24 - abs(hour - end),
            )

        return min(distance / 6.0, 1.0)

    def _assess_travel(
        self,
        location: LocationInfo,
        timestamp: datetime,
        history: UserSessionHistory,
    ) -> float:
        """Check for impossible travel patterns."""
        if not history.known_locations or not history.login_times:
            return 0.0

        last_location = history.known_locations[-1]
        last_time = history.login_times[-1]

        distance = location.distance_km(last_location)
        if distance is None:
            return 0.0

        time_diff = (timestamp - last_time).total_seconds() / 3600
        if time_diff <= 0:
            return 0.8

        required_speed = distance / time_diff

        if required_speed > self.impossible_travel_speed_kmh:
            return min(required_speed / (self.impossible_travel_speed_kmh * 2), 1.0)

        return 0.0

    def _assess_concurrent_sessions(self, history: UserSessionHistory) -> float:
        """Assess concurrent session risk."""
        active = len(history.active_sessions)
        if active >= self.max_concurrent_sessions:
            return 1.0
        if active > self.max_concurrent_sessions // 2:
            return 0.5
        return 0.0

    def _assess_failed_attempts(
        self,
        timestamp: datetime,
        history: UserSessionHistory,
    ) -> float:
        """Assess recent failed login attempts."""
        cutoff = timestamp - self.failed_attempts_window
        recent_failures = [t for t in history.failed_attempts if t > cutoff]

        if len(recent_failures) >= self.max_failed_attempts:
            return 1.0
        if len(recent_failures) > 0:
            return len(recent_failures) / self.max_failed_attempts

        return 0.0

    def _assess_ip_reputation(self, location: LocationInfo) -> float:
        """Assess IP reputation risk."""
        score = location.threat_score

        if location.is_tor:
            score = max(score, 0.8)
        if location.is_proxy:
            score = max(score, 0.5)
        if location.is_hosting:
            score = max(score, 0.4)
        if location.ip_address in self._bad_ip_list:
            score = max(score, 0.9)

        return score

    def record_login(
        self,
        user_id: str,
        session_id: str,
        device: DeviceInfo,
        location: LocationInfo,
        timestamp: datetime | None = None,
    ) -> None:
        """Record a successful login."""
        timestamp = timestamp or datetime.now(timezone.utc)
        history = self.get_user_history(user_id)

        if device.fingerprint_hash not in [d.fingerprint_hash for d in history.known_devices]:
            history.known_devices.append(device)
            if len(history.known_devices) > 10:
                history.known_devices = history.known_devices[-10:]

        if location.city not in [l.city for l in history.known_locations]:
            history.known_locations.append(location)
            if len(history.known_locations) > 20:
                history.known_locations = history.known_locations[-20:]

        history.login_times.append(timestamp)
        if len(history.login_times) > 100:
            history.login_times = history.login_times[-100:]

        if session_id not in history.active_sessions:
            history.active_sessions.append(session_id)

    def record_failed_attempt(
        self,
        user_id: str,
        timestamp: datetime | None = None,
    ) -> None:
        """Record a failed login attempt."""
        timestamp = timestamp or datetime.now(timezone.utc)
        history = self.get_user_history(user_id)
        history.failed_attempts.append(timestamp)

        cutoff = timestamp - timedelta(hours=24)
        history.failed_attempts = [t for t in history.failed_attempts if t > cutoff]

    def trust_device(self, user_id: str, device: DeviceInfo) -> None:
        """Mark a device as trusted."""
        history = self.get_user_history(user_id)
        history.trusted_device_hashes.add(device.fingerprint_hash)

    def end_session(self, user_id: str, session_id: str) -> None:
        """End a session."""
        history = self.get_user_history(user_id)
        if session_id in history.active_sessions:
            history.active_sessions.remove(session_id)

    def add_bad_ip(self, ip: str) -> None:
        """Add an IP to the blocklist."""
        self._bad_ip_list.add(ip)

    def remove_bad_ip(self, ip: str) -> None:
        """Remove an IP from the blocklist."""
        self._bad_ip_list.discard(ip)


_intelligence: SessionIntelligence | None = None


def get_session_intelligence() -> SessionIntelligence:
    """Get the default session intelligence instance."""
    global _intelligence
    if _intelligence is None:
        _intelligence = SessionIntelligence()
    return _intelligence
