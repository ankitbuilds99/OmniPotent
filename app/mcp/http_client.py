"""HTTP client utilities for MCP servers with retry and error handling.

Provides a resilient HTTP client wrapper for MCP servers that:
- Retries failed requests with exponential backoff
- Handles rate limiting (429) gracefully
- Provides consistent error messages for agent context
- Logs failures for debugging
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("devcopilot.mcp.http")

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_FACTOR = 2.0

RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class MCPHTTPError(Exception):
    """HTTP error with context for agent consumption."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        url: str = "",
        retries_attempted: int = 0,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.retries_attempted = retries_attempted


def _should_retry(status_code: int) -> bool:
    """Check if a status code should trigger a retry."""
    return status_code in RETRIABLE_STATUS_CODES


def _get_retry_after(headers: httpx.Headers) -> float | None:
    """Extract Retry-After header value if present."""
    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return None


def http_get(
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> httpx.Response:
    """Make a GET request with retry logic.

    Args:
        url: Request URL
        headers: Request headers
        params: Query parameters
        timeout: Request timeout in seconds
        max_retries: Maximum number of retry attempts
        backoff_factor: Multiplier for exponential backoff

    Returns:
        httpx.Response on success

    Raises:
        MCPHTTPError: On persistent failure after retries
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.get(
                url,
                headers=headers,
                params=params,
                timeout=timeout,
            )

            if resp.status_code < 400:
                return resp

            if resp.status_code == 429:
                retry_after = _get_retry_after(resp.headers) or (backoff_factor ** attempt)
                if attempt < max_retries:
                    log.warning(
                        "Rate limited (429) on %s, retrying in %.1fs (attempt %d/%d)",
                        url,
                        retry_after,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(retry_after)
                    continue

            if _should_retry(resp.status_code) and attempt < max_retries:
                delay = backoff_factor ** attempt
                log.warning(
                    "HTTP %d on %s, retrying in %.1fs (attempt %d/%d)",
                    resp.status_code,
                    url,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()

        except httpx.TimeoutException as e:
            last_error = e
            if attempt < max_retries:
                delay = backoff_factor ** attempt
                log.warning(
                    "Timeout on %s, retrying in %.1fs (attempt %d/%d)",
                    url,
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue

        except httpx.HTTPStatusError as e:
            raise MCPHTTPError(
                f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                status_code=e.response.status_code,
                url=url,
                retries_attempted=attempt,
            ) from e

        except httpx.RequestError as e:
            last_error = e
            if attempt < max_retries:
                delay = backoff_factor ** attempt
                log.warning(
                    "Request error on %s: %s, retrying in %.1fs (attempt %d/%d)",
                    url,
                    str(e)[:100],
                    delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(delay)
                continue

    if last_error:
        raise MCPHTTPError(
            f"Request failed after {max_retries} retries: {last_error}",
            url=url,
            retries_attempted=max_retries,
        ) from last_error

    raise MCPHTTPError(
        f"Request failed after {max_retries} retries",
        url=url,
        retries_attempted=max_retries,
    )


def http_post(
    url: str,
    json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> httpx.Response:
    """Make a POST request with retry logic.

    Note: Only retries on connection/timeout errors, not on 4xx responses
    (which indicate client errors that won't change on retry).
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(
                url,
                json=json,
                data=data,
                headers=headers,
                timeout=timeout,
            )

            if resp.status_code < 400:
                return resp

            if resp.status_code == 429:
                retry_after = _get_retry_after(resp.headers) or (backoff_factor ** attempt)
                if attempt < max_retries:
                    log.warning(
                        "Rate limited (429) on POST %s, retrying in %.1fs",
                        url,
                        retry_after,
                    )
                    time.sleep(retry_after)
                    continue

            if resp.status_code >= 500 and attempt < max_retries:
                delay = backoff_factor ** attempt
                log.warning(
                    "HTTP %d on POST %s, retrying in %.1fs",
                    resp.status_code,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue

            resp.raise_for_status()

        except httpx.TimeoutException as e:
            last_error = e
            if attempt < max_retries:
                delay = backoff_factor ** attempt
                log.warning("Timeout on POST %s, retrying in %.1fs", url, delay)
                time.sleep(delay)
                continue

        except httpx.HTTPStatusError as e:
            raise MCPHTTPError(
                f"HTTP {e.response.status_code}: {e.response.text[:200]}",
                status_code=e.response.status_code,
                url=url,
                retries_attempted=attempt,
            ) from e

        except httpx.RequestError as e:
            last_error = e
            if attempt < max_retries:
                delay = backoff_factor ** attempt
                log.warning("Request error on POST %s, retrying in %.1fs", url, delay)
                time.sleep(delay)
                continue

    if last_error:
        raise MCPHTTPError(
            f"POST failed after {max_retries} retries: {last_error}",
            url=url,
            retries_attempted=max_retries,
        ) from last_error

    raise MCPHTTPError(
        f"POST failed after {max_retries} retries",
        url=url,
        retries_attempted=max_retries,
    )


def safe_api_call(func):
    """Decorator that wraps API calls with error handling for MCP tools.

    Converts exceptions to agent-friendly error dictionaries instead of
    crashing the entire investigation.

    Example:
        @safe_api_call
        def list_commits():
            resp = http_get(...)
            return resp.json()

        # Returns either data or {"error": "..."} on failure
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except MCPHTTPError as e:
            log.error("MCP API error in %s: %s", func.__name__, str(e))
            return {"error": str(e), "retries_attempted": e.retries_attempted}
        except httpx.HTTPStatusError as e:
            log.error("HTTP error in %s: %s", func.__name__, str(e))
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except httpx.RequestError as e:
            log.error("Request error in %s: %s", func.__name__, str(e))
            return {"error": f"Request failed: {str(e)[:200]}"}
        except Exception as e:
            log.exception("Unexpected error in %s", func.__name__)
            return {"error": f"Unexpected error: {type(e).__name__}: {str(e)[:200]}"}

    return wrapper
