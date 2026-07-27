"""Push notification providers for mobile notifications.

Supports:
- Firebase Cloud Messaging (FCM) for Android and web
- Apple Push Notification service (APNs) for iOS
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("devcopilot.notifications.push")


@dataclass
class PushMessage:
    """Push notification message structure."""

    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    badge: int | None = None
    sound: str = "default"
    image_url: str | None = None
    action_url: str | None = None
    priority: str = "high"
    ttl: int = 86400


@dataclass
class DeviceToken:
    """Registered device for push notifications."""

    token: str
    platform: str
    user_id: str
    device_name: str = ""
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PushProvider(ABC):
    """Abstract push notification provider interface."""

    @abstractmethod
    async def send(self, token: str, message: PushMessage) -> bool:
        """Send a push notification to a device."""
        pass

    @abstractmethod
    async def send_multicast(self, tokens: list[str], message: PushMessage) -> dict[str, bool]:
        """Send a push notification to multiple devices."""
        pass

    @abstractmethod
    def is_configured(self) -> bool:
        """Check if the provider is properly configured."""
        pass


class FCMProvider(PushProvider):
    """Firebase Cloud Messaging provider."""

    def __init__(
        self,
        credentials_path: str = "",
        project_id: str = "",
    ):
        self.credentials_path = credentials_path or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", ""
        )
        self.project_id = project_id or os.environ.get("FCM_PROJECT_ID", "")
        self._access_token: str | None = None
        self._token_expiry: datetime | None = None

    def is_configured(self) -> bool:
        return bool(self.credentials_path and self.project_id)

    async def _get_access_token(self) -> str | None:
        """Get OAuth2 access token for FCM API."""
        if self._access_token and self._token_expiry:
            if datetime.now(timezone.utc) < self._token_expiry:
                return self._access_token

        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=["https://www.googleapis.com/auth/firebase.messaging"],
            )
            creds.refresh(Request())
            self._access_token = creds.token
            self._token_expiry = creds.expiry
            return self._access_token
        except Exception as e:
            log.error("Failed to get FCM access token: %s", e)
            return None

    async def send(self, token: str, message: PushMessage) -> bool:
        if not self.is_configured():
            log.warning("FCM not configured")
            return False

        access_token = await self._get_access_token()
        if not access_token:
            return False

        try:
            import httpx

            payload = {
                "message": {
                    "token": token,
                    "notification": {
                        "title": message.title,
                        "body": message.body,
                    },
                    "data": {k: str(v) for k, v in message.data.items()},
                    "android": {
                        "priority": message.priority,
                        "notification": {
                            "sound": message.sound,
                            "click_action": message.action_url or "OPEN_APP",
                        },
                    },
                    "webpush": {
                        "notification": {
                            "icon": "/icon-192.png",
                        },
                    },
                }
            }

            if message.image_url:
                payload["message"]["notification"]["image"] = message.image_url

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=30,
                )

            if resp.status_code == 200:
                log.info("FCM notification sent to %s", token[:20])
                return True
            else:
                log.error("FCM error: %s", resp.text)
                return False
        except Exception as e:
            log.error("FCM send failed: %s", e)
            return False

    async def send_multicast(
        self, tokens: list[str], message: PushMessage
    ) -> dict[str, bool]:
        results = {}
        for token in tokens:
            results[token] = await self.send(token, message)
        return results


class APNsProvider(PushProvider):
    """Apple Push Notification service provider."""

    def __init__(
        self,
        key_id: str = "",
        team_id: str = "",
        key_path: str = "",
        bundle_id: str = "",
        production: bool = False,
    ):
        self.key_id = key_id or os.environ.get("APNS_KEY_ID", "")
        self.team_id = team_id or os.environ.get("APNS_TEAM_ID", "")
        self.key_path = key_path or os.environ.get("APNS_KEY_PATH", "")
        self.bundle_id = bundle_id or os.environ.get("APNS_BUNDLE_ID", "")
        self.production = production or os.environ.get("APNS_PRODUCTION", "").lower() == "true"
        self._jwt_token: str | None = None
        self._jwt_expiry: datetime | None = None

    def is_configured(self) -> bool:
        return bool(
            self.key_id and self.team_id and self.key_path and self.bundle_id
        )

    def _get_apns_host(self) -> str:
        if self.production:
            return "api.push.apple.com"
        return "api.sandbox.push.apple.com"

    async def _get_jwt_token(self) -> str | None:
        """Generate JWT token for APNs."""
        if self._jwt_token and self._jwt_expiry:
            if datetime.now(timezone.utc) < self._jwt_expiry:
                return self._jwt_token

        try:
            import jwt
            from datetime import timedelta

            with open(self.key_path, "rb") as f:
                key = f.read()

            now = datetime.now(timezone.utc)
            payload = {
                "iss": self.team_id,
                "iat": int(now.timestamp()),
            }

            token = jwt.encode(
                payload,
                key,
                algorithm="ES256",
                headers={"kid": self.key_id},
            )

            self._jwt_token = token
            self._jwt_expiry = now + timedelta(minutes=50)
            return token
        except Exception as e:
            log.error("Failed to generate APNs JWT: %s", e)
            return None

    async def send(self, token: str, message: PushMessage) -> bool:
        if not self.is_configured():
            log.warning("APNs not configured")
            return False

        jwt_token = await self._get_jwt_token()
        if not jwt_token:
            return False

        try:
            import httpx

            payload = {
                "aps": {
                    "alert": {
                        "title": message.title,
                        "body": message.body,
                    },
                    "sound": message.sound,
                    "badge": message.badge,
                }
            }

            if message.action_url:
                payload["url"] = message.action_url

            payload.update(message.data)

            async with httpx.AsyncClient(http2=True) as client:
                resp = await client.post(
                    f"https://{self._get_apns_host()}/3/device/{token}",
                    headers={
                        "authorization": f"bearer {jwt_token}",
                        "apns-topic": self.bundle_id,
                        "apns-priority": "10" if message.priority == "high" else "5",
                        "apns-expiration": str(int(datetime.now(timezone.utc).timestamp()) + message.ttl),
                    },
                    json=payload,
                    timeout=30,
                )

            if resp.status_code == 200:
                log.info("APNs notification sent to %s", token[:20])
                return True
            else:
                log.error("APNs error: %s %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            log.error("APNs send failed: %s", e)
            return False

    async def send_multicast(
        self, tokens: list[str], message: PushMessage
    ) -> dict[str, bool]:
        results = {}
        for token in tokens:
            results[token] = await self.send(token, message)
        return results


_DEVICE_REGISTRY: dict[str, DeviceToken] = {}
_USER_DEVICES: dict[str, list[str]] = {}

_fcm_provider: FCMProvider | None = None
_apns_provider: APNsProvider | None = None


def get_push_provider(platform: str = "android") -> PushProvider | None:
    """Get push provider for a platform."""
    global _fcm_provider, _apns_provider

    if platform in ("android", "web"):
        if _fcm_provider is None:
            _fcm_provider = FCMProvider()
        return _fcm_provider if _fcm_provider.is_configured() else None
    elif platform == "ios":
        if _apns_provider is None:
            _apns_provider = APNsProvider()
        return _apns_provider if _apns_provider.is_configured() else None

    return None


def register_device(
    user_id: str,
    token: str,
    platform: str,
    device_name: str = "",
) -> DeviceToken:
    """Register a device for push notifications."""
    device = DeviceToken(
        token=token,
        platform=platform,
        user_id=user_id,
        device_name=device_name,
    )

    _DEVICE_REGISTRY[token] = device

    if user_id not in _USER_DEVICES:
        _USER_DEVICES[user_id] = []
    if token not in _USER_DEVICES[user_id]:
        _USER_DEVICES[user_id].append(token)

    log.info("Registered device %s for user %s", device_name or token[:20], user_id)
    return device


def unregister_device(token: str) -> bool:
    """Unregister a device from push notifications."""
    if token not in _DEVICE_REGISTRY:
        return False

    device = _DEVICE_REGISTRY.pop(token)

    if device.user_id in _USER_DEVICES:
        if token in _USER_DEVICES[device.user_id]:
            _USER_DEVICES[device.user_id].remove(token)

    log.info("Unregistered device %s", token[:20])
    return True


def get_user_devices(user_id: str) -> list[DeviceToken]:
    """Get all registered devices for a user."""
    tokens = _USER_DEVICES.get(user_id, [])
    return [_DEVICE_REGISTRY[t] for t in tokens if t in _DEVICE_REGISTRY]


async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, bool]:
    """Send push notification to all user's devices."""
    devices = get_user_devices(user_id)
    if not devices:
        log.debug("No devices registered for user %s", user_id)
        return {}

    message = PushMessage(
        title=title,
        body=body,
        data=data or {},
        **kwargs,
    )

    results: dict[str, bool] = {}

    for device in devices:
        provider = get_push_provider(device.platform)
        if provider:
            success = await provider.send(device.token, message)
            results[device.token] = success
            if success:
                device.last_used = datetime.now(timezone.utc)
        else:
            results[device.token] = False

    return results


async def send_alert_push(
    user_id: str,
    alert_name: str,
    severity: str,
    message: str,
    investigation_id: str = "",
) -> dict[str, bool]:
    """Send alert push notification."""
    return await send_push_notification(
        user_id=user_id,
        title=f"[{severity.upper()}] {alert_name}",
        body=message[:200],
        data={
            "type": "alert",
            "alert_name": alert_name,
            "severity": severity,
            "investigation_id": investigation_id,
        },
        priority="high",
    )


async def send_investigation_push(
    user_id: str,
    investigation_id: str,
    status: str,
    summary: str,
) -> dict[str, bool]:
    """Send investigation status push notification."""
    return await send_push_notification(
        user_id=user_id,
        title=f"Investigation {status}",
        body=summary[:200],
        data={
            "type": "investigation",
            "investigation_id": investigation_id,
            "status": status,
        },
    )


async def send_approval_push(
    user_id: str,
    action: str,
    requester: str,
    approval_id: str,
) -> dict[str, bool]:
    """Send approval request push notification."""
    return await send_push_notification(
        user_id=user_id,
        title="Approval Required",
        body=f"{requester} requests approval for: {action}",
        data={
            "type": "approval",
            "approval_id": approval_id,
            "action": action,
        },
        priority="high",
    )
