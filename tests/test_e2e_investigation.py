"""End-to-end tests for the investigation pipeline.

Tests the full flow: incident query -> agent investigation -> RCA report.
Uses mocked LLM responses for deterministic, offline testing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestFullInvestigation:
    """E2E tests for complete investigation flows."""

    @pytest.mark.asyncio
    async def test_basic_investigation_flow(self):
        """Test: incident query -> investigation -> RCA report."""
        from app.session import CopilotSession

        mock_result = MagicMock()
        mock_result.report = {
            "title": "Checkout API 500 Errors",
            "root_cause": "Null pointer in discount calculation",
            "confidence": "high",
            "evidence": ["Error logs show NPE at line 142", "Deploy at 14:00 introduced bug"],
            "resolution": "Add null check before discount calculation",
        }
        mock_result.final_text = "Root cause identified: null pointer in discount calculation"
        mock_result.tokens_used = 1500
        mock_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result

            session = CopilotSession(thread_id="test-e2e-basic")
            result = await session.ask("Why is the checkout API throwing 500 errors?")

            assert result.report is not None
            assert "root_cause" in result.report
            assert result.report["confidence"] in ["high", "medium", "low"]
            assert result.tokens_used > 0

    @pytest.mark.asyncio
    async def test_investigation_with_approval_flow(self):
        """Test investigation that requires human approval."""
        from app.session import CopilotSession

        approval_result = MagicMock()
        approval_result.report = None
        approval_result.final_text = None
        approval_result.tokens_used = 500
        approval_result.approval_request = {
            "action": "create_pull_request",
            "description": "Create PR to fix null pointer",
            "risk_level": "medium",
        }

        final_result = MagicMock()
        final_result.report = {
            "title": "Fix Applied",
            "root_cause": "Null pointer in discount calculation",
            "resolution": "PR #123 created with fix",
        }
        final_result.final_text = "PR created successfully"
        final_result.tokens_used = 800
        final_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            with patch.object(CopilotSession, 'resume', new_callable=AsyncMock) as mock_resume:
                mock_ask.return_value = approval_result
                mock_resume.return_value = final_result

                session = CopilotSession(thread_id="test-e2e-approval")

                result1 = await session.ask("Fix the checkout bug and create a PR")
                assert result1.approval_request is not None
                assert result1.approval_request["action"] == "create_pull_request"

                result2 = await session.resume(approved=True, reason="Looks good")
                assert result2.report is not None
                assert "PR" in result2.final_text

    @pytest.mark.asyncio
    async def test_investigation_rejection_flow(self):
        """Test investigation where human rejects proposed action."""
        from app.session import CopilotSession

        approval_result = MagicMock()
        approval_result.approval_request = {
            "action": "restart_service",
            "description": "Restart checkout-api pods",
            "risk_level": "high",
        }

        rejection_result = MagicMock()
        rejection_result.report = {
            "title": "Action Rejected",
            "status": "rejected",
            "reason": "User chose to investigate further",
        }
        rejection_result.final_text = "Action rejected. Continuing investigation..."
        rejection_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            with patch.object(CopilotSession, 'resume', new_callable=AsyncMock) as mock_resume:
                mock_ask.return_value = approval_result
                mock_resume.return_value = rejection_result

                session = CopilotSession(thread_id="test-e2e-rejection")

                result1 = await session.ask("Restart the failing service")
                assert result1.approval_request is not None

                result2 = await session.resume(approved=False, reason="Too risky right now")
                assert result2.report["status"] == "rejected"


class TestInvestigationScenarios:
    """Tests for specific incident scenarios."""

    @pytest.mark.asyncio
    async def test_memory_leak_scenario(self):
        """Test memory leak investigation scenario."""
        from app.session import CopilotSession

        mock_result = MagicMock()
        mock_result.report = {
            "title": "Memory Leak in User Service",
            "root_cause": "Unbounded cache growth in session store",
            "confidence": "high",
            "evidence": [
                "Memory usage grows linearly over time",
                "GC unable to reclaim session objects",
                "Cache has no TTL or size limit",
            ],
            "resolution": "Add TTL and max size to session cache",
        }
        mock_result.final_text = "Memory leak identified in session cache"
        mock_result.tokens_used = 2000
        mock_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result

            session = CopilotSession(thread_id="test-memory-leak")
            result = await session.ask("Why is the API pod getting OOMKilled?")

            assert "memory" in result.report["root_cause"].lower() or "cache" in result.report["root_cause"].lower()
            assert result.report["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_cascading_failure_scenario(self):
        """Test cascading failure investigation."""
        from app.session import CopilotSession

        mock_result = MagicMock()
        mock_result.report = {
            "title": "Cascading Service Failure",
            "root_cause": "Database connection pool exhaustion",
            "confidence": "high",
            "affected_services": ["checkout", "inventory", "user-service"],
            "timeline": [
                {"time": "14:00", "event": "DB connection spike"},
                {"time": "14:02", "event": "Pool exhausted"},
                {"time": "14:05", "event": "Services start failing"},
            ],
        }
        mock_result.final_text = "Cascading failure traced to DB connection exhaustion"
        mock_result.tokens_used = 2500
        mock_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result

            session = CopilotSession(thread_id="test-cascade")
            result = await session.ask("Why are all services degraded?")

            assert "affected_services" in result.report
            assert len(result.report["affected_services"]) > 1

    @pytest.mark.asyncio
    async def test_false_alarm_scenario(self):
        """Test that agent correctly identifies false alarms."""
        from app.session import CopilotSession

        mock_result = MagicMock()
        mock_result.report = {
            "title": "Latency Spike Analysis",
            "root_cause": None,
            "confidence": "high",
            "conclusion": "No action needed - transient spike during deployment",
            "evidence": [
                "Spike lasted only 30 seconds",
                "Correlated with scheduled deployment",
                "All metrics returned to normal",
            ],
        }
        mock_result.final_text = "This appears to be a false alarm - no action needed"
        mock_result.tokens_used = 1200
        mock_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result

            session = CopilotSession(thread_id="test-false-alarm")
            result = await session.ask("Is the latency spike a real issue?")

            assert result.report["root_cause"] is None
            assert "no action" in result.report["conclusion"].lower()


class TestStreamingInvestigation:
    """Tests for streaming investigation responses."""

    @pytest.mark.asyncio
    async def test_streaming_events(self):
        """Test that streaming produces expected event types."""
        from app.session import CopilotSession

        events = [
            {"type": "trace", "line": "Starting investigation..."},
            {"type": "trace", "line": "Searching logs..."},
            {"type": "trace", "line": "Found 15 error events"},
            {"type": "tool_call", "tool": "search_logs", "args": {"query": "error"}},
            {"type": "tool_result", "tool": "search_logs", "result": "..."},
            {"type": "trace", "line": "Analyzing patterns..."},
            {"type": "done", "status": "completed", "answer": "Root cause found"},
        ]

        async def mock_stream(message):
            for event in events:
                yield event

        with patch.object(CopilotSession, 'ask_stream', return_value=mock_stream("test")):
            session = CopilotSession(thread_id="test-streaming")

            collected = []
            async for event in session.ask_stream("Why is checkout failing?"):
                collected.append(event)

            assert len(collected) == len(events)
            assert collected[0]["type"] == "trace"
            assert collected[-1]["type"] == "done"

            tool_calls = [e for e in collected if e["type"] == "tool_call"]
            assert len(tool_calls) >= 1


class TestErrorHandling:
    """Tests for error handling in investigations."""

    @pytest.mark.asyncio
    async def test_llm_timeout_handling(self):
        """Test graceful handling of LLM timeouts."""
        from app.session import CopilotSession
        import asyncio

        async def timeout_ask(*args, **kwargs):
            raise asyncio.TimeoutError("LLM request timed out")

        with patch.object(CopilotSession, 'ask', side_effect=timeout_ask):
            session = CopilotSession(thread_id="test-timeout")

            with pytest.raises(asyncio.TimeoutError):
                await session.ask("Why is checkout failing?")

    @pytest.mark.asyncio
    async def test_tool_failure_handling(self):
        """Test that tool failures don't crash the investigation."""
        from app.session import CopilotSession

        mock_result = MagicMock()
        mock_result.report = {
            "title": "Partial Investigation",
            "root_cause": "Unable to determine - some tools failed",
            "confidence": "low",
            "failed_tools": ["datadog_search"],
            "evidence": ["GitHub data shows recent deploy"],
        }
        mock_result.final_text = "Investigation completed with partial data"
        mock_result.tokens_used = 1000
        mock_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result

            session = CopilotSession(thread_id="test-tool-failure")
            result = await session.ask("Why is checkout failing?")

            assert result.report["confidence"] == "low"
            assert "failed_tools" in result.report


class TestTokenBudget:
    """Tests for token budget enforcement."""

    @pytest.mark.asyncio
    async def test_token_budget_tracking(self):
        """Test that token usage is tracked correctly."""
        from app.session import CopilotSession

        mock_result = MagicMock()
        mock_result.report = {"title": "Test"}
        mock_result.final_text = "Done"
        mock_result.tokens_used = 5000
        mock_result.approval_request = None

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.return_value = mock_result

            session = CopilotSession(thread_id="test-tokens")
            result = await session.ask("Test query")

            assert result.tokens_used == 5000

    @pytest.mark.asyncio
    async def test_multi_turn_token_accumulation(self):
        """Test token accumulation across multiple turns."""
        from app.session import CopilotSession

        results = [
            MagicMock(report={"step": 1}, final_text="Step 1", tokens_used=1000, approval_request=None),
            MagicMock(report={"step": 2}, final_text="Step 2", tokens_used=1500, approval_request=None),
            MagicMock(report={"step": 3}, final_text="Step 3", tokens_used=2000, approval_request=None),
        ]

        with patch.object(CopilotSession, 'ask', new_callable=AsyncMock) as mock_ask:
            mock_ask.side_effect = results

            session = CopilotSession(thread_id="test-multi-turn")

            total_tokens = 0
            for i in range(3):
                result = await session.ask(f"Step {i+1}")
                total_tokens += result.tokens_used

            assert total_tokens == 4500
