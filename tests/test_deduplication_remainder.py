"""What the first pass at the deduplication audit left behind.

Round 1 stopped Layer 6 from dropping alerts and stopped Layer 2 from inventing
similarity, but four things it did not do were still wrong on a real request:

1. Resolving an incident told no layer's index about it, so an alert that fired
   again afterwards was added to the *resolved* incident. The caller got
   `add_to_existing` with an incident id while `GET /deduplication/incidents`
   returned nothing — a live outage with no open incident.
2. Layer 2 read `similarity_backend` once per call, so a candidate that a loaded
   model had not embedded was scored with token overlap and reported as
   `method="embedding"`, judged against the embedding threshold.
3. The same-service guard treated an empty service as a wildcard. `service`
   defaults to `""` on both `RawAlert` and the API's `ProcessAlertRequest`, so one
   alert posted without a service could be text-matched onto anything.
4. `pending_digests()` expired stale entries as a side effect and
   `get_suppression_stats()` calls it, so reading `/api/deduplication/stats`
   destroyed part of the digest backlog.

These tests pin each of those, plus the one claim that had to be made true rather
than fixed: the digest buffer is undelivered because nothing connects it to
`app/notifications/delivery.py`, not because no digest transport exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.deduplication import suppression as suppression_module
from app.deduplication.models import AlertSeverity, Decision, RawAlert
from app.deduplication.pipeline import DeduplicationPipeline, PipelineConfig
from app.deduplication.semantic import SemanticConfig, SemanticSimilarityEngine


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


class _FakeEmbedding(list):
    """What `SentenceTransformer.encode` hands back: a vector with `.tolist()`."""

    def tolist(self) -> list[float]:
        return list(self)


class _FlakyModel:
    """A loaded model that cannot encode every alert.

    Not hypothetical: `_get_embedding` catches an `encode` failure and returns
    None, and `register_alert` then caches the alert with an empty embedding. A
    candidate cached before the model finished loading looks the same.
    """

    def encode(self, text: str) -> _FakeEmbedding:
        if "flaky" in text:
            raise RuntimeError("encoder refused this input")
        return _FakeEmbedding([1.0, 0.0, 0.0])


class TestResolvedIncidentsDoNotAbsorbNewAlerts:
    def test_refire_through_every_layer_opens_a_new_incident(self):
        """The audit's own remedy, through the full six-layer pipeline."""
        pipeline = DeduplicationPipeline(PipelineConfig())

        for _ in range(3):
            pipeline.process_alert(
                _alert("payments-api", "PaymentGateway503", "503 from the gateway")
            )

        resolved_id = pipeline.get_active_incidents()[0].incident_id
        assert pipeline.resolve_incident(resolved_id) is True
        assert pipeline.get_active_incidents() == []

        refire = pipeline.process_alert(
            _alert("payments-api", "PaymentGateway503", "503 from the gateway")
        )

        assert refire.decision is Decision.NEW_INCIDENT
        assert refire.incident_id != resolved_id
        # The half of the failure that survived round 1: an operator watching
        # incidents has to be able to see the outage.
        assert [i.incident_id for i in pipeline.get_active_incidents()] == [
            refire.incident_id
        ]
        assert len(pipeline.get_incident(resolved_id).alerts) == 3

    def test_alerts_after_the_refire_join_the_new_incident(self):
        """The guard must not turn a repeating alert into an incident per alert."""
        pipeline = DeduplicationPipeline(PipelineConfig())

        first = pipeline.process_alert(_alert("payments-api", "Gateway503", "503s"))
        assert pipeline.resolve_incident(first.incident_id) is True

        refire = pipeline.process_alert(_alert("payments-api", "Gateway503", "503s"))
        again = pipeline.process_alert(_alert("payments-api", "Gateway503", "503s"))

        assert again.decision is Decision.ADD_TO_EXISTING
        assert again.incident_id == refire.incident_id
        assert len(pipeline.get_active_incidents()) == 1

    def test_a_full_incident_still_refuses_more_alerts(self):
        """The capacity limit moved into the same guard and has to still hold."""
        pipeline = DeduplicationPipeline(
            PipelineConfig(
                max_alerts_per_incident=2,
                enable_layer2_semantic=False,
                enable_layer3_temporal=False,
                enable_layer4_topology=False,
                enable_layer5_ml=False,
            )
        )

        decisions = [
            pipeline.process_alert(_alert("payments-api", "Gateway503", "503s"))
            for _ in range(3)
        ]

        assert [d.decision for d in decisions] == [
            Decision.NEW_INCIDENT,
            Decision.ADD_TO_EXISTING,
            Decision.NEW_INCIDENT,
        ]


class TestSemanticMatchNamesTheMeasureThatRan:
    def test_unembedded_candidate_is_reported_as_lexical(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(
                use_entity_extraction=False,
                use_learned_equivalences=False,
                similarity_threshold=0.5,
                lexical_similarity_threshold=0.8,
            )
        )
        engine._model = _FlakyModel()
        engine._model_load_attempted = True
        assert engine.similarity_backend == "embedding"

        incident_id = uuid4()
        engine.register_alert(
            _alert("payments-api", "flaky alert", "p99 latency 2500 ms"), incident_id
        )

        match = engine.find_similar(
            _alert("payments-api", "flaky alert", "p99 latency 2600 ms")
        )

        assert match is not None
        assert match.incident_id == incident_id
        # The score came out of `_lexical_similarity`; calling it "embedding"
        # invited the reader to trust a number no model produced.
        assert match.method == "lexical_overlap"

    def test_unembedded_candidate_is_held_to_the_lexical_threshold(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(
                use_entity_extraction=False,
                use_learned_equivalences=False,
                similarity_threshold=0.5,
                lexical_similarity_threshold=0.99,
            )
        )
        engine._model = _FlakyModel()
        engine._model_load_attempted = True

        engine.register_alert(
            _alert("payments-api", "flaky alert", "p99 latency 2500 ms"), uuid4()
        )

        # 0.87-ish token overlap clears the transformer's 0.5 but not the 0.99 the
        # measure that actually ran is calibrated for.
        assert (
            engine.find_similar(
                _alert("payments-api", "flaky alert", "p99 latency 2600 ms")
            )
            is None
        )

    def test_embedded_candidates_are_still_reported_as_embedding(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(
                use_entity_extraction=False,
                use_learned_equivalences=False,
                similarity_threshold=0.5,
                lexical_similarity_threshold=0.99,
            )
        )
        engine._model = _FlakyModel()
        engine._model_load_attempted = True

        incident_id = uuid4()
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms"), incident_id
        )

        match = engine.find_similar(
            _alert("payments-api", "HighLatency", "totally different words")
        )

        assert match is not None
        assert match.method == "embedding"
        assert match.incident_id == incident_id
        assert match.similarity_score == pytest.approx(1.0)

    def test_an_explicit_threshold_applies_to_whichever_measure_runs(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(
                use_entity_extraction=False,
                use_learned_equivalences=False,
                lexical_similarity_threshold=0.99,
            )
        )
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            uuid4(),
        )

        query = _alert("payments-api", "HighLatency", "p99 latency 2650 ms over 500 ms")

        assert engine.find_similar(query) is None
        assert engine.find_similar(query, threshold=0.5) is not None


class TestServicelessAlertsDoNotMergeIntoAnything:
    def test_an_alert_with_no_service_does_not_join_a_named_service(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(use_entity_extraction=False, use_learned_equivalences=False)
        )
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            uuid4(),
        )

        # `ProcessAlertRequest.service` is a plain `str`, so this is what a POST
        # with `"service": ""` reaches Layer 2 as.
        assert (
            engine.find_similar(
                _alert("", "HighLatency", "p99 latency 2500 ms over 500 ms")
            )
            is None
        )

    def test_two_serviceless_alerts_are_not_the_same_service(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(use_entity_extraction=False, use_learned_equivalences=False)
        )
        engine.register_alert(
            _alert("", "HighLatency", "p99 latency 2500 ms over 500 ms"), uuid4()
        )

        assert (
            engine.find_similar(
                _alert("", "HighLatency", "p99 latency 2500 ms over 500 ms")
            )
            is None
        )

    def test_an_alert_with_no_service_does_not_join_by_metric_equivalence(self):
        """The text path is not the only way a serviceless alert got merged."""
        engine = SemanticSimilarityEngine()
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            uuid4(),
        )

        assert engine.find_similar(_alert("", "SlowResponse", "response time 4000 ms")) is None

    def test_equivalent_metrics_on_the_same_service_still_merge(self):
        engine = SemanticSimilarityEngine()
        incident_id = uuid4()
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            incident_id,
        )

        match = engine.find_similar(
            _alert("payments-api", "SlowResponse", "response time 4000 ms")
        )

        assert match is not None and match.incident_id == incident_id

    def test_compare_alerts_will_not_recommend_merging_an_unknown_service(self):
        engine = SemanticSimilarityEngine()

        result = engine.compare_alerts(
            _alert("", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
        )

        assert result["recommendation"] == "SEPARATE"

    def test_named_same_service_alerts_still_merge(self):
        """The guard rejects unknown services, not every service."""
        engine = SemanticSimilarityEngine(
            SemanticConfig(use_entity_extraction=False, use_learned_equivalences=False)
        )
        incident_id = uuid4()
        engine.register_alert(
            _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms"),
            incident_id,
        )

        match = engine.find_similar(
            _alert("payments-api", "HighLatency", "p99 latency 2650 ms over 500 ms")
        )

        assert match is not None and match.incident_id == incident_id


class TestReadingStatsDoesNotDestroyTheDigestBacklog:
    def _pipeline_with_a_stale_digest(self) -> DeduplicationPipeline:
        pipeline = DeduplicationPipeline(
            PipelineConfig(
                enable_layer2_semantic=False,
                enable_layer3_temporal=False,
                enable_layer4_topology=False,
                enable_layer5_ml=False,
            )
        )
        for _ in range(6):
            pipeline.process_alert(
                _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
            )
        assert pipeline.suppression.pending_digests()

        for entry in pipeline.suppression.pending_digests():
            entry.last_seen = datetime.utcnow() - timedelta(days=3)
        return pipeline

    def test_stats_do_not_expire_entries(self):
        pipeline = self._pipeline_with_a_stale_digest()

        stats = pipeline.get_stats()["suppression"]

        assert stats["pending_digest_groups"] == 1
        assert pipeline.suppression.pending_digests(), (
            "reading /deduplication/stats discarded the digest backlog"
        )

    def test_repeated_stats_reads_report_the_same_backlog(self):
        pipeline = self._pipeline_with_a_stale_digest()

        first = pipeline.suppression.get_suppression_stats()
        second = pipeline.suppression.get_suppression_stats()

        assert first["pending_digest_alerts"] == second["pending_digest_alerts"]
        assert second["pending_digest_alerts"] > 0

    def test_expiry_happens_on_the_write_path(self):
        pipeline = self._pipeline_with_a_stale_digest()

        # Another alert being digested is a write; that is where the buffer is
        # allowed to shrink.
        for _ in range(6):
            pipeline.process_alert(
                _alert("batch", "OtherWarning", "something else", AlertSeverity.LOW)
            )

        remaining = {d.alert_name for d in pipeline.suppression.pending_digests()}
        assert remaining == {"OtherWarning"}

    def test_draining_still_expires_and_clears(self):
        pipeline = self._pipeline_with_a_stale_digest()

        assert pipeline.suppression.drain_digests() == []
        assert pipeline.suppression.pending_digests() == []


class TestDigestDeliveryIsDescribedAccurately:
    def test_the_transport_the_comment_used_to_deny_exists(self):
        from app.notifications import delivery

        assert callable(delivery.hold_for_digest)
        assert callable(delivery.flush_digest)

    def test_stats_say_not_wired_rather_than_not_implemented(self):
        engine = suppression_module.SuppressionEngine()

        assert suppression_module.DIGEST_DELIVERY_WIRED is False
        assert engine.get_suppression_stats()["digest_delivery"] == "not_wired"
