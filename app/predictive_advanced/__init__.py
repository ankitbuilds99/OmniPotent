"""Advanced Predictive Incidents Engine.

Goes beyond basic prediction with:
- Multi-signal fusion with correlation coefficients
- Similar past incident matching with pattern recognition
- Prevention playbook auto-suggestion
- Time-to-impact countdown with confidence intervals
- Causal chain prediction with cascade analysis
"""

from app.predictive_advanced.multi_signal_fusion import (
    SignalSource,
    CorrelatedSignal,
    FusedPrediction,
    MultiSignalFusion,
    get_multi_signal_fusion,
)
from app.predictive_advanced.similar_incidents import (
    SimilarityScore,
    SimilarIncident,
    IncidentPattern,
    SimilarIncidentMatcher,
    get_similar_incident_matcher,
)
from app.predictive_advanced.prevention_playbooks import (
    PreventionPlaybook,
    PlaybookStep,
    PlaybookSuggestion,
    PreventionPlaybookEngine,
    get_prevention_playbook_engine,
)
from app.predictive_advanced.time_to_impact import (
    ImpactCountdown,
    ConfidenceInterval,
    CountdownTracker,
    get_countdown_tracker,
)
from app.predictive_advanced.cascade_predictor import (
    CascadeStep,
    CascadePrediction,
    ServiceImpact,
    CascadePredictor,
    get_cascade_predictor,
)

__all__ = [
    # Multi-Signal Fusion
    "SignalSource",
    "CorrelatedSignal",
    "FusedPrediction",
    "MultiSignalFusion",
    "get_multi_signal_fusion",
    # Similar Incidents
    "SimilarityScore",
    "SimilarIncident",
    "IncidentPattern",
    "SimilarIncidentMatcher",
    "get_similar_incident_matcher",
    # Prevention Playbooks
    "PreventionPlaybook",
    "PlaybookStep",
    "PlaybookSuggestion",
    "PreventionPlaybookEngine",
    "get_prevention_playbook_engine",
    # Time to Impact
    "ImpactCountdown",
    "ConfidenceInterval",
    "CountdownTracker",
    "get_countdown_tracker",
    # Cascade Predictor
    "CascadeStep",
    "CascadePrediction",
    "ServiceImpact",
    "CascadePredictor",
    "get_cascade_predictor",
]
