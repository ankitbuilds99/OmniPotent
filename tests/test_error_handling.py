"""Tests for error handling module."""

import asyncio
import pytest


class TestExceptions:
    """Tests for custom exception hierarchy."""

    def test_copilot_error_base(self):
        """Test CopilotError base class."""
        from app.error_handling.exceptions import CopilotError

        error = CopilotError(
            message="Something went wrong",
            code="TEST_ERROR",
            status_code=500,
            details={"key": "value"},
            retryable=True,
        )

        assert error.message == "Something went wrong"
        assert error.code == "TEST_ERROR"
        assert error.status_code == 500
        assert error.details == {"key": "value"}
        assert error.retryable is True

    def test_copilot_error_to_dict(self):
        """Test exception serialization."""
        from app.error_handling.exceptions import CopilotError

        error = CopilotError("Test", code="TEST", status_code=400)
        data = error.to_dict()

        assert data["error"] == "TEST"
        assert data["message"] == "Test"

    def test_validation_error(self):
        """Test ValidationError."""
        from app.error_handling.exceptions import ValidationError

        error = ValidationError("Invalid email", field="email")

        assert error.status_code == 400
        assert error.code == "VALIDATION_ERROR"
        assert error.field == "email"
        assert error.details["field"] == "email"

    def test_authentication_error(self):
        """Test AuthenticationError."""
        from app.error_handling.exceptions import AuthenticationError

        error = AuthenticationError()

        assert error.status_code == 401
        assert error.code == "AUTHENTICATION_ERROR"
        assert error.retryable is False

    def test_rate_limit_error(self):
        """Test RateLimitError with retry_after."""
        from app.error_handling.exceptions import RateLimitError

        error = RateLimitError(retry_after=120)

        assert error.status_code == 429
        assert error.retry_after == 120
        assert error.retryable is True
        assert error.details["retry_after_seconds"] == 120

    def test_external_service_error(self):
        """Test ExternalServiceError."""
        from app.error_handling.exceptions import ExternalServiceError

        original = ValueError("Connection failed")
        error = ExternalServiceError(
            message="Datadog unavailable",
            service="datadog",
            original_error=original,
        )

        assert error.status_code == 502
        assert error.service == "datadog"
        assert error.original_error == original
        assert error.retryable is True

    def test_llm_error(self):
        """Test LLMError."""
        from app.error_handling.exceptions import LLMError

        error = LLMError(
            message="API rate limit exceeded",
            provider="anthropic",
            model="claude-sonnet-4-20250514",
        )

        assert error.provider == "anthropic"
        assert error.model == "claude-sonnet-4-20250514"
        assert "anthropic" in error.service

    def test_timeout_error(self):
        """Test TimeoutError."""
        from app.error_handling.exceptions import TimeoutError

        error = TimeoutError(
            operation="search_logs",
            timeout_seconds=30.0,
        )

        assert error.status_code == 504
        assert error.operation == "search_logs"
        assert error.retryable is True


class TestRetryDecorator:
    """Tests for retry decorator."""

    def test_retry_success_first_try(self):
        """Test function succeeds on first try."""
        from app.error_handling.retry import with_retry

        call_count = 0

        @with_retry(max_attempts=3)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "success"

        result = succeed()
        assert result == "success"
        assert call_count == 1

    def test_retry_after_failures(self):
        """Test function retries and eventually succeeds."""
        from app.error_handling.retry import with_retry

        call_count = 0

        @with_retry(max_attempts=3, initial_delay=0.01)
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"

        result = fail_then_succeed()
        assert result == "success"
        assert call_count == 3

    def test_retry_exhausted(self):
        """Test all retries exhausted raises original error."""
        from app.error_handling.retry import with_retry

        call_count = 0

        @with_retry(max_attempts=3, initial_delay=0.01)
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        with pytest.raises(ValueError, match="Always fails"):
            always_fail()

        assert call_count == 3

    def test_retry_config_exponential_backoff(self):
        """Test exponential backoff calculation."""
        from app.error_handling.retry import RetryConfig

        config = RetryConfig(
            initial_delay=1.0,
            exponential_base=2.0,
            max_delay=60.0,
            jitter=False,
        )

        assert config.calculate_delay(1) == 1.0
        assert config.calculate_delay(2) == 2.0
        assert config.calculate_delay(3) == 4.0
        assert config.calculate_delay(10) == 60.0

    def test_retry_config_should_retry(self):
        """Test should_retry logic."""
        from app.error_handling.retry import RetryConfig
        from app.error_handling.exceptions import RateLimitError, ValidationError

        config = RetryConfig()

        assert config.should_retry(RateLimitError()) is True
        assert config.should_retry(ValidationError("bad")) is False

    @pytest.mark.asyncio
    async def test_async_retry(self):
        """Test async retry decorator."""
        from app.error_handling.retry import async_with_retry

        call_count = 0

        @async_with_retry(max_attempts=3, initial_delay=0.01)
        async def async_fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("Temporary")
            return "success"

        result = await async_fail_then_succeed()
        assert result == "success"
        assert call_count == 2


class TestCircuitBreaker:
    """Tests for circuit breaker."""

    @pytest.mark.asyncio
    async def test_circuit_closed_allows_calls(self):
        """Test closed circuit allows calls."""
        from app.error_handling.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker(name="test", failure_threshold=3)

        async with breaker:
            pass

        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.successful_calls == 1

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        """Test circuit opens after threshold failures."""
        from app.error_handling.circuit_breaker import (
            CircuitBreaker,
            CircuitState,
            CircuitOpenError,
        )

        breaker = CircuitBreaker(name="test", failure_threshold=2)

        for _ in range(2):
            with pytest.raises(ValueError):
                async with breaker:
                    raise ValueError("Failed")

        assert breaker.state == CircuitState.OPEN

        with pytest.raises(CircuitOpenError):
            async with breaker:
                pass

    @pytest.mark.asyncio
    async def test_circuit_half_open_after_timeout(self):
        """Test circuit goes half-open after timeout."""
        from app.error_handling.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            timeout=0.01,
        )

        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("Failed")

        assert breaker.state == CircuitState.OPEN

        await asyncio.sleep(0.02)

        async with breaker:
            pass

        assert breaker.state == CircuitState.HALF_OPEN or breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_closes_after_success_threshold(self):
        """Test circuit closes after enough successes in half-open."""
        from app.error_handling.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            success_threshold=2,
            timeout=0.01,
        )

        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("Failed")

        await asyncio.sleep(0.02)

        for _ in range(2):
            async with breaker:
                pass

        assert breaker.state == CircuitState.CLOSED

    def test_circuit_reset(self):
        """Test manual circuit reset."""
        from app.error_handling.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker(name="test")
        breaker.force_open()

        assert breaker.state == CircuitState.OPEN

        breaker.reset()

        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.total_calls == 0

    @pytest.mark.asyncio
    async def test_circuit_decorator(self):
        """Test circuit breaker as decorator."""
        from app.error_handling.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(name="test")

        @breaker
        async def protected_call():
            return "success"

        result = await protected_call()
        assert result == "success"
        assert breaker.stats.successful_calls == 1


class TestErrorHandlers:
    """Tests for error handling utilities."""

    def test_classify_error(self):
        """Test error classification."""
        from app.error_handling.handlers import classify_error, ErrorClassification
        from app.error_handling.exceptions import ValidationError, RateLimitError

        assert classify_error(ValidationError("bad")) == ErrorClassification.USER_ERROR
        assert classify_error(RateLimitError()) == ErrorClassification.RATE_LIMIT

    def test_to_user_message_safe(self):
        """Test user messages don't leak sensitive info."""
        from app.error_handling.handlers import to_user_message
        from app.error_handling.exceptions import CopilotError

        error = CopilotError("Internal: password=secret123 at /home/user/file.py")
        message = to_user_message(error)

        assert "password" not in message.lower()
        assert "secret" not in message
        assert "/home/" not in message

    def test_to_user_message_rate_limit(self):
        """Test rate limit error message."""
        from app.error_handling.handlers import to_user_message

        error = Exception("Rate_limit_exceeded error from API")
        message = to_user_message(error)

        assert "rate limit" in message.lower()

    def test_handle_exception_context(self):
        """Test exception handling returns context."""
        from app.error_handling.handlers import handle_exception, ErrorClassification
        from app.error_handling.exceptions import ValidationError

        error = ValidationError("Invalid field", field="email")
        context = handle_exception(error, request_id="req_123")

        assert context.classification == ErrorClassification.USER_ERROR
        assert context.status_code == 400
        assert context.retryable is False
        assert "req_123" in str(context.details)
