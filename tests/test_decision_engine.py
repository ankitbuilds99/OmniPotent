"""Tests for the Decision Engine module."""

import pytest
from datetime import datetime, timezone

from app.decision_engine import (
    DecisionOption,
    SuccessPredictor,
    PredictionFeatures,
    OptionRanker,
    RankingCriteria,
    create_decision_engine,
)
from app.decision_engine.success_predictor import ActionType, RiskLevel
from app.decision_engine.option_ranker import RankingStrategy
from app.decision_engine.engine import (
    IncidentContext,
    DecisionOutcome,
    DecisionConfidence,
)


class TestSuccessPredictor:
    """Tests for SuccessPredictor."""

    @pytest.fixture
    def predictor(self):
        return SuccessPredictor()

    @pytest.fixture
    def sample_features(self):
        return PredictionFeatures(
            action_type=ActionType.RESTART_SERVICE,
            action_complexity=0.3,
            is_reversible=True,
            requires_downtime=False,
            estimated_duration_seconds=60,
            incident_severity="P2",
            incident_duration_minutes=30,
            affected_services_count=1,
            is_customer_facing=True,
            is_revenue_impacting=False,
            similar_incidents_count=10,
            similar_action_success_rate=0.9,
            service_change_frequency=2.0,
            time_since_last_deploy_hours=24.0,
            is_peak_traffic=False,
            is_change_freeze=False,
            is_maintenance_window=False,
            on_call_experience_level=0.8,
            root_cause_confidence=0.85,
            evidence_count=8,
            diagnostic_coverage=0.7,
        )

    @pytest.mark.asyncio
    async def test_predict_success_basic(self, predictor, sample_features):
        """Test basic success prediction."""
        result = await predictor.predict(sample_features)

        assert 0 <= result.probability <= 1
        assert 0 <= result.confidence <= 1
        assert isinstance(result.risk_level, RiskLevel)
        assert result.prediction_id is not None
        assert len(result.model_version) > 0

    @pytest.mark.asyncio
    async def test_predict_high_risk_action(self, predictor):
        """Test prediction for high-risk action."""
        features = PredictionFeatures(
            action_type=ActionType.DATABASE_MIGRATION,
            action_complexity=0.9,
            is_reversible=False,
            requires_downtime=True,
            estimated_duration_seconds=3600,
            incident_severity="P0",
            incident_duration_minutes=120,
            affected_services_count=5,
            is_customer_facing=True,
            is_revenue_impacting=True,
            similar_incidents_count=2,
            similar_action_success_rate=0.6,
            service_change_frequency=5.0,
            time_since_last_deploy_hours=2.0,
            is_peak_traffic=True,
            is_change_freeze=False,
            is_maintenance_window=False,
            on_call_experience_level=0.3,
            root_cause_confidence=0.4,
            evidence_count=2,
            diagnostic_coverage=0.3,
        )

        result = await predictor.predict(features)

        assert result.probability < 0.7
        assert result.risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]
        assert len(result.negative_factors) > 0
        assert len(result.recommended_safeguards) > 0

    @pytest.mark.asyncio
    async def test_predict_low_risk_action(self, predictor):
        """Test prediction for low-risk action."""
        features = PredictionFeatures(
            action_type=ActionType.FEATURE_FLAG_TOGGLE,
            action_complexity=0.1,
            is_reversible=True,
            requires_downtime=False,
            estimated_duration_seconds=5,
            incident_severity="P3",
            incident_duration_minutes=10,
            affected_services_count=1,
            is_customer_facing=False,
            is_revenue_impacting=False,
            similar_incidents_count=50,
            similar_action_success_rate=0.98,
            service_change_frequency=1.0,
            time_since_last_deploy_hours=48.0,
            is_peak_traffic=False,
            is_change_freeze=False,
            is_maintenance_window=True,
            on_call_experience_level=0.9,
            root_cause_confidence=0.95,
            evidence_count=15,
            diagnostic_coverage=0.9,
        )

        result = await predictor.predict(features)

        assert result.probability > 0.8
        assert result.risk_level in [RiskLevel.LOW, RiskLevel.MINIMAL]
        assert len(result.positive_factors) > 0

    @pytest.mark.asyncio
    async def test_record_outcome(self, predictor, sample_features):
        """Test recording prediction outcomes."""
        prediction = await predictor.predict(sample_features)

        await predictor.record_outcome(
            prediction_id=prediction.prediction_id,
            was_successful=True,
            actual_duration_seconds=55,
            required_rollback=False,
        )


class TestOptionRanker:
    """Tests for OptionRanker."""

    @pytest.fixture
    def ranker(self):
        return OptionRanker()

    @pytest.fixture
    def sample_options(self):
        return [
            DecisionOption(
                option_id="opt1",
                name="Restart Service",
                description="Restart the affected service",
                action_type="restart_service",
                success_probability=0.9,
                risk_score=0.2,
                estimated_time_minutes=5,
                estimated_cost_usd=0,
                impact_reduction_percent=80,
                is_reversible=True,
                is_automated=True,
                requires_approval=False,
                requires_downtime=False,
            ),
            DecisionOption(
                option_id="opt2",
                name="Rollback Deployment",
                description="Rollback to previous version",
                action_type="rollback",
                success_probability=0.85,
                risk_score=0.4,
                estimated_time_minutes=15,
                estimated_cost_usd=50,
                impact_reduction_percent=95,
                is_reversible=True,
                is_automated=True,
                requires_approval=True,
                requires_downtime=False,
            ),
            DecisionOption(
                option_id="opt3",
                name="Database Migration",
                description="Run corrective migration",
                action_type="database_migration",
                success_probability=0.7,
                risk_score=0.7,
                estimated_time_minutes=60,
                estimated_cost_usd=200,
                impact_reduction_percent=100,
                is_reversible=False,
                is_automated=False,
                requires_approval=True,
                requires_downtime=True,
            ),
        ]

    def test_rank_options_balanced(self, ranker, sample_options):
        """Test ranking with balanced strategy."""
        result = ranker.rank(sample_options, RankingStrategy.BALANCED)

        assert len(result.ranked_options) == 3
        assert result.ranked_options[0].rank == 1
        assert result.top_recommendation is not None
        assert result.strategy == RankingStrategy.BALANCED

    def test_rank_options_speed_focused(self, ranker, sample_options):
        """Test ranking with speed-focused strategy."""
        result = ranker.rank(sample_options, RankingStrategy.SPEED_FOCUSED)

        assert result.ranked_options[0].option.option_id == "opt1"

    def test_rank_options_safety_focused(self, ranker, sample_options):
        """Test ranking with safety-focused strategy."""
        result = ranker.rank(sample_options, RankingStrategy.SAFETY_FOCUSED)

        assert result.ranked_options[0].option.risk_score <= 0.4

    def test_rank_options_cost_focused(self, ranker, sample_options):
        """Test ranking with cost-focused strategy."""
        result = ranker.rank(sample_options, RankingStrategy.COST_FOCUSED)

        assert result.ranked_options[0].option.estimated_cost_usd < 100

    def test_rank_options_empty(self, ranker):
        """Test ranking with no options."""
        result = ranker.rank([], RankingStrategy.BALANCED)

        assert len(result.ranked_options) == 0
        assert result.top_recommendation is None

    def test_rank_options_with_context(self, ranker, sample_options):
        """Test ranking with incident context."""
        context = {
            "incident_severity": "P0",
            "is_revenue_impacting": True,
        }

        result = ranker.rank(sample_options, RankingStrategy.BALANCED, context=context)

        assert result.top_recommendation is not None

    def test_close_alternatives(self, ranker, sample_options):
        """Test identification of close alternatives."""
        result = ranker.rank(sample_options, RankingStrategy.BALANCED)

        for alt in result.close_alternatives:
            assert alt.percentage_of_top >= 90

    def test_option_scores(self, ranker, sample_options):
        """Test that all score components are present."""
        result = ranker.rank(sample_options, RankingStrategy.BALANCED)

        for ranked in result.ranked_options:
            assert "success_probability" in ranked.scores
            assert "risk_score" in ranked.scores
            assert "time_to_resolution" in ranked.scores
            assert "cost" in ranked.scores
            assert "reversibility" in ranked.scores


class TestDecisionEngine:
    """Tests for the main DecisionEngine."""

    @pytest.fixture
    def engine(self):
        return create_decision_engine()

    @pytest.fixture
    def sample_context(self):
        return IncidentContext(
            incident_id="INC-123",
            title="API Latency Spike",
            severity="P1",
            status="investigating",
            started_at=datetime.now(timezone.utc),
            duration_minutes=45,
            affected_services=["api-gateway", "user-service"],
            affected_users_estimate=5000,
            is_customer_facing=True,
            is_revenue_impacting=True,
            root_cause_hypothesis="Database connection pool exhaustion",
            root_cause_confidence=0.75,
            evidence_collected=[
                {"type": "metrics", "source": "prometheus"},
                {"type": "logs", "source": "elasticsearch"},
            ],
            is_peak_traffic=False,
            is_change_freeze=False,
            is_maintenance_window=False,
            recent_deployments=[],
            responders=["alice", "bob"],
            on_call_experience_level=0.8,
        )

    @pytest.fixture
    def sample_options(self):
        return [
            DecisionOption(
                option_id="opt1",
                name="Increase Connection Pool",
                description="Increase database connection pool size",
                action_type="config_change",
                success_probability=0.85,
                risk_score=0.3,
                estimated_time_minutes=10,
                estimated_cost_usd=0,
                impact_reduction_percent=90,
                is_reversible=True,
                is_automated=True,
                requires_approval=False,
                requires_downtime=False,
            ),
            DecisionOption(
                option_id="opt2",
                name="Restart Service",
                description="Restart the affected service",
                action_type="restart_service",
                success_probability=0.7,
                risk_score=0.4,
                estimated_time_minutes=5,
                estimated_cost_usd=0,
                impact_reduction_percent=60,
                is_reversible=True,
                is_automated=True,
                requires_approval=False,
                requires_downtime=False,
            ),
        ]

    @pytest.mark.asyncio
    async def test_decide_basic(self, engine, sample_context, sample_options):
        """Test basic decision making."""
        result = await engine.decide(sample_context, sample_options)

        assert result.decision_id is not None
        assert result.incident_id == "INC-123"
        assert result.outcome in list(DecisionOutcome)
        assert result.confidence in list(DecisionConfidence)
        assert 0 <= result.composite_score <= 1

    @pytest.mark.asyncio
    async def test_decide_with_ranking_strategy(self, engine, sample_context, sample_options):
        """Test decision with different ranking strategies."""
        for strategy in RankingStrategy:
            result = await engine.decide(sample_context, sample_options, strategy)
            assert result.ranking_result is not None

    @pytest.mark.asyncio
    async def test_decide_generates_safeguards(self, engine, sample_context, sample_options):
        """Test that decisions include safeguards."""
        result = await engine.decide(sample_context, sample_options)

        assert isinstance(result.safeguards, list)
        assert isinstance(result.prerequisites, list)
        assert isinstance(result.post_actions, list)

    @pytest.mark.asyncio
    async def test_decide_high_severity(self, engine, sample_options):
        """Test decision for high-severity incident."""
        context = IncidentContext(
            incident_id="INC-999",
            title="Complete Outage",
            severity="P0",
            status="active",
            started_at=datetime.now(timezone.utc),
            duration_minutes=5,
            affected_services=["all"],
            affected_users_estimate=100000,
            is_customer_facing=True,
            is_revenue_impacting=True,
            root_cause_hypothesis="Unknown",
            root_cause_confidence=0.2,
            evidence_collected=[],
            is_peak_traffic=True,
            is_change_freeze=False,
            is_maintenance_window=False,
            recent_deployments=[{"version": "1.0.1", "time": "now"}],
            responders=["alice"],
            on_call_experience_level=0.5,
        )

        result = await engine.decide(context, sample_options)

        assert len(result.warnings) > 0 or len(result.safeguards) > 0

    @pytest.mark.asyncio
    async def test_decide_during_change_freeze(self, engine, sample_options):
        """Test decision during change freeze."""
        context = IncidentContext(
            incident_id="INC-456",
            title="Minor Issue",
            severity="P3",
            status="investigating",
            started_at=datetime.now(timezone.utc),
            duration_minutes=10,
            affected_services=["backend"],
            affected_users_estimate=100,
            is_customer_facing=False,
            is_revenue_impacting=False,
            root_cause_hypothesis="Cache miss",
            root_cause_confidence=0.9,
            evidence_collected=[],
            is_peak_traffic=False,
            is_change_freeze=True,
            is_maintenance_window=False,
            recent_deployments=[],
            responders=["alice"],
            on_call_experience_level=0.8,
        )

        result = await engine.decide(context, sample_options)

        assert result.outcome in [
            DecisionOutcome.REQUIRE_APPROVAL,
            DecisionOutcome.PROCEED_WITH_CAUTION,
            DecisionOutcome.PROCEED,
        ]

    @pytest.mark.asyncio
    async def test_decide_no_options(self, engine, sample_context):
        """Test decision with no options."""
        result = await engine.decide(sample_context, [])

        assert result.recommended_option is None


class TestRankingCriteria:
    """Tests for RankingCriteria."""

    def test_criteria_from_strategy(self):
        """Test creating criteria from strategies."""
        for strategy in RankingStrategy:
            if strategy != RankingStrategy.CUSTOM:
                criteria = RankingCriteria.from_strategy(strategy)
                assert criteria.validate()

    def test_criteria_weights_sum_to_one(self):
        """Test that default criteria weights sum to 1."""
        criteria = RankingCriteria()
        assert criteria.validate()

    def test_criteria_to_dict(self):
        """Test criteria serialization."""
        criteria = RankingCriteria()
        data = criteria.to_dict()

        assert "success_probability" in data
        assert "risk_score" in data
        assert "time_to_resolution" in data


class TestPredictionFeatures:
    """Tests for PredictionFeatures."""

    def test_features_to_vector(self):
        """Test feature vector generation."""
        features = PredictionFeatures(
            action_type=ActionType.RESTART_SERVICE,
            action_complexity=0.5,
            is_reversible=True,
            requires_downtime=False,
            estimated_duration_seconds=60,
            incident_severity="P2",
            incident_duration_minutes=30,
            affected_services_count=1,
            is_customer_facing=True,
            is_revenue_impacting=False,
            similar_incidents_count=10,
            similar_action_success_rate=0.9,
            service_change_frequency=2.0,
            time_since_last_deploy_hours=24.0,
            is_peak_traffic=False,
            is_change_freeze=False,
            is_maintenance_window=False,
            on_call_experience_level=0.8,
            root_cause_confidence=0.85,
            evidence_count=8,
            diagnostic_coverage=0.7,
        )

        vector = features.to_vector()

        assert isinstance(vector, list)
        assert all(isinstance(v, float) for v in vector)
        assert all(0 <= v <= 1 for v in vector)

    def test_features_to_dict(self):
        """Test feature serialization."""
        features = PredictionFeatures(
            action_type=ActionType.ROLLBACK,
            action_complexity=0.6,
            is_reversible=True,
            requires_downtime=False,
            estimated_duration_seconds=300,
            incident_severity="P1",
            incident_duration_minutes=60,
            affected_services_count=3,
            is_customer_facing=True,
            is_revenue_impacting=True,
            similar_incidents_count=5,
            similar_action_success_rate=0.85,
            service_change_frequency=3.0,
            time_since_last_deploy_hours=4.0,
            is_peak_traffic=True,
            is_change_freeze=False,
            is_maintenance_window=False,
            on_call_experience_level=0.6,
            root_cause_confidence=0.7,
            evidence_count=6,
            diagnostic_coverage=0.5,
        )

        data = features.to_dict()

        assert data["action_type"] == "rollback"
        assert data["is_reversible"] is True
        assert data["incident_severity"] == "P1"
