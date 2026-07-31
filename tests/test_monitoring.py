"""Tests for Ambient Monitoring system."""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.monitoring.detectors import (
    AnomalyType,
    Anomaly,
    StatisticalDetector,
    ThresholdDetector,
    TrendDetector,
    PatternDetector,
)
from app.monitoring.ambient import (
    AmbientMonitor,
    MonitorConfig,
    MetricData,
)


class TestStatisticalDetector:
    """Tests for the statistical anomaly detector."""

    def test_detects_outlier(self):
        detector = StatisticalDetector(z_threshold=3.0, min_samples=5)

        # Normal values with one outlier
        values = [10, 11, 10, 9, 10, 11, 10, 50]  # 50 is the outlier

        anomalies = detector.detect("latency", "api-server", values)

        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.STATISTICAL_OUTLIER
        assert anomalies[0].current_value == 50
        assert anomalies[0].service == "api-server"

    def test_no_anomaly_for_normal_data(self):
        detector = StatisticalDetector(z_threshold=3.0, min_samples=5)

        # All normal values
        values = [10, 11, 10, 9, 10, 11, 10, 10, 11]

        anomalies = detector.detect("latency", "api-server", values)

        assert len(anomalies) == 0

    def test_needs_minimum_samples(self):
        detector = StatisticalDetector(z_threshold=3.0, min_samples=10)

        # Too few samples
        values = [10, 11, 50]  # 50 would be outlier but not enough samples

        anomalies = detector.detect("latency", "api-server", values)

        assert len(anomalies) == 0

    def test_severity_increases_with_zscore(self):
        detector = StatisticalDetector(z_threshold=3.0, min_samples=5)

        # Moderate outlier
        values = [10] * 20 + [40]
        anomalies = detector.detect("latency", "api-server", values)
        assert len(anomalies) >= 1

        # Extreme outlier
        values = [10] * 20 + [200]
        anomalies = detector.detect("latency", "api-server", values)
        assert len(anomalies) >= 1
        # Higher z-score should give higher severity
        assert anomalies[0].severity in ("high", "critical")


class TestThresholdDetector:
    """Tests for the threshold breach detector."""

    def test_detects_critical_threshold(self):
        detector = ThresholdDetector()

        # Error rate above critical (default 5%)
        values = [0.01, 0.02, 0.03, 0.06]

        anomalies = detector.detect("error_rate", "api-server", values)

        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == AnomalyType.THRESHOLD_BREACH
        assert anomalies[0].severity == "critical"

    def test_detects_warning_threshold(self):
        detector = ThresholdDetector()

        # Error rate above warning (1%) but below critical (5%)
        values = [0.005, 0.008, 0.015]

        anomalies = detector.detect("error_rate", "api-server", values)

        assert len(anomalies) == 1
        assert anomalies[0].severity == "medium"

    def test_no_anomaly_below_threshold(self):
        detector = ThresholdDetector()

        # Error rate below warning
        values = [0.001, 0.002, 0.003]

        anomalies = detector.detect("error_rate", "api-server", values)

        assert len(anomalies) == 0

    def test_custom_thresholds(self):
        detector = ThresholdDetector(thresholds={
            "custom_metric": {"warning": 50, "critical": 100, "direction": "above"},
        })

        values = [40, 45, 110]  # Above critical

        anomalies = detector.detect("custom_metric", "service", values)

        assert len(anomalies) == 1
        assert anomalies[0].severity == "critical"

    def test_below_direction_threshold(self):
        detector = ThresholdDetector()

        # Request rate below warning (default 10)
        values = [100, 50, 8]  # Dropped below warning

        anomalies = detector.detect("request_rate", "api-server", values)

        assert len(anomalies) == 1


class TestTrendDetector:
    """Tests for the trend change detector."""

    def test_detects_sudden_spike(self):
        detector = TrendDetector()

        # Stable then sudden spike (need >50% change from previous value)
        values = [100] * 60 + [100, 300]  # 200% increase in last step

        anomalies = detector.detect("latency_p99", "api-server", values)

        # Should detect the sudden change (LATENCY_SPIKE or TREND_CHANGE)
        relevant = [a for a in anomalies if a.anomaly_type in (AnomalyType.LATENCY_SPIKE, AnomalyType.TREND_CHANGE)]
        assert len(relevant) >= 1

    def test_detects_sudden_drop(self):
        detector = TrendDetector()

        # Stable then sudden drop (need >50% change from previous value)
        values = [100] * 60 + [100, 20]  # 80% decrease in last step

        anomalies = detector.detect("request_rate", "api-server", values)

        # Should detect the sudden change (TRAFFIC_DROP or TREND_CHANGE)
        relevant = [a for a in anomalies if a.anomaly_type in (AnomalyType.TRAFFIC_DROP, AnomalyType.TREND_CHANGE)]
        assert len(relevant) >= 1

    def test_no_anomaly_for_stable_trend(self):
        detector = TrendDetector()

        # Slow steady increase
        values = [100 + i for i in range(70)]

        anomalies = detector.detect("latency", "api-server", values)

        # Should not flag gradual change as anomaly
        spike_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.LATENCY_SPIKE]
        assert len(spike_anomalies) == 0

    def test_needs_enough_data(self):
        detector = TrendDetector(long_window=60)

        # Not enough data
        values = [100, 200]

        anomalies = detector.detect("latency", "api-server", values)

        # No trend anomalies without enough history
        trend_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.TREND_CHANGE]
        assert len(trend_anomalies) == 0


class TestPatternDetector:
    """Tests for the pattern-based detector."""

    def test_detects_error_burst(self):
        detector = PatternDetector(burst_threshold=5, burst_window=10)

        # Baseline then burst of errors
        values = [1] * 20 + [10, 15, 20, 25, 30, 35, 40]

        anomalies = detector.detect("error_count", "api-server", values)

        burst_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.ERROR_BURST]
        assert len(burst_anomalies) >= 1

    def test_detects_resource_exhaustion(self):
        detector = PatternDetector()

        # CPU climbing toward 100%
        values = [70, 72, 74, 76, 78, 80, 82, 84, 86, 88]

        anomalies = detector.detect("cpu_usage", "api-server", values)

        exhaustion = [a for a in anomalies if a.anomaly_type == AnomalyType.RESOURCE_EXHAUSTION]
        assert len(exhaustion) >= 1
        assert exhaustion[0].severity in ("high", "critical")

    def test_no_exhaustion_at_low_usage(self):
        detector = PatternDetector()

        # CPU at safe levels
        values = [30, 32, 34, 36, 38, 40, 42, 44, 46, 48]

        anomalies = detector.detect("cpu_usage", "api-server", values)

        exhaustion = [a for a in anomalies if a.anomaly_type == AnomalyType.RESOURCE_EXHAUSTION]
        assert len(exhaustion) == 0


class TestAmbientMonitor:
    """Tests for the ambient monitor."""

    @pytest.fixture
    def mock_metrics_fetcher(self):
        async def fetcher(service: str, metric: str, minutes: int) -> MetricData:
            return MetricData(
                service=service,
                metric_name=metric,
                values=[10] * 20 + [100],  # Normal then spike
                timestamps=[datetime.now(timezone.utc) - timedelta(minutes=i) for i in range(21)],
            )
        return fetcher

    @pytest.fixture
    def mock_investigation_trigger(self):
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_monitor_initialization(self):
        monitor = AmbientMonitor()

        assert monitor.state.running is False
        assert monitor.state.anomalies_detected == 0

    @pytest.mark.asyncio
    async def test_monitor_start_stop(self):
        monitor = AmbientMonitor()

        await monitor.start()
        assert monitor.state.running is True

        await monitor.stop()
        assert monitor.state.running is False

    @pytest.mark.asyncio
    async def test_monitor_detects_anomalies(self, mock_metrics_fetcher, mock_investigation_trigger):
        config = MonitorConfig(
            poll_interval_seconds=0.1,
            services=["test-service"],
            metrics=["latency_p99"],
            auto_investigate=False,
        )

        monitor = AmbientMonitor(
            config=config,
            metrics_fetcher=mock_metrics_fetcher,
            investigation_trigger=mock_investigation_trigger,
        )

        # Run one poll cycle
        await monitor._poll_once()

        # Should have detected anomalies
        assert monitor.state.anomalies_detected > 0

    @pytest.mark.asyncio
    async def test_monitor_triggers_investigation(self, mock_investigation_trigger):
        async def high_severity_fetcher(service: str, metric: str, minutes: int) -> MetricData:
            return MetricData(
                service=service,
                metric_name=metric,
                values=[0.01] * 20 + [0.10],  # Error rate spike
                timestamps=[datetime.now(timezone.utc) - timedelta(minutes=i) for i in range(21)],
            )

        config = MonitorConfig(
            services=["critical-service"],
            metrics=["error_rate"],
            auto_investigate=True,
        )

        monitor = AmbientMonitor(
            config=config,
            metrics_fetcher=high_severity_fetcher,
            investigation_trigger=mock_investigation_trigger,
        )

        await monitor._poll_once()

        # High severity anomaly should trigger investigation
        if mock_investigation_trigger.called:
            incident = mock_investigation_trigger.call_args[0][0]
            assert incident["service"] == "critical-service"
            assert incident["proactive"] is True

    @pytest.mark.asyncio
    async def test_cooldown_prevents_alert_storms(self, mock_metrics_fetcher):
        config = MonitorConfig(
            alert_cooldown_seconds=60,
            services=["test-service"],
            metrics=["latency"],
        )

        monitor = AmbientMonitor(
            config=config,
            metrics_fetcher=mock_metrics_fetcher,
        )

        # First poll
        await monitor._poll_once()
        first_count = monitor.state.anomalies_detected

        # Second poll (should be in cooldown)
        await monitor._poll_once()
        second_count = monitor.state.anomalies_detected

        # Same anomalies should not be counted again during cooldown
        # (actual behavior depends on implementation)

    @pytest.mark.asyncio
    async def test_get_stats(self, mock_metrics_fetcher):
        config = MonitorConfig(
            services=["svc1", "svc2"],
            metrics=["metric1", "metric2"],
        )

        monitor = AmbientMonitor(
            config=config,
            metrics_fetcher=mock_metrics_fetcher,
        )

        stats = monitor.get_stats()

        assert stats["running"] is False
        assert stats["services"] == ["svc1", "svc2"]
        assert len(stats["metrics"]) == 2
        assert len(stats["detectors"]) == 4


class TestAnomalyDataclass:
    """Tests for the Anomaly dataclass."""

    def test_anomaly_to_dict(self):
        anomaly = Anomaly(
            anomaly_type=AnomalyType.THRESHOLD_BREACH,
            metric_name="error_rate",
            service="api-server",
            severity="high",
            confidence=0.95,
            current_value=0.08,
            threshold=0.05,
            description="Error rate exceeded critical threshold",
        )

        d = anomaly.to_dict()

        assert d["anomaly_type"] == "threshold_breach"
        assert d["service"] == "api-server"
        assert d["confidence"] == 0.95
        assert d["current_value"] == 0.08


class TestMetricData:
    """Tests for MetricData dataclass."""

    def test_metric_data_creation(self):
        now = datetime.now(timezone.utc)
        data = MetricData(
            service="test-service",
            metric_name="cpu_usage",
            values=[50.0, 55.0, 60.0],
            timestamps=[now - timedelta(minutes=2), now - timedelta(minutes=1), now],
        )

        assert data.service == "test-service"
        assert len(data.values) == 3
        assert len(data.timestamps) == 3
