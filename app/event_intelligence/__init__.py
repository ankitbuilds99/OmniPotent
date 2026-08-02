"""Event Intelligence Layer.

Advanced event processing including:
- Noise reduction with ML-based classification
- Severity prediction
- Blast radius estimation integration
- Feature store for time-series statistics
"""

from app.event_intelligence.noise_reduction import (
    NoiseReductionEngine,
    NoiseSignal,
    NoiseDecision,
    NoiseCategory,
)
from app.event_intelligence.severity_predictor import (
    SeverityPredictor,
    SeverityPrediction,
    PredictedSeverity,
)
from app.event_intelligence.feature_store import (
    FeatureStore,
    AlertFeatures,
)

__all__ = [
    "NoiseReductionEngine",
    "NoiseSignal",
    "NoiseDecision",
    "NoiseCategory",
    "SeverityPredictor",
    "SeverityPrediction",
    "PredictedSeverity",
    "FeatureStore",
    "AlertFeatures",
]
