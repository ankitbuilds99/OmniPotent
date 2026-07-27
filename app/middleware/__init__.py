"""Production middleware stack for FastAPI.

Provides:
- Request/response tracing with correlation IDs
- Structured request logging
- Error handling and user-friendly responses
- Performance metrics collection
- Request validation integration
"""

from app.middleware.tracing import (
    TracingMiddleware,
    get_trace_context,
    TraceContext,
)
from app.middleware.error_handler import (
    ErrorHandlerMiddleware,
    register_exception_handlers,
)
from app.middleware.request_logger import (
    RequestLoggerMiddleware,
    RequestLog,
)
from app.middleware.metrics import (
    MetricsMiddleware,
    get_request_metrics,
    RequestMetrics,
)

__all__ = [
    # Tracing
    "TracingMiddleware",
    "get_trace_context",
    "TraceContext",
    # Error handling
    "ErrorHandlerMiddleware",
    "register_exception_handlers",
    # Logging
    "RequestLoggerMiddleware",
    "RequestLog",
    # Metrics
    "MetricsMiddleware",
    "get_request_metrics",
    "RequestMetrics",
]
