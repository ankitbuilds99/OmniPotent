"""Layer 6 must not drop alerts, and Layer 2 must not invent similarity.

Two defaults in the deduplication engine turned noise reduction into signal loss:

1. `BusinessRuleEngine` shipped an always-on rule reading `alert_count > 3 AND
   window = 1h` with `action="suppress"`. Layer 6 runs before every correlation
   layer, so the 4th and every later occurrence of *any* alert in a rolling hour
   — criticals included — came back as `decision=suppress` with no incident. The
   rule's own `digest_instead=True` parameter was never read by anything, and the
   pipeline records an occurrence for suppressed alerts too, so the count could
   never fall back under the threshold and a critical that re-fired after its
   incident was resolved stayed invisible.

2. Layer 2 compared hash-bucket vectors produced by `_fallback_embedding` (the
   only live path, because `sentence-transformers` is in an optional extra that
   the image does not install). Those vectors were non-negative in every
   dimension, so the cosine between two completely unrelated alerts sat near 0.8
   and crossed the 0.85 threshold often enough to merge a search-index OOM into
   an auth-service certificate incident.

These tests pin the repaired behaviour: repeated alerts stay visible, digest
rules collect rather than drop, unparseable rule conditions match nothing, and
text similarity neither manufactures a score nor crosses a service boundary.

Every test below whose answer a calendar rule could change runs against a frozen
clock. Two of the shipped business rules read the calendar — "Low severity night
suppression" is `severity = low AND hour BETWEEN 23 AND 7`, "Weekend non-critical
suppression" is `severity NOT IN (critical, high) AND day IN (sat, sun)` — so a test
that let the engine read the wall clock asserted a different thing depending on the
hour and the weekday it happened to run at. Four of these did: at night the first
four LOW alerts came back SUPPRESS, and at the weekend a rule the rule-parsing tests
expect not to match matched every non-critical alert. That is worse than having no
test, because it passes in CI on a Tuesday afternoon and fails in production week.
`BusinessRuleEngine` and `SuppressionEngine` now take the clock as a dependency, and
these tests supply one, so what they assert does not move. What they assert has not
changed: the frozen instant is an ordinary weekday afternoon, which is when they used
to pass.

`TestSemanticMatchingDoesNotInventSimilarity` deliberately does not freeze anything.
It is about Layer 2, and it uses CRITICAL alerts, which both calendar rules exclude
by severity at every hour of every day — so pinning the clock there would hide the
fact that the exclusion is what makes those cases stable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from app.deduplication.models import AlertSeverity, Decision, RawAlert
from app.deduplication.pipeline import DeduplicationPipeline, PipelineConfig
from app.deduplication.semantic import SemanticConfig, SemanticSimilarityEngine
from app.deduplication.suppression import BusinessRule, BusinessRuleEngine

#: A Tuesday, 14:30 UTC. Deliberately outside both calendar rules: not a weekend,
#: and not inside the 23:00-07:00 night window. This is "an ordinary working
#: afternoon", the condition under which every assertion below was written.
WEEKDAY_AFTERNOON = datetime(2026, 3, 3, 14, 30, 0)


def _frozen_clock() -> datetime:
    return WEEKDAY_AFTERNOON


def _alert(
    service: str,
    alert_name: str,
    message: str = "",
    severity: AlertSeverity = AlertSeverity.CRITICAL,
) -> RawAlert:
    return RawAlert(
        service=service,
        alert_name=alert_name,
        message=message,
        severity=severity,
        timestamp=datetime.utcnow(),
    )


def _rule_engine() -> BusinessRuleEngine:
    """A rule engine whose answers do not depend on what day it is."""
    return BusinessRuleEngine(_frozen_clock)


def _fingerprint_only_pipeline() -> DeduplicationPipeline:
    """A Starter-plan pipeline: exact fingerprint matching plus suppression."""
    pipeline = DeduplicationPipeline(
        PipelineConfig(
            enable_layer1_fingerprint=True,
            enable_layer6_suppression=True,
            enable_layer2_semantic=False,
            enable_layer3_temporal=False,
            enable_layer4_topology=False,
            enable_layer5_ml=False,
        )
    )
    pipeline.suppression.set_clock(_frozen_clock)
    return pipeline


class TestRepeatedAlertsStayVisible:
    def test_repeating_critical_is_never_suppressed_by_a_business_rule(self):
        pipeline = _fingerprint_only_pipeline()

        decisions = [
            pipeline.process_alert(
                _alert("payments", "PaymentGateway503", "503 from payment gateway")
            )
            for _ in range(8)
        ]

        assert [d.decision for d in decisions].count(Decision.SUPPRESS) == 0
        assert all(d.incident_id is not None for d in decisions)
        # One incident, eight alerts on it: the noise is still collapsed, it is
        # just collapsed by fingerprint matching instead of being thrown away.
        assert len(pipeline.get_active_incidents()) == 1

    def test_critical_refiring_after_resolution_opens_a_new_incident(self):
        pipeline = _fingerprint_only_pipeline()

        for _ in range(5):
            pipeline.process_alert(_alert("payments", "PaymentGateway503", "503s"))

        incident_id = pipeline.get_active_incidents()[0].incident_id
        assert pipeline.resolve_incident(incident_id) is True

        refire = pipeline.process_alert(_alert("payments", "PaymentGateway503", "503s"))

        assert refire.decision is not Decision.SUPPRESS
        # An incident id alone is not enough: it used to be the *resolved*
        # incident's id, which reads as a handled alert while no incident is open.
        assert refire.incident_id != incident_id
        assert refire.decision is Decision.NEW_INCIDENT
        active = pipeline.get_active_incidents()
        assert [i.incident_id for i in active] == [refire.incident_id]

    def test_repeating_low_severity_alert_is_digested_not_dropped(self):
        pipeline = _fingerprint_only_pipeline()

        decisions = [
            pipeline.process_alert(
                _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
            )
            for _ in range(6)
        ]

        assert [d.decision for d in decisions].count(Decision.SUPPRESS) == 0

        digests = pipeline.suppression.pending_digests()
        assert [d.fingerprint for d in digests] == [decisions[-1].fingerprint]
        assert digests[0].count >= 1
        assert digests[0].service == "batch"

        stats = pipeline.suppression.get_suppression_stats()
        # The backlog is reported as undelivered rather than folded into the
        # suppression count, where it would have read as noise successfully removed.
        assert stats["digest_delivery"] == "not_wired"
        assert stats["pending_digest_alerts"] == digests[0].count
        assert stats["total_alerts_suppressed"] == 0

    def test_draining_digests_empties_the_buffer(self):
        pipeline = _fingerprint_only_pipeline()
        for _ in range(6):
            pipeline.process_alert(
                _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
            )

        drained = pipeline.suppression.drain_digests()

        assert drained and drained[0].service == "batch"
        assert pipeline.suppression.pending_digests() == []


class TestBusinessRuleConditionsFailClosed:
    def test_severity_exclusion_clause_is_honoured(self):
        engine = _rule_engine()

        rule, action = engine.evaluate(
            _alert("payments", "PaymentGateway503", severity=AlertSeverity.CRITICAL),
            {"alert_count": 50},
        )

        assert rule is None
        assert action == ""

    def test_low_severity_repetition_still_matches_the_digest_rule(self):
        engine = _rule_engine()

        rule, action = engine.evaluate(
            _alert("batch", "DiskUsageWarning", severity=AlertSeverity.LOW),
            {"alert_count": 50},
        )

        assert action == "digest"
        assert rule is not None and rule.name == "Digest repeated non-critical alerts"

    def test_unparseable_condition_matches_nothing(self):
        engine = _rule_engine()
        engine.add_rule(
            BusinessRule(
                name="Homegrown rule",
                condition="team = payments AND phase_of_moon = full",
                action="suppress",
            )
        )

        rule, action = engine.evaluate(
            _alert("payments", "Whatever", severity=AlertSeverity.MEDIUM),
            {"alert_count": 1},
        )

        assert rule is None
        assert action == ""

    def test_empty_condition_matches_nothing(self):
        engine = _rule_engine()
        engine.add_rule(BusinessRule(name="Rule with no condition", action="suppress"))

        rule, action = engine.evaluate(
            _alert("payments", "Whatever", severity=AlertSeverity.MEDIUM),
            {"alert_count": 1},
        )

        assert rule is None
        assert action == ""

    def test_understood_condition_still_matches(self):
        engine = _rule_engine()
        engine.add_rule(
            BusinessRule(
                name="Silence the noisy canary",
                condition="service = canary AND severity IN (low, info)",
                action="suppress",
            )
        )

        rule, action = engine.evaluate(
            _alert("canary", "Blip", severity=AlertSeverity.INFO),
            {"alert_count": 1},
        )
        assert action == "suppress" and rule is not None

        other, _ = engine.evaluate(
            _alert("payments", "Blip", severity=AlertSeverity.INFO),
            {"alert_count": 1},
        )
        assert other is None


UNRELATED_ALERTS = [
    ("auth-service", "TLSCertificateExpiring", "tls certificate expires in 3 days"),
    ("payments-api", "HighLatency", "p99 latency 2500ms above threshold 500ms"),
    ("search-index", "OOMKilled", "memory oom killed pod restart"),
    ("billing-worker", "QueueBacklog", "queue depth 50000 and growing"),
    ("cdn-edge", "Http5xxSpike", "5xx rate 12% spike at edge"),
    ("notification-svc", "HighErrorRate", "error rate 20% failures sending"),
]


class TestSemanticMatchingDoesNotInventSimilarity:
    def test_unrelated_alerts_each_get_their_own_incident(self):
        pipeline = DeduplicationPipeline(PipelineConfig())

        decisions = [
            pipeline.process_alert(_alert(service, name, message))
            for service, name, message in UNRELATED_ALERTS
        ]

        assert all(d.decision is Decision.NEW_INCIDENT for d in decisions), [
            (d.decision.value, d.reason) for d in decisions
        ]
        assert len(pipeline.get_active_incidents()) == len(UNRELATED_ALERTS)

    def test_text_match_requires_the_same_service(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(use_entity_extraction=False, use_learned_equivalences=False)
        )
        incident_id = uuid4()
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            incident_id,
        )

        same_service = engine.find_similar(
            _alert("payments-api", "HighLatency", "p99 latency 2650 ms over 500 ms")
        )
        assert same_service is not None
        assert same_service.incident_id == incident_id

        other_service = engine.find_similar(
            _alert("search-index", "HighLatency", "p99 latency 2650 ms over 500 ms")
        )
        assert other_service is None

    def test_unrelated_text_scores_near_zero(self):
        engine = SemanticSimilarityEngine()

        result = engine.compare_alerts(
            _alert("auth-service", "TLSCertificateExpiring", "tls certificate expires"),
            _alert("search-index", "OOMKilled", "memory oom killed pod restart"),
        )

        assert result["text_similarity"] < 0.3
        assert result["recommendation"] == "SEPARATE"

    def test_unmeasured_embedding_similarity_is_not_reported_as_zero(self):
        engine = SemanticSimilarityEngine()

        result = engine.compare_alerts(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms"),
            _alert("payments-api", "HighLatency", "p99 latency 2650 ms"),
        )
        stats = engine.get_stats()

        if stats["model_loaded"]:
            assert stats["similarity_backend"] == "embedding"
            assert stats["model_unavailable_reason"] is None
            assert result["text_similarity_method"] == "embedding"
            assert result["embedding_similarity"] == result["text_similarity"]
        else:
            # No model ran, so there is no embedding similarity to report. The
            # score that did get measured is labelled with the measure that
            # produced it instead of borrowing the embedding's name.
            assert stats["similarity_backend"] == "lexical_overlap"
            assert stats["model_unavailable_reason"]
            assert result["text_similarity_method"] == "lexical_overlap"
            assert result["embedding_similarity"] is None
            assert result["text_similarity"] > 0.0
