"""Predictive Scaling - ML-based traffic prediction and auto-scaling."""

from app.predictive_scaling.predictor import (
    ScalingDirection,
    PredictionConfidence,
    TrafficDataPoint,
    TrafficPrediction,
    ScalingRecommendation,
    ScalingConfig,
    PredictiveScaler,
    create_predictive_scaler,
)

__all__ = [
    "ScalingDirection",
    "PredictionConfidence",
    "TrafficDataPoint",
    "TrafficPrediction",
    "ScalingRecommendation",
    "ScalingConfig",
    "PredictiveScaler",
    "create_predictive_scaler",
]
