"""Global error handling middleware and exception handlers.

Provides:
- Consistent error response format
- User-friendly error messages
- Error classification and metrics
- Integration with tracing
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from app.error_handling.exceptions import (
    CopilotError,
)
from app.error_handling.handlers import (
    handle_exception,
    to_user_message,
    classify_error,
    log_exception,
)
from app.middleware.tracing import get_trace_context

log = logging.getLogger("omnipotent.middleware.error_handler")


def _error_response(
    status_code: int,
    error: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    """Create a standardized error response."""
    body: dict[str, Any] = {
        "error": error,
        "message": message,
    }

    if details:
        body["details"] = details

    if request_id:
        body["request_id"] = request_id

    ctx = get_trace_context()
    if ctx:
        body["trace_id"] = ctx.trace_id

    return JSONResponse(status_code=status_code, content=body)


async def copilot_error_handler(
    request: Request, exc: CopilotError
) -> JSONResponse:
    """Handle CopilotError exceptions."""
    ctx = get_trace_context()
    request_id = ctx.trace_id if ctx else None

    context = handle_exception(exc, request_id=request_id)
    log_exception(exc, context, request_id=request_id)

    details = None
    if exc.details:
        details = [{"code": exc.code, "details": exc.details}]

    return _error_response(
        status_code=exc.status_code,
        error=exc.code,
        message=context.user_message,
        details=details,
        request_id=request_id,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI/Pydantic validation errors."""
    ctx = get_trace_context()
    request_id = ctx.trace_id if ctx else None

    details = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error.get("loc", []))
        details.append({
            "code": "VALIDATION_ERROR",
            "field": field,
            "message": error.get("msg", "Invalid value"),
            "type": error.get("type", "value_error"),
        })

    log.warning(
        "Validation error: %d field(s) invalid",
        len(details),
        extra={"request_id": request_id, "errors": details},
    )

    return _error_response(
        status_code=400,
        error="VALIDATION_ERROR",
        message="Request validation failed. Check the details for specific errors.",
        details=details,
        request_id=request_id,
    )


async def pydantic_error_handler(
    request: Request, exc: PydanticValidationError
) -> JSONResponse:
    """Handle raw Pydantic validation errors."""
    ctx = get_trace_context()
    request_id = ctx.trace_id if ctx else None

    details = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error.get("loc", []))
        details.append({
            "code": "VALIDATION_ERROR",
            "field": field,
            "message": error.get("msg", "Invalid value"),
        })

    return _error_response(
        status_code=400,
        error="VALIDATION_ERROR",
        message="Data validation failed.",
        details=details,
        request_id=request_id,
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    ctx = get_trace_context()
    request_id = ctx.trace_id if ctx else None

    classification = classify_error(exc)
    user_message = to_user_message(exc)

    log.error(
        "Unhandled exception: %s",
        type(exc).__name__,
        extra={
            "request_id": request_id,
            "classification": classification.value,
            "path": request.url.path,
        },
        exc_info=True,
    )

    return _error_response(
        status_code=500,
        error="INTERNAL_ERROR",
        message=user_message,
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on a FastAPI app.

    Usage:
        app = FastAPI()
        register_exception_handlers(app)
    """
    app.add_exception_handler(CopilotError, copilot_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PydanticValidationError, pydantic_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_error_handler)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Middleware that catches and handles errors uniformly.

    This provides a safety net for errors that slip through
    the exception handlers, ensuring consistent error responses.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Process request with error handling."""
        try:
            return await call_next(request)
        except CopilotError as e:
            return await copilot_error_handler(request, e)
        except RequestValidationError as e:
            return await validation_error_handler(request, e)
        except PydanticValidationError as e:
            return await pydantic_error_handler(request, e)
        except Exception as e:
            return await generic_error_handler(request, e)
