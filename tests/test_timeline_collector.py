"""Tests for the Timeline Collector module."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.timeline.collector import (
    EventType,
    EventSeverity,
    EventSource,
    TimelineEvent,
    TimelineFilter,
    TimelineCollector,
    aggregate_events,
    correlate_events,
    collect_prometheus_events,
    collect_kubernetes_events,
    collect_github_events,
    collect_incident_timeline,
)


class TestEventEnums:
    """Tests for event enumerations."""

    def test_event_type_values(self):
        """Test EventType enum values."""
        assert EventType.ALERT.value == "alert"
        assert EventType.METRIC.value == "metric"
        assert EventType.LOG.value == "log"
        assert EventType.DEPLOY.value == "deploy"
        assert EventType.CONFIG_CHANGE.value == "config_change"
        assert EventType.SCALE.value == "scale"
        assert EventType.RESTART.value == "restart"
        assert EventType.COMMUNICATION.value == "communication"
        assert EventType.ANNOTATION.value == "annotation"
        assert EventType.INVESTIGATION.value == "investigation"
        assert EventType.MITIGATION.value == "mitigation"
        assert EventType.RESOLUTION.value == "resolution"
        assert EventType.OTHER.value == "other"

    def test_event_severity_values(self):
        """Test EventSeverity enum values."""
        assert EventSeverity.INFO.value == "info"
        assert EventSeverity.WARNING.value == "warning"
        assert EventSeverity.ERROR.value == "error"
        assert EventSeverity.CRITICAL.value == "critical"

    def test_event_source_values(self):
        """Test EventSource enum values."""
        assert EventSource.PROMETHEUS.value == "prometheus"
        assert EventSource.DATADOG.value == "datadog"
        assert EventSource.CLOUDWATCH.value == "cloudwatch"
        assert EventSource.ELASTICSEARCH.value == "elasticsearch"
        assert EventSource.GITHUB.value == "github"
        assert EventSource.ARGOCD.value == "argocd"
        assert EventSource.KUBERNETES.value == "kubernetes"
        assert EventSource.SLACK.value == "slack"
        assert EventSource.PAGERDUTY.value == "pagerduty"
        assert EventSource.MANUAL.value == "manual"
        assert EventSource.COPILOT.value == "copilot"
        assert EventSource.OTHER.value == "other"


class TestTimelineEvent:
    """Tests for TimelineEvent dataclass."""

    @pytest.fixture
    def sample_event(self):
        """Create a sample event."""
        return TimelineEvent(
            id="event_001",
            timestamp=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            title="High CPU Alert",
            description="CPU usage exceeded 90%",
            event_type=EventType.ALERT,
            severity=EventSeverity.WARNING,
            source=EventSource.PROMETHEUS,
            service="checkout-service",
            host="node-1",
            user="oncall",
            url="https://prometheus.example.com/alert/123",
            tags=["cpu", "performance"],
            metadata={"threshold": 90, "current": 95},
            correlation_id="incident_001",
            parent_id="",
            duration_seconds=300,
        )

    def test_event_creation_minimal(self):
        """Test creating event with minimal fields."""
        event = TimelineEvent(
            id="event_001",
            timestamp=datetime.now(timezone.utc),
            title="Test Event",
        )
        assert event.id == "event_001"
        assert event.title == "Test Event"
        assert event.description == ""
        assert event.event_type == EventType.OTHER
        assert event.severity == EventSeverity.INFO
        assert event.source == EventSource.OTHER
        assert event.tags == []
        assert event.metadata == {}

    def test_event_to_dict(self, sample_event):
        """Test converting event to dictionary."""
        result = sample_event.to_dict()

        assert result["id"] == "event_001"
        assert result["timestamp"] == "2024-01-15T10:30:00+00:00"
        assert result["title"] == "High CPU Alert"
        assert result["description"] == "CPU usage exceeded 90%"
        assert result["event_type"] == "alert"
        assert result["severity"] == "warning"
        assert result["source"] == "prometheus"
        assert result["service"] == "checkout-service"
        assert result["host"] == "node-1"
        assert result["user"] == "oncall"
        assert result["url"] == "https://prometheus.example.com/alert/123"
        assert result["tags"] == ["cpu", "performance"]
        assert result["metadata"] == {"threshold": 90, "current": 95}
        assert result["correlation_id"] == "incident_001"
        assert result["parent_id"] == ""
        assert result["duration_seconds"] == 300

    def test_event_with_empty_optional_fields(self):
        """Test event with empty optional fields."""
        event = TimelineEvent(
            id="event_002",
            timestamp=datetime.now(timezone.utc),
            title="Simple Event",
        )
        result = event.to_dict()

        assert result["service"] == ""
        assert result["host"] == ""
        assert result["user"] == ""
        assert result["url"] == ""
        assert result["tags"] == []
        assert result["metadata"] == {}
        assert result["correlation_id"] == ""
        assert result["parent_id"] == ""
        assert result["duration_seconds"] is None


class TestTimelineFilter:
    """Tests for TimelineFilter."""

    @pytest.fixture
    def base_event(self):
        """Create a base event for filtering tests."""
        return TimelineEvent(
            id="event_001",
            timestamp=datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            title="Test Alert",
            description="Test description",
            event_type=EventType.ALERT,
            severity=EventSeverity.WARNING,
            source=EventSource.PROMETHEUS,
            service="checkout-service",
            tags=["production", "high-priority"],
        )

    def test_filter_default_values(self):
        """Test filter with default values."""
        filter = TimelineFilter()

        assert filter.start_time is None
        assert filter.end_time is None
        assert filter.event_types is None
        assert filter.severities is None
        assert filter.sources is None
        assert filter.services is None
        assert filter.tags is None
        assert filter.search_query == ""
        assert filter.limit == 1000

    def test_filter_matches_all_default(self, base_event):
        """Test default filter matches everything."""
        filter = TimelineFilter()
        assert filter.matches(base_event) is True

    def test_filter_by_start_time(self, base_event):
        """Test filtering by start time."""
        filter_before = TimelineFilter(
            start_time=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
        )
        assert filter_before.matches(base_event) is True

        filter_after = TimelineFilter(
            start_time=datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        )
        assert filter_after.matches(base_event) is False

    def test_filter_by_end_time(self, base_event):
        """Test filtering by end time."""
        filter_after = TimelineFilter(
            end_time=datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc)
        )
        assert filter_after.matches(base_event) is True

        filter_before = TimelineFilter(
            end_time=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
        )
        assert filter_before.matches(base_event) is False

    def test_filter_by_event_types(self, base_event):
        """Test filtering by event types."""
        filter_match = TimelineFilter(event_types=[EventType.ALERT, EventType.METRIC])
        assert filter_match.matches(base_event) is True

        filter_no_match = TimelineFilter(event_types=[EventType.DEPLOY])
        assert filter_no_match.matches(base_event) is False

    def test_filter_by_severities(self, base_event):
        """Test filtering by severities."""
        filter_match = TimelineFilter(severities=[EventSeverity.WARNING, EventSeverity.ERROR])
        assert filter_match.matches(base_event) is True

        filter_no_match = TimelineFilter(severities=[EventSeverity.CRITICAL])
        assert filter_no_match.matches(base_event) is False

    def test_filter_by_sources(self, base_event):
        """Test filtering by sources."""
        filter_match = TimelineFilter(sources=[EventSource.PROMETHEUS])
        assert filter_match.matches(base_event) is True

        filter_no_match = TimelineFilter(sources=[EventSource.DATADOG])
        assert filter_no_match.matches(base_event) is False

    def test_filter_by_services(self, base_event):
        """Test filtering by services."""
        filter_match = TimelineFilter(services=["checkout-service", "payment-service"])
        assert filter_match.matches(base_event) is True

        filter_no_match = TimelineFilter(services=["auth-service"])
        assert filter_no_match.matches(base_event) is False

    def test_filter_by_tags(self, base_event):
        """Test filtering by tags."""
        filter_match = TimelineFilter(tags=["production"])
        assert filter_match.matches(base_event) is True

        filter_match_any = TimelineFilter(tags=["staging", "high-priority"])
        assert filter_match_any.matches(base_event) is True

        filter_no_match = TimelineFilter(tags=["development"])
        assert filter_no_match.matches(base_event) is False

    def test_filter_by_search_query(self, base_event):
        """Test filtering by search query."""
        filter_title = TimelineFilter(search_query="test alert")
        assert filter_title.matches(base_event) is True

        filter_description = TimelineFilter(search_query="description")
        assert filter_description.matches(base_event) is True

        filter_tag = TimelineFilter(search_query="production")
        assert filter_tag.matches(base_event) is True

        filter_no_match = TimelineFilter(search_query="nonexistent")
        assert filter_no_match.matches(base_event) is False

    def test_filter_combined(self, base_event):
        """Test combined filter criteria."""
        filter_all_match = TimelineFilter(
            start_time=datetime(2024, 1, 15, 11, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 15, 13, 0, 0, tzinfo=timezone.utc),
            event_types=[EventType.ALERT],
            severities=[EventSeverity.WARNING],
            sources=[EventSource.PROMETHEUS],
            services=["checkout-service"],
            tags=["production"],
            search_query="alert",
        )
        assert filter_all_match.matches(base_event) is True

        filter_one_fails = TimelineFilter(
            event_types=[EventType.ALERT],
            severities=[EventSeverity.CRITICAL],
        )
        assert filter_one_fails.matches(base_event) is False


class TestTimelineCollector:
    """Tests for TimelineCollector class."""

    @pytest.fixture
    def collector(self):
        """Create a collector instance."""
        return TimelineCollector()

    @pytest.fixture
    def sample_events(self):
        """Create sample events."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        return [
            TimelineEvent(
                id="event_001",
                timestamp=base,
                title="Alert 1",
                event_type=EventType.ALERT,
                severity=EventSeverity.WARNING,
                source=EventSource.PROMETHEUS,
                service="checkout-service",
            ),
            TimelineEvent(
                id="event_002",
                timestamp=base + timedelta(minutes=10),
                title="Deploy",
                event_type=EventType.DEPLOY,
                severity=EventSeverity.INFO,
                source=EventSource.GITHUB,
                service="checkout-service",
            ),
            TimelineEvent(
                id="event_003",
                timestamp=base + timedelta(minutes=20),
                title="Alert 2",
                event_type=EventType.ALERT,
                severity=EventSeverity.ERROR,
                source=EventSource.PROMETHEUS,
                service="payment-service",
            ),
        ]

    def test_register_collector(self, collector):
        """Test registering an event collector."""
        async def mock_collector(start, end, context):
            return []

        collector.register_collector(EventSource.PROMETHEUS, mock_collector)

        assert EventSource.PROMETHEUS in collector._collectors

    @pytest.mark.asyncio
    async def test_collect_from_registered_sources(self, collector):
        """Test collecting events from registered sources."""
        events = [
            TimelineEvent(
                id="event_001",
                timestamp=datetime.now(timezone.utc),
                title="Test Event",
            )
        ]

        async def mock_collector(start, end, context):
            return events

        collector.register_collector(EventSource.PROMETHEUS, mock_collector)

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        result = await collector.collect(start, end)

        assert len(result) == 1
        assert result[0].id == "event_001"

    @pytest.mark.asyncio
    async def test_collect_skips_unregistered_sources(self, collector):
        """Test that unregistered sources are skipped."""
        async def mock_collector(start, end, context):
            return [
                TimelineEvent(
                    id="event_001",
                    timestamp=datetime.now(timezone.utc),
                    title="Test",
                )
            ]

        collector.register_collector(EventSource.PROMETHEUS, mock_collector)

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        result = await collector.collect(start, end, sources=[EventSource.DATADOG])

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_collect_handles_collector_errors(self, collector):
        """Test that collector errors are handled gracefully."""
        async def failing_collector(start, end, context):
            raise Exception("Connection failed")

        collector.register_collector(EventSource.PROMETHEUS, failing_collector)

        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        result = await collector.collect(start, end)

        assert result == []

    @pytest.mark.asyncio
    async def test_collect_sorts_events_by_timestamp(self, collector):
        """Test that collected events are sorted by timestamp."""
        now = datetime.now(timezone.utc)
        events = [
            TimelineEvent(id="event_003", timestamp=now + timedelta(minutes=20), title="Third"),
            TimelineEvent(id="event_001", timestamp=now, title="First"),
            TimelineEvent(id="event_002", timestamp=now + timedelta(minutes=10), title="Second"),
        ]

        async def mock_collector(start, end, context):
            return events

        collector.register_collector(EventSource.PROMETHEUS, mock_collector)

        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)

        result = await collector.collect(start, end)

        assert result[0].id == "event_001"
        assert result[1].id == "event_002"
        assert result[2].id == "event_003"

    def test_add_event(self, collector, sample_events):
        """Test adding a single event."""
        collector.add_event(sample_events[0])

        assert len(collector._events) == 1
        assert collector._events[0].id == "event_001"

    def test_add_event_maintains_sort_order(self, collector, sample_events):
        """Test that adding events maintains sort order."""
        collector.add_event(sample_events[2])
        collector.add_event(sample_events[0])
        collector.add_event(sample_events[1])

        assert collector._events[0].id == "event_001"
        assert collector._events[1].id == "event_002"
        assert collector._events[2].id == "event_003"

    def test_get_events_no_filter(self, collector, sample_events):
        """Test getting events without filter."""
        for event in sample_events:
            collector.add_event(event)

        result = collector.get_events()

        assert len(result) == 3

    def test_get_events_with_filter(self, collector, sample_events):
        """Test getting events with filter."""
        for event in sample_events:
            collector.add_event(event)

        filter = TimelineFilter(event_types=[EventType.ALERT])
        result = collector.get_events(filter)

        assert len(result) == 2
        assert all(e.event_type == EventType.ALERT for e in result)

    def test_get_events_with_limit(self, collector, sample_events):
        """Test getting events with limit."""
        for event in sample_events:
            collector.add_event(event)

        filter = TimelineFilter(limit=2)
        result = collector.get_events(filter)

        assert len(result) == 2

    def test_get_services(self, collector, sample_events):
        """Test getting unique services."""
        for event in sample_events:
            collector.add_event(event)

        services = collector.get_services()

        assert len(services) == 2
        assert "checkout-service" in services
        assert "payment-service" in services

    def test_get_event_counts_by_type(self, collector, sample_events):
        """Test getting event counts by type."""
        for event in sample_events:
            collector.add_event(event)

        counts = collector.get_event_counts_by_type()

        assert counts["alert"] == 2
        assert counts["deploy"] == 1

    def test_get_event_counts_by_severity(self, collector, sample_events):
        """Test getting event counts by severity."""
        for event in sample_events:
            collector.add_event(event)

        counts = collector.get_event_counts_by_severity()

        assert counts["warning"] == 1
        assert counts["info"] == 1
        assert counts["error"] == 1

    def test_get_timeline_summary_empty(self, collector):
        """Test getting summary for empty timeline."""
        summary = collector.get_timeline_summary()

        assert summary["total_events"] == 0
        assert summary["start_time"] is None
        assert summary["end_time"] is None
        assert summary["duration_minutes"] == 0
        assert summary["services"] == []
        assert summary["by_type"] == {}
        assert summary["by_severity"] == {}

    def test_get_timeline_summary_with_events(self, collector, sample_events):
        """Test getting summary with events."""
        for event in sample_events:
            collector.add_event(event)

        summary = collector.get_timeline_summary()

        assert summary["total_events"] == 3
        assert summary["start_time"] is not None
        assert summary["end_time"] is not None
        assert summary["duration_minutes"] == 20
        assert len(summary["services"]) == 2
        assert summary["by_type"]["alert"] == 2
        assert summary["by_severity"]["warning"] == 1


class TestAggregateEvents:
    """Tests for aggregate_events function."""

    def test_aggregate_empty_events(self):
        """Test aggregating empty event list."""
        result = aggregate_events([])
        assert result == []

    def test_aggregate_single_event(self):
        """Test aggregating single event."""
        event = TimelineEvent(
            id="event_001",
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            title="Test",
            severity=EventSeverity.WARNING,
            service="test-service",
        )

        result = aggregate_events([event], window_seconds=60)

        assert len(result) == 1
        assert result[0]["count"] == 1
        assert result[0]["by_severity"]["warning"] == 1
        assert "test-service" in result[0]["services"]

    def test_aggregate_multiple_windows(self):
        """Test aggregating events into multiple windows."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(id="e1", timestamp=base, title="E1", severity=EventSeverity.INFO),
            TimelineEvent(
                id="e2",
                timestamp=base + timedelta(seconds=30),
                title="E2",
                severity=EventSeverity.WARNING,
            ),
            TimelineEvent(
                id="e3",
                timestamp=base + timedelta(seconds=90),
                title="E3",
                severity=EventSeverity.ERROR,
            ),
        ]

        result = aggregate_events(events, window_seconds=60)

        assert len(result) == 2
        assert result[0]["count"] == 2
        assert result[1]["count"] == 1

    def test_aggregate_severity_counts(self):
        """Test that severity counts are correct in each window."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(id="e1", timestamp=base, title="E1", severity=EventSeverity.INFO),
            TimelineEvent(
                id="e2",
                timestamp=base + timedelta(seconds=10),
                title="E2",
                severity=EventSeverity.INFO,
            ),
            TimelineEvent(
                id="e3",
                timestamp=base + timedelta(seconds=20),
                title="E3",
                severity=EventSeverity.WARNING,
            ),
        ]

        result = aggregate_events(events, window_seconds=60)

        assert result[0]["by_severity"]["info"] == 2
        assert result[0]["by_severity"]["warning"] == 1
        assert result[0]["by_severity"]["error"] == 0
        assert result[0]["by_severity"]["critical"] == 0


class TestCorrelateEvents:
    """Tests for correlate_events function."""

    def test_correlate_empty_events(self):
        """Test correlating empty event list."""
        result = correlate_events([])
        assert result == []

    def test_correlate_single_event(self):
        """Test correlating single event."""
        event = TimelineEvent(
            id="event_001",
            timestamp=datetime.now(timezone.utc),
            title="Test",
        )

        result = correlate_events([event])

        assert len(result) == 1
        assert len(result[0]) == 1
        assert result[0][0].id == "event_001"

    def test_correlate_by_service(self):
        """Test correlating events by service."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(
                id="e1",
                timestamp=base,
                title="E1",
                service="checkout-service",
            ),
            TimelineEvent(
                id="e2",
                timestamp=base + timedelta(seconds=30),
                title="E2",
                service="checkout-service",
            ),
            TimelineEvent(
                id="e3",
                timestamp=base + timedelta(seconds=60),
                title="E3",
                service="payment-service",
            ),
        ]

        result = correlate_events(events, correlation_window_seconds=120)

        assert len(result) == 2
        group_ids = [[e.id for e in group] for group in result]
        assert ["e1", "e2"] in group_ids

    def test_correlate_by_correlation_id(self):
        """Test correlating events by correlation_id."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(
                id="e1",
                timestamp=base,
                title="E1",
                correlation_id="incident_001",
            ),
            TimelineEvent(
                id="e2",
                timestamp=base + timedelta(seconds=30),
                title="E2",
                correlation_id="incident_001",
            ),
            TimelineEvent(
                id="e3",
                timestamp=base + timedelta(seconds=60),
                title="E3",
                correlation_id="incident_002",
            ),
        ]

        result = correlate_events(events, correlation_window_seconds=120)

        group1_ids = [e.id for e in result[0]]
        assert "e1" in group1_ids
        assert "e2" in group1_ids

    def test_correlate_by_tags(self):
        """Test correlating events by shared tags."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(
                id="e1",
                timestamp=base,
                title="E1",
                tags=["production", "checkout"],
            ),
            TimelineEvent(
                id="e2",
                timestamp=base + timedelta(seconds=30),
                title="E2",
                tags=["production", "payment"],
            ),
            TimelineEvent(
                id="e3",
                timestamp=base + timedelta(seconds=60),
                title="E3",
                tags=["staging"],
            ),
        ]

        result = correlate_events(events, correlation_window_seconds=120)

        group1_ids = [e.id for e in result[0]]
        assert "e1" in group1_ids
        assert "e2" in group1_ids

    def test_correlate_outside_window(self):
        """Test that events outside correlation window are not grouped."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(
                id="e1",
                timestamp=base,
                title="E1",
                service="checkout-service",
            ),
            TimelineEvent(
                id="e2",
                timestamp=base + timedelta(minutes=10),
                title="E2",
                service="checkout-service",
            ),
        ]

        result = correlate_events(events, correlation_window_seconds=60)

        assert len(result) == 2
        assert len(result[0]) == 1
        assert len(result[1]) == 1

    def test_correlate_groups_sorted_by_timestamp(self):
        """Test that events within groups are sorted by timestamp."""
        base = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        events = [
            TimelineEvent(
                id="e1",
                timestamp=base + timedelta(seconds=30),
                title="E1",
                service="checkout-service",
            ),
            TimelineEvent(
                id="e2",
                timestamp=base,
                title="E2",
                service="checkout-service",
            ),
        ]

        result = correlate_events(events, correlation_window_seconds=120)

        assert result[0][0].id == "e2"
        assert result[0][1].id == "e1"


class TestPrometheusCollector:
    """Tests for Prometheus event collector."""

    @pytest.mark.asyncio
    async def test_collect_without_url(self):
        """Test that no events are returned without prometheus_url."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        result = await collect_prometheus_events(start, end, {})

        assert result == []

    @pytest.mark.asyncio
    async def test_collect_with_valid_response(self):
        """Test collecting events with valid Prometheus response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": {
                "result": [
                    {
                        "metric": {
                            "alertname": "HighCPU",
                            "service": "checkout",
                            "description": "CPU too high",
                        },
                        "value": [
                            datetime.now(timezone.utc).timestamp(),
                            "1",
                        ],
                    }
                ]
            }
        }

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value = mock_response

            start = datetime.now(timezone.utc) - timedelta(hours=1)
            end = datetime.now(timezone.utc) + timedelta(hours=1)

            result = await collect_prometheus_events(
                start, end, {"prometheus_url": "http://prometheus:9090"}
            )

            assert len(result) == 1
            assert result[0].title == "HighCPU"
            assert result[0].source == EventSource.PROMETHEUS
            assert result[0].event_type == EventType.ALERT

    @pytest.mark.asyncio
    async def test_collect_handles_non_200_response(self):
        """Test handling of non-200 response."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value = mock_response

            start = datetime.now(timezone.utc) - timedelta(hours=1)
            end = datetime.now(timezone.utc)

            result = await collect_prometheus_events(
                start, end, {"prometheus_url": "http://prometheus:9090"}
            )

            assert result == []

    @pytest.mark.asyncio
    async def test_collect_handles_exception(self):
        """Test handling of exceptions during collection."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.side_effect = Exception("Connection failed")

            start = datetime.now(timezone.utc) - timedelta(hours=1)
            end = datetime.now(timezone.utc)

            result = await collect_prometheus_events(
                start, end, {"prometheus_url": "http://prometheus:9090"}
            )

            assert result == []


class TestKubernetesCollector:
    """Tests for Kubernetes event collector."""

    @pytest.mark.asyncio
    async def test_collect_handles_missing_kubernetes_library(self):
        """Test graceful handling when kubernetes library is unavailable."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        with patch.dict("sys.modules", {"kubernetes": None}):
            result = await collect_kubernetes_events(start, end, {})
            assert result == []


class TestGitHubCollector:
    """Tests for GitHub event collector."""

    @pytest.mark.asyncio
    async def test_collect_without_token(self):
        """Test that no events are returned without github_token."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        result = await collect_github_events(start, end, {})

        assert result == []

    @pytest.mark.asyncio
    async def test_collect_without_repo(self):
        """Test that no events are returned without github_repo."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        result = await collect_github_events(start, end, {"github_token": "token"})

        assert result == []

    @pytest.mark.asyncio
    async def test_collect_with_valid_response(self):
        """Test collecting events with valid GitHub response."""
        now = datetime.now(timezone.utc)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "environment": "production",
                "description": "Deploy v1.2.3",
                "creator": {"login": "deployer"},
                "url": "https://api.github.com/repos/org/repo/deployments/1",
                "ref": "main",
                "sha": "abc123",
            }
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value = mock_response

            start = now - timedelta(hours=1)
            end = now + timedelta(hours=1)

            result = await collect_github_events(
                start,
                end,
                {"github_token": "token", "github_repo": "org/repo"},
            )

            assert len(result) == 1
            assert result[0].event_type == EventType.DEPLOY
            assert result[0].source == EventSource.GITHUB
            assert result[0].title == "Deployment: production"
            assert result[0].user == "deployer"

    @pytest.mark.asyncio
    async def test_collect_filters_by_time_range(self):
        """Test that events outside time range are filtered."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=7)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "created_at": old_time.isoformat().replace("+00:00", "Z"),
                "environment": "production",
            }
        ]

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_client.return_value.__aenter__.return_value = mock_instance
            mock_instance.get.return_value = mock_response

            start = now - timedelta(hours=1)
            end = now

            result = await collect_github_events(
                start,
                end,
                {"github_token": "token", "github_repo": "org/repo"},
            )

            assert len(result) == 0


class TestCollectIncidentTimeline:
    """Tests for collect_incident_timeline function."""

    @pytest.mark.asyncio
    async def test_collect_incident_timeline_returns_tuple(self):
        """Test that collect_incident_timeline returns events and summary."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        events, summary = await collect_incident_timeline(
            incident_id="inc_001",
            start_time=start,
            end_time=end,
            context={},
        )

        assert isinstance(events, list)
        assert isinstance(summary, dict)
        assert "total_events" in summary

    @pytest.mark.asyncio
    async def test_collect_incident_timeline_sets_incident_id(self):
        """Test that incident_id is set in context."""
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        end = datetime.now(timezone.utc)

        events, summary = await collect_incident_timeline(
            incident_id="inc_001",
            start_time=start,
            end_time=end,
        )

        assert isinstance(summary, dict)
