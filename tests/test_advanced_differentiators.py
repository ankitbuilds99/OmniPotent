"""Tests for advanced differentiator features.

Tests for:
- Predictive Incidents
- Code-Level Root Cause
- Change Risk Scoring
- AI Fix Generation
- Cross-Incident Learning
"""

from datetime import datetime, timedelta, timezone


class TestPredictiveSignals:
    """Tests for predictive signal correlation."""

    def test_register_signal(self):
        from app.predictive.signals import (
            SignalCorrelator,
            SignalType,
        )

        correlator = SignalCorrelator()
        signal = correlator.register_signal(
            signal_type=SignalType.MEMORY_USAGE,
            service="payment-service",
            threshold_warning=75.0,
            threshold_critical=90.0,
        )

        assert signal.signal_id is not None
        assert signal.service == "payment-service"
        assert signal.threshold_critical == 90.0

    def test_record_data_points(self):
        from app.predictive.signals import (
            SignalCorrelator,
            SignalType,
            SignalTrend,
        )

        correlator = SignalCorrelator()
        signal = correlator.register_signal(
            signal_type=SignalType.MEMORY_USAGE,
            service="payment-service",
            threshold_warning=75.0,
            threshold_critical=90.0,
        )

        for i in range(10):
            correlator.record_data_point(signal.signal_id, 60.0 + i * 2)

        updated = correlator.get_signal(signal.signal_id)
        assert updated is not None
        assert updated.current_value == 78.0
        assert updated.trend == SignalTrend.INCREASING

    def test_get_critical_signals(self):
        from app.predictive.signals import (
            SignalCorrelator,
            SignalType,
        )

        correlator = SignalCorrelator()
        signal = correlator.register_signal(
            signal_type=SignalType.CPU_USAGE,
            service="api-gateway",
            threshold_warning=70.0,
            threshold_critical=85.0,
        )

        correlator.record_data_point(signal.signal_id, 90.0)

        critical = correlator.get_critical_signals()
        assert len(critical) == 1
        assert critical[0].current_value == 90.0


class TestIncidentPredictions:
    """Tests for incident prediction."""

    def test_analyze_signals(self):
        from app.predictive.signals import SignalCorrelator, SignalType
        from app.predictive.predictions import IncidentPredictor

        correlator = SignalCorrelator()
        signal = correlator.register_signal(
            signal_type=SignalType.MEMORY_USAGE,
            service="payment-service",
            threshold_warning=75.0,
            threshold_critical=90.0,
        )

        for i in range(10):
            correlator.record_data_point(signal.signal_id, 70.0 + i * 3)

        predictor = IncidentPredictor(correlator=correlator, min_confidence=0.5)
        predictions = predictor.analyze_signals()

        assert len(predictions) >= 0

    def test_prediction_dashboard(self):
        from app.predictive.predictions import IncidentPredictor

        predictor = IncidentPredictor()
        data = predictor.get_dashboard_data()

        assert "active_predictions" in data
        assert "by_incident_type" in data


class TestBusinessImpact:
    """Tests for business impact forecasting."""

    def test_forecast_impact(self):
        from app.predictive.predictions import (
            PredictedIncident,
            PredictionConfidence,
            IncidentType,
            CausalChain,
        )
        from app.predictive.business_impact import ImpactForecaster
        from app.predictive.signals import SignalType, MetricSignal, SignalTrend

        signal = MetricSignal(
            signal_id="test_sig",
            signal_type=SignalType.MEMORY_USAGE,
            service="payment-service",
            current_value=85.0,
            threshold_warning=75.0,
            threshold_critical=90.0,
            trend=SignalTrend.INCREASING,
            trend_rate=2.0,
            time_to_threshold=timedelta(hours=2),
            baseline_mean=50.0,
            baseline_stddev=10.0,
        )

        chain = CausalChain(
            chain_id="chain_1",
            steps=[],
            root_cause_signal=signal,
            root_cause_description="Memory leak",
            total_probability=0.9,
        )

        prediction = PredictedIncident(
            prediction_id="pred_1",
            incident_type=IncidentType.RESOURCE_EXHAUSTION,
            affected_services=["payment-service", "checkout-api"],
            confidence=PredictionConfidence.HIGH,
            confidence_score=0.9,
            predicted_at=datetime.now(timezone.utc),
            expected_incident_time=datetime.now(timezone.utc) + timedelta(hours=2),
            causal_chain=chain,
            signals=[signal],
        )

        forecaster = ImpactForecaster()
        impact = forecaster.forecast_impact(prediction)

        assert impact.revenue_impact is not None
        assert impact.customer_impact is not None
        assert impact.sla_impact is not None
        assert impact.prevention_value > 0


class TestPreventionActions:
    """Tests for prevention action suggestions."""

    def test_suggest_actions(self):
        from app.predictive.predictions import (
            PredictedIncident,
            PredictionConfidence,
            IncidentType,
            CausalChain,
        )
        from app.predictive.prevention import PreventionEngine
        from app.predictive.signals import SignalType, MetricSignal, SignalTrend

        signal = MetricSignal(
            signal_id="test_sig",
            signal_type=SignalType.MEMORY_USAGE,
            service="payment-service",
            current_value=85.0,
            threshold_warning=75.0,
            threshold_critical=90.0,
            trend=SignalTrend.INCREASING,
            trend_rate=2.0,
            time_to_threshold=timedelta(hours=2),
            baseline_mean=50.0,
            baseline_stddev=10.0,
        )

        chain = CausalChain(
            chain_id="chain_1",
            steps=[],
            root_cause_signal=signal,
            root_cause_description="Memory leak",
            total_probability=0.9,
        )

        prediction = PredictedIncident(
            prediction_id="pred_1",
            incident_type=IncidentType.RESOURCE_EXHAUSTION,
            affected_services=["payment-service"],
            confidence=PredictionConfidence.HIGH,
            confidence_score=0.9,
            predicted_at=datetime.now(timezone.utc),
            expected_incident_time=datetime.now(timezone.utc) + timedelta(hours=2),
            causal_chain=chain,
            signals=[signal],
        )

        engine = PreventionEngine()
        actions = engine.suggest_actions(prediction)

        assert len(actions) >= 1
        assert any(a.action_type.value == "scale_up" for a in actions)


class TestSeasonalPatterns:
    """Tests for seasonal pattern detection."""

    def test_get_known_patterns(self):
        from app.predictive.seasonal import SeasonalDetector

        detector = SeasonalDetector()
        patterns = detector.get_all_patterns()

        assert len(patterns) >= 3
        assert any("Monday" in p.name for p in patterns)
        assert any("Friday" in p.name for p in patterns)

    def test_upcoming_occurrences(self):
        from app.predictive.seasonal import SeasonalDetector

        detector = SeasonalDetector()
        upcoming = detector.get_upcoming_occurrences(days_ahead=14)

        assert isinstance(upcoming, list)


class TestRootCauseAnalysis:
    """Tests for root cause analysis."""

    def test_analyze_incident(self):
        from app.rca.analyzer import RCAAnalyzer

        analyzer = RCAAnalyzer()
        analysis = analyzer.analyze_incident(
            incident_id="INC-123",
            error_message="NoneType has no attribute 'charge'",
            error_type="AttributeError",
            stack_trace='''File "billing/checkout.py", line 142, in process_payment
    payment_method.charge(amount)''',
            service="payment-service",
        )

        assert analysis.analysis_id is not None
        assert analysis.root_cause_type.value == "null_handling"
        assert "checkout.py" in analysis.crash_location.file_path

    def test_ai_explanation(self):
        from app.rca.analyzer import RCAAnalyzer

        analyzer = RCAAnalyzer()
        analysis = analyzer.analyze_incident(
            incident_id="INC-124",
            error_message="connection pool exhausted",
            error_type="ResourceError",
            stack_trace="File /app/db.py, line 50, in query",
            service="billing-db",
        )

        assert analysis.ai_explanation is not None
        assert len(analysis.ai_explanation) > 50


class TestCommitBisection:
    """Tests for commit bisection."""

    def test_start_bisection(self):
        from app.rca.bisection import CommitBisector, CommitInfo

        commits = []
        for i in range(8):
            commits.append(
                CommitInfo(
                    commit_hash=f"abc{i:03d}",
                    short_hash=f"abc{i:03d}",
                    author="dev",
                    author_email="dev@example.com",
                    date=datetime.now(timezone.utc) - timedelta(days=7 - i),
                    message=f"Commit {i}",
                    parents=[],
                )
            )

        bisector = CommitBisector()
        bisection_id = bisector.start_bisection(
            commits,
            known_good="abc000",
            known_bad="abc007",
        )

        assert bisection_id is not None
        assert bisector.get_remaining_steps() > 0

    def test_bisection_flow(self):
        from app.rca.bisection import CommitBisector, CommitInfo, CommitStatus

        commits = []
        for i in range(8):
            commits.append(
                CommitInfo(
                    commit_hash=f"abc{i:03d}",
                    short_hash=f"abc{i:03d}",
                    author="dev",
                    author_email="dev@example.com",
                    date=datetime.now(timezone.utc) - timedelta(days=7 - i),
                    message=f"Commit {i}",
                    parents=[],
                )
            )

        bisector = CommitBisector()
        bisector.start_bisection(commits, known_good="abc000", known_bad="abc007")

        next_commit = bisector.get_next_commit()
        assert next_commit is not None

        bisector.report_result(next_commit.commit_hash, CommitStatus.GOOD)
        status = bisector.get_bisection_status()
        assert status is not None


class TestCrossServiceTracing:
    """Tests for cross-service tracing."""

    def test_trace_error(self):
        from app.rca.cross_service import CrossServiceTracer

        tracer = CrossServiceTracer()
        trace = tracer.trace_error(
            entry_service="checkout-api",
            error_message="Payment failed",
        )

        assert trace.trace_id is not None
        assert trace.entry_service == "checkout-api"
        assert len(trace.services_involved) > 0


class TestTestGapDetection:
    """Tests for test gap detection."""

    def test_analyze_for_incident(self):
        from app.rca.test_gaps import TestGapDetector

        detector = TestGapDetector()
        gaps = detector.analyze_for_incident(
            file_path="billing/checkout.py",
            function_name="process_payment",
            error_type="AttributeError",
            error_message="NoneType has no attribute 'charge'",
            line_number=142,
        )

        assert len(gaps) >= 1
        assert any(g.gap_type.value == "null_check" for g in gaps)

    def test_generate_missing_tests(self):
        from app.rca.test_gaps import TestGapDetector

        detector = TestGapDetector()
        gaps = detector.analyze_for_incident(
            file_path="billing/checkout.py",
            function_name="process_payment",
            error_type="AttributeError",
            error_message="None",
            line_number=142,
        )

        test_code = detector.generate_missing_tests(gaps)
        assert "def test_" in test_code
        assert "pytest" in test_code


class TestChangeRiskScoring:
    """Tests for change risk scoring."""

    def test_score_change(self):
        from app.risk.scorer import ChangeRiskScorer

        scorer = ChangeRiskScorer()
        score = scorer.score_change(
            pr_number="#847",
            files_changed=[
                {"path": "billing/checkout.py", "additions": 50, "deletions": 10},
            ],
            lines_added=50,
            lines_removed=10,
            reviewers=["alice", "bob"],
            review_time_minutes=45,
            author="carol",
            deploy_day="Monday",
            deploy_hour=10,
        )

        assert score.score_id is not None
        assert 0 <= score.overall_score <= 10
        assert score.risk_level is not None
        assert len(score.factors) > 0


class TestBlastRadius:
    """Tests for blast radius analysis."""

    def test_analyze_blast_radius(self):
        from app.risk.blast_radius import BlastRadiusAnalyzer

        analyzer = BlastRadiusAnalyzer()
        analysis = analyzer.analyze(["billing/checkout.py", "payment/processor.py"])

        assert analysis.analysis_id is not None
        assert analysis.total_requests_at_risk >= 0
        assert analysis.visualization_data is not None


class TestDeployStrategy:
    """Tests for deploy strategy recommendations."""

    def test_recommend_strategy(self):
        from app.risk.scorer import ChangeRiskScorer
        from app.risk.deploy_strategy import StrategyRecommender

        scorer = ChangeRiskScorer()
        risk_score = scorer.score_change(
            pr_number="#847",
            files_changed=[{"path": "billing/checkout.py"}],
            lines_added=100,
            lines_removed=20,
            reviewers=["alice"],
            review_time_minutes=30,
            author="carol",
        )

        recommender = StrategyRecommender()
        strategy = recommender.recommend(risk_score)

        assert strategy.strategy_id is not None
        assert len(strategy.stages) >= 0
        assert strategy.timing is not None


class TestAIFixGeneration:
    """Tests for AI fix generation."""

    def test_generate_fix(self):
        from app.ai_fix.generator import AIFixGenerator

        generator = AIFixGenerator()
        fix = generator.generate_fix(
            incident_id="INC-123",
            file_path="billing/checkout.py",
            line_number=142,
            error_type="AttributeError",
            error_message="NoneType has no attribute 'charge'",
            function_name="process_payment",
        )

        assert fix.fix_id is not None
        assert len(fix.strategies) >= 1
        assert fix.recommended_strategy is not None
        assert len(fix.generated_tests) >= 1

    def test_validate_fix(self):
        from app.ai_fix.generator import AIFixGenerator

        generator = AIFixGenerator()
        fix = generator.generate_fix(
            incident_id="INC-124",
            file_path="billing/checkout.py",
            line_number=142,
            error_type="AttributeError",
            error_message="None",
            function_name="process_payment",
        )

        validation = generator.validate_fix(fix.fix_id)
        assert validation is not None
        assert validation.syntax_valid


class TestPatternMatcher:
    """Tests for code pattern matching."""

    def test_find_patterns(self):
        from app.ai_fix.patterns import PatternMatcher

        matcher = PatternMatcher()
        patterns = matcher.find_patterns_for_fix(
            error_type="AttributeError",
            file_path="billing/checkout.py",
        )

        assert len(patterns) >= 1
        assert any(p.pattern_type == "defensive" for p in patterns)


class TestIncidentClustering:
    """Tests for incident clustering."""

    def test_cluster_incidents(self):
        from app.learning.clustering import IncidentClusterer

        clusterer = IncidentClusterer()

        for i in range(5):
            clusterer.add_incident(
                incident_id=f"INC-{i}",
                error_type="AttributeError",
                error_message="NoneType has no attribute",
                affected_services=["billing"],
                duration_minutes=30,
                severity="SEV2",
            )

        analysis = clusterer.cluster_incidents()

        assert analysis.total_incidents == 5
        assert len(analysis.clusters) >= 1

    def test_find_similar_incidents(self):
        from app.learning.clustering import IncidentClusterer

        clusterer = IncidentClusterer()

        clusterer.add_incident(
            incident_id="INC-1",
            error_type="AttributeError",
            error_message="NoneType",
            affected_services=["billing"],
            duration_minutes=30,
            severity="SEV2",
        )
        clusterer.add_incident(
            incident_id="INC-2",
            error_type="AttributeError",
            error_message="NoneType error",
            affected_services=["billing"],
            duration_minutes=45,
            severity="SEV2",
        )

        similar = clusterer.find_similar_incidents("INC-1")
        assert len(similar) >= 1


class TestKnowledgeGraph:
    """Tests for knowledge graph."""

    def test_add_incident(self):
        from app.learning.knowledge_graph import IncidentKnowledgeGraph

        graph = IncidentKnowledgeGraph()
        node = graph.add_incident(
            incident_id="INC-123",
            title="Payment failure",
            affected_services=["payment-service", "billing"],
            resolved_by="alice",
            root_cause="null_handling",
        )

        assert node is not None
        assert node.node_id == "INC-123"

    def test_get_neighbors(self):
        from app.learning.knowledge_graph import (
            IncidentKnowledgeGraph,
            EdgeType,
        )

        graph = IncidentKnowledgeGraph()
        graph.add_incident(
            incident_id="INC-123",
            title="Payment failure",
            affected_services=["payment-service"],
            resolved_by="alice",
        )

        neighbors = graph.get_neighbors("INC-123", EdgeType.AFFECTS)
        assert len(neighbors) >= 1

    def test_generate_insights(self):
        from app.learning.knowledge_graph import IncidentKnowledgeGraph

        graph = IncidentKnowledgeGraph()

        for i in range(6):
            graph.add_incident(
                incident_id=f"INC-{i}",
                title=f"Issue {i}",
                affected_services=["payment-service"],
                resolved_by="alice",
            )

        insights = graph.generate_insights()
        assert isinstance(insights, list)


class TestRecommendations:
    """Tests for proactive recommendations."""

    def test_generate_recommendations(self):
        from app.learning.recommendations import RecommendationEngine

        engine = RecommendationEngine()
        recs = engine.generate_recommendations(
            incident_clusters=[
                {
                    "root_cause_category": "null_undefined",
                    "incident_count": 8,
                    "common_services": ["billing"],
                    "trend": "increasing",
                    "incidents": ["INC-1", "INC-2"],
                }
            ],
            total_incidents=20,
        )

        assert len(recs) >= 1
        assert recs[0].priority.value in ["critical", "high"]

    def test_generate_runbook(self):
        from app.learning.recommendations import RecommendationEngine

        engine = RecommendationEngine()
        runbook = engine.generate_runbook(
            root_cause="resource_exhaustion",
            resolutions=[
                {"incident_id": "INC-1", "resolution_time_minutes": 30, "successful": True},
                {"incident_id": "INC-2", "resolution_time_minutes": 45, "successful": True},
            ],
        )

        assert runbook.runbook_id is not None
        assert len(runbook.resolution_steps) > 0


class TestExpertiseMapping:
    """Tests for expertise mapping."""

    def test_record_resolution(self):
        from app.learning.expertise import ExpertiseMapper

        mapper = ExpertiseMapper()
        record = mapper.record_resolution(
            incident_id="INC-123",
            engineer="alice",
            service="payment-service",
            root_cause_type="null_handling",
            resolution_time_minutes=30,
        )

        assert record is not None
        assert record.engineer == "alice"

    def test_get_expertise(self):
        from app.learning.expertise import ExpertiseMapper

        mapper = ExpertiseMapper()

        for i in range(5):
            mapper.record_resolution(
                incident_id=f"INC-{i}",
                engineer="alice",
                service="payment-service",
                root_cause_type="null_handling",
                resolution_time_minutes=30 + i * 5,
            )

        expertise = mapper.get_engineer_expertise("alice")

        assert expertise.total_incidents_resolved == 5
        assert len(expertise.service_expertise) >= 1

    def test_recommend_engineer(self):
        from app.learning.expertise import ExpertiseMapper

        mapper = ExpertiseMapper()

        for i in range(3):
            mapper.record_resolution(
                incident_id=f"INC-{i}",
                engineer="alice",
                service="payment-service",
                root_cause_type="null_handling",
                resolution_time_minutes=25,
            )

        for i in range(2):
            mapper.record_resolution(
                incident_id=f"INC-{10 + i}",
                engineer="bob",
                service="user-service",
                root_cause_type="timeout",
                resolution_time_minutes=40,
            )

        recs = mapper.recommend_engineer(
            service="payment-service",
            root_cause_type="null_handling",
        )

        assert len(recs) >= 1
        assert recs[0].engineer == "alice"


class TestConfidenceCalibration:
    """Tests for confidence calibration."""

    def test_record_prediction(self):
        from app.predictive.calibration import ConfidenceCalibrator

        calibrator = ConfidenceCalibrator()
        record = calibrator.record_prediction(
            prediction_id="pred_1",
            confidence=0.85,
            expected_time=datetime.now(timezone.utc) + timedelta(hours=2),
            incident_type="resource_exhaustion",
            affected_services=["payment-service"],
        )

        assert record is not None
        assert record.predicted_confidence == 0.85

    def test_calculate_metrics(self):
        from app.predictive.calibration import (
            ConfidenceCalibrator,
            PredictionOutcome,
        )

        calibrator = ConfidenceCalibrator()

        for i in range(10):
            calibrator.record_prediction(
                prediction_id=f"pred_{i}",
                confidence=0.8 + (i % 3) * 0.05,
                expected_time=datetime.now(timezone.utc) + timedelta(hours=2),
                incident_type="test",
                affected_services=["service"],
            )
            calibrator.record_outcome(
                f"pred_{i}",
                PredictionOutcome.TRUE_POSITIVE if i % 2 == 0 else PredictionOutcome.FALSE_POSITIVE,
            )

        metrics = calibrator.calculate_metrics()

        assert metrics.total_predictions == 10
        assert metrics.brier_score >= 0
