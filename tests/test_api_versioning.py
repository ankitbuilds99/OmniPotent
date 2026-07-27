"""Tests for API versioning system."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.api.versioning import (
    APIVersion,
    VersionedRouter,
    get_version_from_request,
    version_response_headers,
    version_dependency,
    get_api_versions_info,
)


class TestAPIVersion:
    """Tests for APIVersion enum."""

    def test_from_string_v1(self):
        """Should parse v1 from string."""
        assert APIVersion.from_string("v1") == APIVersion.V1
        assert APIVersion.from_string("V1") == APIVersion.V1
        assert APIVersion.from_string("1") == APIVersion.V1

    def test_from_string_v2(self):
        """Should parse v2 from string."""
        assert APIVersion.from_string("v2") == APIVersion.V2
        assert APIVersion.from_string("V2") == APIVersion.V2

    def test_from_string_invalid(self):
        """Should return None for invalid version."""
        assert APIVersion.from_string("v99") is None
        assert APIVersion.from_string("invalid") is None
        assert APIVersion.from_string("") is None

    def test_latest(self):
        """Should return latest stable version."""
        latest = APIVersion.latest()
        assert latest == APIVersion.V1

    def test_all_versions(self):
        """Should return all version strings."""
        versions = APIVersion.all_versions()
        assert "v1" in versions
        assert "v2" in versions


class TestGetVersionFromRequest:
    """Tests for version extraction from request."""

    def test_url_path_v1(self):
        """Should extract version from URL path."""
        request = MagicMock()
        request.url.path = "/v1/chat"
        request.headers = {}

        version = get_version_from_request(request)
        assert version == APIVersion.V1

    def test_url_path_v2(self):
        """Should extract v2 from URL path."""
        request = MagicMock()
        request.url.path = "/v2/chat"
        request.headers = {}

        version = get_version_from_request(request)
        assert version == APIVersion.V2

    def test_api_prefix_path(self):
        """Should extract version from /api/v1/ path."""
        request = MagicMock()
        request.url.path = "/api/v1/chat"
        request.headers = {}

        version = get_version_from_request(request)
        assert version == APIVersion.V1

    def test_header_version(self):
        """Should extract version from X-API-Version header."""
        request = MagicMock()
        request.url.path = "/chat"
        request.headers = {"x-api-version": "v2"}

        version = get_version_from_request(request)
        assert version == APIVersion.V2

    def test_accept_header_version(self):
        """Should extract version from Accept header."""
        request = MagicMock()
        request.url.path = "/chat"
        request.headers = {
            "accept": "application/vnd.copilot.v2+json",
        }

        version = get_version_from_request(request)
        assert version == APIVersion.V2

    def test_default_to_latest(self):
        """Should default to latest when no version specified."""
        request = MagicMock()
        request.url.path = "/chat"
        request.headers = {}

        version = get_version_from_request(request)
        assert version == APIVersion.latest()


class TestVersionResponseHeaders:
    """Tests for version response headers."""

    def test_includes_version(self):
        """Should include X-API-Version header."""
        headers = version_response_headers(APIVersion.V1)

        assert headers["X-API-Version"] == "v1"

    def test_includes_status(self):
        """Should include X-API-Status header."""
        headers = version_response_headers(APIVersion.V1)

        assert headers["X-API-Status"] == "stable"

    def test_beta_status(self):
        """Should show beta status for v2."""
        headers = version_response_headers(APIVersion.V2)

        assert headers["X-API-Status"] == "beta"


class TestVersionedRouter:
    """Tests for VersionedRouter class."""

    def test_creates_router_with_version_tag(self):
        """Should create router with version tag."""
        router = VersionedRouter(APIVersion.V1)

        assert router.version == APIVersion.V1
        assert "v1" in router.router.tags

    def test_custom_tags(self):
        """Should use custom tags when provided."""
        router = VersionedRouter(APIVersion.V1, tags=["Custom"])

        assert "Custom" in router.router.tags

    def test_deprecate_endpoint(self):
        """Should track deprecated endpoints."""
        router = VersionedRouter(APIVersion.V1)
        router.deprecate("/old", replacement="/new")

        assert "/old" in router._deprecated_endpoints


class TestVersionDependency:
    """Tests for version dependency."""

    def test_parses_header(self):
        """Should parse version from header."""
        version = version_dependency("v2")
        assert version == APIVersion.V2

    def test_defaults_to_latest(self):
        """Should default to latest when header is None."""
        version = version_dependency(None)
        assert version == APIVersion.latest()

    def test_invalid_header_defaults(self):
        """Should default to latest for invalid header."""
        version = version_dependency("invalid")
        assert version == APIVersion.latest()


class TestGetApiVersionsInfo:
    """Tests for API versions info endpoint."""

    def test_includes_current_version(self):
        """Should include current version."""
        info = get_api_versions_info()

        assert info["current"] == "v1"

    def test_includes_supported_versions(self):
        """Should include all supported versions."""
        info = get_api_versions_info()

        assert "v1" in info["supported"]
        assert "v2" in info["supported"]

    def test_includes_version_details(self):
        """Should include version details."""
        info = get_api_versions_info()

        assert "v1" in info["versions"]
        assert info["versions"]["v1"]["status"] == "stable"
        assert "released" in info["versions"]["v1"]
