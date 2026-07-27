"""Production-grade structured JSON logging.

Features:
- JSON format for log aggregation (ELK, CloudWatch, Datadog)
- Request ID correlation across services
- Automatic context enrichment (tenant, actor, trace_id)
- Log levels configurable via environment
- Human-readable format for development
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from app.observability import request_id_var


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production log aggregation.

    Outputs one JSON object per log line with:
    - timestamp (ISO8601 UTC)
    - level, logger, message
    - request_id (if available)
    - tenant_id, actor (if in multi-tenant context)
    - trace_id, span_id (if OpenTelemetry active)
    - exception info (if present)
    - extra fields from log record
    """

    RESERVED_ATTRS = frozenset({
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "module", "msecs",
        "message", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    })

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            log_obj["request_id"] = request_id

        try:
            from app import tenant_context
            cfg = tenant_context.get_tenant()
            if cfg:
                log_obj["tenant_id"] = cfg.org_id
            actor = tenant_context.get_actor()
            if actor:
                log_obj["actor"] = actor
        except Exception:
            pass

        try:
            from opentelemetry import trace
            span = trace.get_current_span()
            if span and span.is_recording():
                ctx = span.get_span_context()
                log_obj["trace_id"] = format(ctx.trace_id, "032x")
                log_obj["span_id"] = format(ctx.span_id, "016x")
        except Exception:
            pass

        if record.exc_info:
            log_obj["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info),
            }

        for key, value in record.__dict__.items():
            if key not in self.RESERVED_ATTRS and not key.startswith("_"):
                try:
                    json.dumps(value)
                    log_obj[key] = value
                except (TypeError, ValueError):
                    log_obj[key] = str(value)

        return json.dumps(log_obj, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Human-readable formatter for development.

    Format: [timestamp] LEVEL logger - message (request_id)
    """

    COLORS = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname

        if self.use_colors and level in self.COLORS:
            level = f"{self.COLORS[level]}{level:8}{self.RESET}"
        else:
            level = f"{level:8}"

        msg = record.getMessage()

        request_id = request_id_var.get()
        rid_str = f" [{request_id}]" if request_id else ""

        base = f"[{ts}] {level} {record.name:24} - {msg}{rid_str}"

        if record.exc_info:
            base += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return base


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    log_file: str | None = None,
) -> None:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_format: Use JSON format (production) vs human-readable (dev)
        log_file: Optional file path for log output

    Environment variables (override args):
        COPILOT_LOG_LEVEL: Override log level
        COPILOT_LOG_FORMAT: "json" or "human"
        COPILOT_LOG_FILE: Log file path
    """
    import os

    level = os.environ.get("COPILOT_LOG_LEVEL", level).upper()
    json_format = os.environ.get("COPILOT_LOG_FORMAT", "json" if json_format else "human") == "json"
    log_file = os.environ.get("COPILOT_LOG_FILE", log_file)

    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    use_colors = sys.stderr.isatty()
    formatter = JSONFormatter() if json_format else HumanFormatter(use_colors=use_colors)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)


class LogContext:
    """Context manager for adding fields to all logs within a scope.

    Usage:
        with LogContext(user_id="123", operation="sync"):
            log.info("Starting sync")  # includes user_id, operation
    """

    def __init__(self, **fields: Any):
        self.fields = fields
        self.old_factory: Callable[..., logging.LogRecord] | None = None

    def __enter__(self) -> "LogContext":
        self.old_factory = logging.getLogRecordFactory()
        fields = self.fields
        old_factory = self.old_factory

        def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = old_factory(*args, **kwargs)
            for key, value in fields.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(factory)
        return self

    def __exit__(self, *args: Any) -> None:
        if self.old_factory:
            logging.setLogRecordFactory(self.old_factory)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the devcopilot prefix.

    Usage:
        log = get_logger(__name__)  # devcopilot.module.submodule
    """
    if name.startswith("app."):
        name = "devcopilot." + name[4:]
    elif not name.startswith("devcopilot."):
        name = "devcopilot." + name
    return logging.getLogger(name)
