"""Tests for structured logging system."""

from __future__ import annotations

import json
import logging
from io import StringIO
from unittest.mock import patch

from app.logging_config import (
    JSONFormatter,
    HumanFormatter,
    LogContext,
    setup_logging,
    get_logger,
)


class TestJSONFormatter:
    """Tests for JSON log formatter."""

    def test_basic_format(self):
        """Should format log as JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_includes_request_id(self):
        """Should include request ID when set."""
        from app.observability import request_id_var

        formatter = JSONFormatter()
        token = request_id_var.set("req-123")

        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )

            output = formatter.format(record)
            data = json.loads(output)

            assert data.get("request_id") == "req-123"
        finally:
            request_id_var.reset(token)

    def test_includes_exception(self):
        """Should include exception info."""
        formatter = JSONFormatter()

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert data["exception"]["type"] == "ValueError"
        assert data["exception"]["message"] == "Test error"

    def test_includes_extra_fields(self):
        """Should include extra fields from record."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )
        record.user_id = "user123"
        record.action = "login"

        output = formatter.format(record)
        data = json.loads(output)

        assert data.get("user_id") == "user123"
        assert data.get("action") == "login"


class TestHumanFormatter:
    """Tests for human-readable formatter."""

    def test_basic_format(self):
        """Should format log in human-readable format."""
        formatter = HumanFormatter(use_colors=False)
        record = logging.LogRecord(
            name="devcopilot.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        assert "INFO" in output
        assert "devcopilot.test" in output
        assert "Test message" in output

    def test_includes_request_id(self):
        """Should include request ID."""
        from app.observability import request_id_var

        formatter = HumanFormatter(use_colors=False)
        token = request_id_var.set("req-456")

        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )

            output = formatter.format(record)

            assert "req-456" in output
        finally:
            request_id_var.reset(token)

    def test_includes_exception_traceback(self):
        """Should include exception traceback."""
        formatter = HumanFormatter(use_colors=False)

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)

        assert "ValueError" in output
        assert "Test error" in output


class TestLogContext:
    """Tests for LogContext context manager."""

    def test_adds_fields_to_logs(self):
        """Should add fields to logs within context."""
        output = StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(JSONFormatter())

        logger = logging.getLogger("test_context")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        with LogContext(user_id="user789", operation="test"):
            logger.info("Test message")

        output.seek(0)
        data = json.loads(output.read())

        assert data.get("user_id") == "user789"
        assert data.get("operation") == "test"

        logger.removeHandler(handler)

    def test_restores_factory_after_context(self):
        """Should restore original factory after context."""
        original = logging.getLogRecordFactory()

        with LogContext(test="value"):
            pass

        assert logging.getLogRecordFactory() == original


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_sets_log_level(self):
        """Should set the log level."""
        setup_logging(level="DEBUG", json_format=False)
        root = logging.getLogger()

        assert root.level == logging.DEBUG

    def test_uses_json_format(self):
        """Should use JSON format when requested."""
        setup_logging(level="INFO", json_format=True)
        root = logging.getLogger()

        assert len(root.handlers) > 0
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_uses_human_format(self):
        """Should use human format when not JSON."""
        setup_logging(level="INFO", json_format=False)
        root = logging.getLogger()

        assert len(root.handlers) > 0
        assert isinstance(root.handlers[0].formatter, HumanFormatter)

    def test_env_override(self):
        """Should respect environment variables."""
        with patch.dict("os.environ", {"COPILOT_LOG_LEVEL": "WARNING"}):
            setup_logging(level="DEBUG")
            root = logging.getLogger()

            assert root.level == logging.WARNING


class TestGetLogger:
    """Tests for get_logger function."""

    def test_prefixes_app_names(self):
        """Should prefix app.* with devcopilot."""
        logger = get_logger("app.api.main")

        assert logger.name == "devcopilot.api.main"

    def test_prefixes_plain_names(self):
        """Should prefix plain names."""
        logger = get_logger("mymodule")

        assert logger.name == "devcopilot.mymodule"

    def test_preserves_devcopilot_prefix(self):
        """Should not double-prefix devcopilot names."""
        logger = get_logger("devcopilot.test")

        assert logger.name == "devcopilot.test"
