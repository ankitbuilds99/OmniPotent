"""Base classes for external service connectors."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("omnipotent.connectors.base")


class ConnectorError(Exception):
    """Base exception for connector errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ConnectorConfig:
    """Base configuration for connectors."""
    url: str
    api_key: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: float = 30.0
    verify_ssl: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryResult:
    """Result from a connector query."""
    success: bool
    data: Any
    error: str | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


class Connector(ABC):
    """Base class for external service connectors."""

    def __init__(self, config: ConnectorConfig):
        """Initialize the connector.

        Args:
            config: Connector configuration
        """
        self.config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.url,
                timeout=self.config.timeout_seconds,
                verify=self.config.verify_ssl,
                headers=self._default_headers(),
            )
        return self._client

    @abstractmethod
    def _default_headers(self) -> dict[str, str]:
        """Return default headers for requests."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the service is reachable and healthy."""
        pass

    @property
    @abstractmethod
    def connector_type(self) -> str:
        """Return the connector type name."""
        pass

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an HTTP request with error handling."""
        try:
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            raise ConnectorError(
                f"{self.connector_type} API error: {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise ConnectorError(f"{self.connector_type} connection error: {e}") from e

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, **kwargs) -> httpx.Response:
        return await self._request("POST", path, **kwargs)
