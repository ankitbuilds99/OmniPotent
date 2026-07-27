"""API versioning support.

Implements:
- URL-based versioning (/v1/, /v2/)
- Header-based versioning (X-API-Version)
- Version negotiation and deprecation warnings
- Backwards compatibility layer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Callable, Sequence

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("devcopilot.api.versioning")


class APIVersion(Enum):
    """Supported API versions."""

    V1 = "v1"
    V2 = "v2"

    @classmethod
    def from_string(cls, value: str) -> "APIVersion | None":
        """Parse version from string."""
        value = value.lower().strip().lstrip("v")
        for v in cls:
            if v.value.lstrip("v") == value:
                return v
        return None

    @classmethod
    def latest(cls) -> "APIVersion":
        """Get the latest stable version."""
        return cls.V1

    @classmethod
    def all_versions(cls) -> list[str]:
        """Get all version strings."""
        return [v.value for v in cls]


@dataclass
class VersionInfo:
    """Information about an API version."""

    version: APIVersion
    status: str  # stable, deprecated, sunset
    released: date
    sunset_date: date | None = None
    changelog_url: str | None = None


VERSION_INFO: dict[APIVersion, VersionInfo] = {
    APIVersion.V1: VersionInfo(
        version=APIVersion.V1,
        status="stable",
        released=date(2024, 1, 1),
        changelog_url="https://docs.devopscopilot.dev/api/v1/changelog",
    ),
    APIVersion.V2: VersionInfo(
        version=APIVersion.V2,
        status="beta",
        released=date(2024, 6, 1),
        changelog_url="https://docs.devopscopilot.dev/api/v2/changelog",
    ),
}


def get_version_from_request(request: Request) -> APIVersion:
    """Extract API version from request.

    Priority:
    1. URL path prefix (/v1/, /v2/)
    2. X-API-Version header
    3. Accept header (application/vnd.copilot.v1+json)
    4. Default to latest stable
    """
    path = request.url.path

    for version in APIVersion:
        if path.startswith(f"/{version.value}/") or path.startswith(f"/api/{version.value}/"):
            return version

    header_version = request.headers.get("x-api-version")
    if header_version:
        parsed = APIVersion.from_string(header_version)
        if parsed:
            return parsed

    accept = request.headers.get("accept", "")
    if "vnd.copilot." in accept:
        for version in APIVersion:
            if f"vnd.copilot.{version.value}" in accept:
                return version

    return APIVersion.latest()


def version_response_headers(version: APIVersion) -> dict[str, str]:
    """Get response headers for version info."""
    info = VERSION_INFO.get(version)
    headers = {
        "X-API-Version": version.value,
    }

    if info:
        headers["X-API-Status"] = info.status
        if info.sunset_date:
            headers["Sunset"] = info.sunset_date.isoformat()
            headers["Deprecation"] = "true"

    return headers


class VersionedRouter:
    """Factory for creating versioned API routers.

    Usage:
        v1 = VersionedRouter(APIVersion.V1)

        @v1.get("/users")
        async def list_users_v1():
            return {"version": "v1", "users": [...]}

        # Register with app
        app.include_router(v1.router, prefix="/v1")
    """

    def __init__(
        self,
        version: APIVersion,
        prefix: str = "",
        tags: Sequence[str | Enum] | None = None,
    ):
        self.version = version
        router_tags: list[str | Enum] = list(tags) if tags else [version.value]
        self.router = APIRouter(prefix=prefix, tags=router_tags)
        self._deprecated_endpoints: set[str] = set()

    def get(self, path: str, **kwargs: Any) -> Callable:
        """Register a GET endpoint."""
        return self.router.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Callable:
        """Register a POST endpoint."""
        return self.router.post(path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Callable:
        """Register a PUT endpoint."""
        return self.router.put(path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Callable:
        """Register a DELETE endpoint."""
        return self.router.delete(path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Callable:
        """Register a PATCH endpoint."""
        return self.router.patch(path, **kwargs)

    def deprecate(self, path: str, replacement: str | None = None) -> None:
        """Mark an endpoint as deprecated.

        Args:
            path: The endpoint path.
            replacement: Optional replacement endpoint.
        """
        self._deprecated_endpoints.add(path)
        if replacement:
            log.info(
                "Endpoint %s %s deprecated, use %s instead",
                self.version.value,
                path,
                replacement,
            )


def create_versioned_app() -> dict[APIVersion, VersionedRouter]:
    """Create routers for all API versions.

    Returns:
        Dict mapping version to router.
    """
    routers = {}
    for version in APIVersion:
        routers[version] = VersionedRouter(version)
    return routers


async def version_middleware(request: Request, call_next: Callable) -> JSONResponse:
    """Middleware to add version headers to responses."""
    version = get_version_from_request(request)
    response = await call_next(request)

    for key, value in version_response_headers(version).items():
        response.headers[key] = value

    return response


def version_dependency(
    x_api_version: str | None = Header(None, alias="X-API-Version"),
) -> APIVersion:
    """FastAPI dependency for version extraction.

    Usage:
        @app.get("/endpoint")
        async def endpoint(version: APIVersion = Depends(version_dependency)):
            if version == APIVersion.V2:
                return {"new": "response"}
            return {"old": "response"}
    """
    if x_api_version:
        parsed = APIVersion.from_string(x_api_version)
        if parsed:
            return parsed
    return APIVersion.latest()


def get_api_versions_info() -> dict[str, Any]:
    """Get information about all API versions.

    Returns:
        Version info for documentation/discovery.
    """
    return {
        "current": APIVersion.latest().value,
        "supported": APIVersion.all_versions(),
        "versions": {
            v.value: {
                "status": info.status,
                "released": info.released.isoformat(),
                "sunset": info.sunset_date.isoformat() if info.sunset_date else None,
                "changelog": info.changelog_url,
            }
            for v, info in VERSION_INFO.items()
        },
    }
