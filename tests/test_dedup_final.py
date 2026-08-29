"""The last of the deduplication audit: the clock, the guards, and the digest.

Five things were still wrong after two rounds of fixes, and each of them is the
kind that reads as working:

1. `BusinessRuleEngine` evaluated `hour BETWEEN 23 AND 7` and `day IN (sat, sun)`
   against the wall clock, so four tests in `tests/test_deduplication_audit.py`
   asserted a different thing depending on when they ran. The clock is now a
   dependency; the tests below pin what each shipped calendar rule does at named
   instants, so the behaviour is written down instead of being whatever today is.

2. `_find_by_entity_matching` — Layer 2's *first* branch, tried before the text
   path — had no service guard. It was safe only because the service term is 0.35
   of the entity score and two different services therefore cannot reach the 0.85
   default. Nothing held that; lowering the threshold reopened cross-service
   merging on the branch the invariant was not written on.

3. `_find_by_text_similarity` ranked candidates with one `best_score` shared by
   the embedding and the lexical measure. The two are not the same scale, so a
   token-overlap 0.92 out-ranked a transformer cosine 0.86 and the wrong incident
   came back.

4. `ProcessAlertRequest.service` was a plain `str`, so `{"service": ""}` was a
   valid request for an alert Layer 2 will refuse to correlate with anything.

5. Layer 6 collected digests that went nowhere, and `resolve_incident` could not
   evict a closed incident from Layers 1-4, so its alerts stayed match candidates
   for the life of the process.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.billing import runtime as billing_runtime
from app.deduplication import api_router as dedup_api
from app.deduplication.models import AlertSeverity, Decision, RawAlert
from app.deduplication.pipeline import DeduplicationPipeline, PipelineConfig
from app.deduplication.semantic import SemanticConfig, SemanticSimilarityEngine
from app.deduplication.suppression import BusinessRuleEngine, DigestEntry

#: Named instants, so a rule about "the weekend" is asserted against an actual
#: weekend rather than against whatever day the suite happens to run on.
TUESDAY_AFTERNOON = datetime(2026, 3, 3, 14, 30)
TUESDAY_NIGHT = datetime(2026, 3, 3, 2, 0)
SATURDAY_AFTERNOON = datetime(2026, 3, 7, 14, 30)
SATURDAY_NIGHT = datetime(2026, 3, 7, 2, 0)


class _Clock:
    """A clock a test can move. Callable, so it drops straight into `set_clock`."""

    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


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


def _starter_pipeline(clock: _Clock) -> DeduplicationPipeline:
    """Fingerprint + suppression only, on a clock the test controls."""
    pipeline = DeduplicationPipeline(
        PipelineConfig(
            enable_layer2_semantic=False,
            enable_layer3_temporal=False,
            enable_layer4_topology=False,
            enable_layer5_ml=False,
        )
    )
    pipeline.suppression.set_clock(clock)
    return pipeline


# --------------------------------------------------------------------------
# 1. The clock is a dependency, and the calendar rules say what they do
# --------------------------------------------------------------------------
class TestBusinessRulesReadTheInjectedClock:
    @pytest.mark.parametrize(
        "moment,severity,expected_rule,expected_action",
        [
            # An ordinary working afternoon: neither calendar rule applies, and a
            # single occurrence is under the repetition threshold.
            (TUESDAY_AFTERNOON, AlertSeverity.LOW, None, ""),
            (TUESDAY_AFTERNOON, AlertSeverity.MEDIUM, None, ""),
            # 02:00 falls inside `hour BETWEEN 23 AND 7`, which only names `low`.
            (TUESDAY_NIGHT, AlertSeverity.LOW, "Low severity night suppression", "suppress"),
            (TUESDAY_NIGHT, AlertSeverity.MEDIUM, None, ""),
            # The weekend rule digests every non-critical severity, all day.
            (SATURDAY_AFTERNOON, AlertSeverity.MEDIUM, "Weekend non-critical suppression", "digest"),
            (SATURDAY_AFTERNOON, AlertSeverity.LOW, "Weekend non-critical suppression", "digest"),
            # Saturday night: the night rule is listed first, so it wins for `low`.
            (SATURDAY_NIGHT, AlertSeverity.LOW, "Low severity night suppression", "suppress"),
            (SATURDAY_NIGHT, AlertSeverity.MEDIUM, "Weekend non-critical suppression", "digest"),
            # A critical is excluded from both, at every hour of every day.
            (TUESDAY_NIGHT, AlertSeverity.CRITICAL, None, ""),
            (SATURDAY_NIGHT, AlertSeverity.CRITICAL, None, ""),
        ],
    )
    def test_each_shipped_calendar_rule_at_a_named_instant(
        self, moment, severity, expected_rule, expected_action
    ):
        engine = BusinessRuleEngine(lambda: moment)

        rule, action = engine.evaluate(
            _alert("batch", "DiskUsageWarning", "disk at 81%", severity),
            {"alert_count": 1},
        )

        assert action == expected_action
        assert (rule.name if rule else None) == expected_rule

    def test_the_same_engine_answers_differently_as_its_clock_moves(self):
        """The dependency is real: nothing else about the call changes."""
        clock = _Clock(TUESDAY_AFTERNOON)
        engine = BusinessRuleEngine(clock)
        alert = _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)

        assert engine.evaluate(alert, {"alert_count": 1}) == (None, "")

        clock.now = TUESDAY_NIGHT
        rule, action = engine.evaluate(alert, {"alert_count": 1})
        assert action == "suppress" and rule.name == "Low severity night suppression"

    def test_a_starter_pipeline_is_deterministic_at_any_hour(self):
        """The scenario `test_repeating_low_severity_alert_...` asserts, pinned.

        Six repeats of one LOW alert, run at four named instants. The guarantee is
        not "never suppressed" — at 02:00 the night rule suppressing a LOW alert is
        the rule working — it is that the whole sequence is a function of the clock
        the engine was given and of nothing else.

        The night sequence is worth reading rather than counting. `evaluate` returns
        the first matching rule and the repetition digest rule is listed first, so
        the night rule only owns the alerts under the repetition threshold: the
        first four (`alert_count` 0-3) are suppressed, and from the fifth
        `alert_count > 3` matches and digests, which passes the alert through to
        Layer 1 and opens an incident. An alert that keeps firing therefore stops
        being dropped and starts being collected — the direction that loses no
        signal — and the operator ends up with exactly one incident either way.
        """
        expected = {
            # Neither calendar rule applies; the repetition rule digests the fifth
            # and sixth occurrence and nothing is ever suppressed.
            TUESDAY_AFTERNOON: (
                ["new_incident"] + ["add_to_existing"] * 5,
                2,
            ),
            # The weekend rule digests every non-critical alert from the first one,
            # so all six land in the digest group.
            SATURDAY_AFTERNOON: (
                ["new_incident"] + ["add_to_existing"] * 5,
                6,
            ),
            TUESDAY_NIGHT: (
                ["suppress"] * 4 + ["new_incident", "add_to_existing"],
                2,
            ),
            SATURDAY_NIGHT: (
                ["suppress"] * 4 + ["new_incident", "add_to_existing"],
                2,
            ),
        }

        for moment, (sequence, digested) in expected.items():
            pipeline = _starter_pipeline(_Clock(moment))
            decisions = [
                pipeline.process_alert(
                    _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
                )
                for _ in range(6)
            ]

            assert [d.decision.value for d in decisions] == sequence, (
                f"{moment} gave an unexpected decision sequence"
            )
            # Whatever the hour, the repeats collapse onto one incident, and every
            # alert that was not suppressed but did match a digest rule is in the
            # buffer — a digested alert is collected, never dropped.
            assert len(pipeline.get_active_incidents()) == 1
            assert [d.count for d in pipeline.suppression.pending_digests()] == [
                digested
            ], f"{moment} digested an unexpected number of alerts"


# --------------------------------------------------------------------------
# 2. Layer 2's first branch enforces the same-service invariant explicitly
# --------------------------------------------------------------------------
class TestEntityMatchingWillNotCrossAServiceBoundary:
    @staticmethod
    def _engine() -> SemanticSimilarityEngine:
        return SemanticSimilarityEngine(
            SemanticConfig(
                use_entity_extraction=True,
                use_learned_equivalences=False,
                # Well under the 0.65 two different services can reach, which is
                # what used to make the missing guard invisible.
                similarity_threshold=0.6,
                lexical_similarity_threshold=0.6,
            )
        )

    def test_a_different_service_is_not_an_entity_match(self):
        engine = self._engine()
        incident_id = uuid4()
        engine.register_alert(
            _alert(
                "payments-api",
                "HighLatency",
                "p99 latency 2500 ms above threshold 500 ms timeout",
            ),
            incident_id,
        )

        query = _alert(
            "search-index",
            "HighLatency",
            "p99 latency 2600 ms above threshold 500 ms timeout",
        )

        # Same metric, same direction, same error type: 0.65 on the entity score,
        # over the 0.6 threshold. Only the service differs, and only the service
        # may stop it.
        assert engine._entity_similarity(
            engine._entity_extractor.extract(query),
            engine._entity_extractor.extract(
                engine._incident_alerts[incident_id][0]
            ),
        ) == pytest.approx(0.65)
        assert engine._find_by_entity_matching(query, 0.6) is None
        assert engine.find_similar(query) is None

    def test_the_same_service_still_matches_on_entities(self):
        """The guard rejects other services, not entity matching."""
        engine = self._engine()
        incident_id = uuid4()
        engine.register_alert(
            _alert(
                "payments-api",
                "HighLatency",
                "p99 latency 2500 ms above threshold 500 ms timeout",
            ),
            incident_id,
        )

        match = engine._find_by_entity_matching(
            _alert(
                "payments-api",
                "HighLatency",
                "p99 latency 2600 ms above threshold 500 ms timeout",
            ),
            0.6,
        )

        assert match is not None
        assert match.method == "entity_extraction"
        assert match.incident_id == incident_id

    def test_turning_the_invariant_off_turns_it_off_everywhere(self):
        """One switch, not one per code path."""
        engine = SemanticSimilarityEngine(
            SemanticConfig(
                use_learned_equivalences=False,
                similarity_threshold=0.6,
                lexical_similarity_threshold=0.6,
                require_same_service_for_text_match=False,
            )
        )
        incident_id = uuid4()
        engine.register_alert(
            _alert(
                "payments-api",
                "HighLatency",
                "p99 latency 2500 ms above threshold 500 ms timeout",
            ),
            incident_id,
        )

        match = engine._find_by_entity_matching(
            _alert(
                "search-index",
                "HighLatency",
                "p99 latency 2600 ms above threshold 500 ms timeout",
            ),
            0.6,
        )
        assert match is not None and match.incident_id == incident_id


# --------------------------------------------------------------------------
# 3. Two measures, two rankings
# --------------------------------------------------------------------------
class _FakeEmbedding(list):
    def tolist(self) -> list[float]:
        return list(self)


class _ScriptedModel:
    """Encodes by keyword, and refuses anything containing "flaky".

    `_get_embedding` catches an `encode` failure and returns None, and
    `register_alert` then caches that alert with an empty embedding — which is
    also what a candidate cached before the model loaded looks like. That is how
    one candidate set comes to hold both kinds of score.
    """

    VECTORS = {
        "queryvec": [1.0, 0.0],
        # cos(query, this) == 0.86 exactly.
        "candidatevec": [0.86, (1 - 0.86**2) ** 0.5],
    }

    def encode(self, text: str) -> _FakeEmbedding:
        if "flaky" in text:
            raise RuntimeError("encoder refused this input")
        for key, vector in self.VECTORS.items():
            if key in text:
                return _FakeEmbedding(vector)
        return _FakeEmbedding([0.0, 1.0])


class TestScoresFromDifferentMeasuresAreNotRankedAgainstEachOther:
    @staticmethod
    def _engine() -> SemanticSimilarityEngine:
        engine = SemanticSimilarityEngine(
            SemanticConfig(
                use_entity_extraction=False,
                use_learned_equivalences=False,
                similarity_threshold=0.8,
                lexical_similarity_threshold=0.8,
            )
        )
        engine._model = _ScriptedModel()
        engine._model_load_attempted = True
        return engine

    QUERY = ("payments-api", "HighLatency", "queryvec p99 latency 2500 ms over 500 ms")
    #: Scored by the model: cosine 0.86 against the query, almost no shared words.
    EMBEDDED = ("payments-api", "SlowCheckout", "candidatevec totally unrelated wording")
    #: The model refuses it, so it is scored lexically: 0.92 token overlap.
    UNEMBEDDED = ("payments-api", "HighLatency", "flaky p99 latency 2500 ms over 500 ms")

    @pytest.mark.parametrize("embedded_first", [True, False])
    def test_a_lexical_score_does_not_outrank_an_embedding_score(self, embedded_first):
        engine = self._engine()
        embedded_incident, lexical_incident = uuid4(), uuid4()

        registrations = [
            (self.EMBEDDED, embedded_incident),
            (self.UNEMBEDDED, lexical_incident),
        ]
        if not embedded_first:
            registrations.reverse()
        for (service, name, message), incident_id in registrations:
            engine.register_alert(_alert(service, name, message), incident_id)

        match = engine.find_similar(_alert(*self.QUERY))

        # The numbers: 0.86 from a transformer cosine, 0.92 from token overlap.
        # They answer different questions, and a shared `best_score` let the
        # larger one win regardless.
        assert match is not None
        assert match.method == "embedding"
        assert match.similarity_score == pytest.approx(0.86)
        assert match.incident_id == embedded_incident

    def test_the_lexical_best_is_still_returned_when_nothing_was_embedded(self):
        """Preferring the embedding must not mean discarding the other measure."""
        engine = self._engine()
        lexical_incident = uuid4()
        engine.register_alert(_alert(*self.UNEMBEDDED), lexical_incident)

        match = engine.find_similar(_alert(*self.QUERY))

        assert match is not None
        assert match.method == "lexical_overlap"
        assert match.incident_id == lexical_incident
        assert match.similarity_score == pytest.approx(0.923, abs=0.005)

    def test_the_best_of_several_lexical_candidates_still_wins(self):
        """Ranking within one measure is unchanged."""
        engine = self._engine()
        weak, strong = uuid4(), uuid4()
        engine.register_alert(
            _alert("payments-api", "HighLatency", "flaky p99 latency over 500 ms"), weak
        )
        engine.register_alert(_alert(*self.UNEMBEDDED), strong)

        match = engine.find_similar(_alert(*self.QUERY))

        assert match is not None and match.incident_id == strong


# --------------------------------------------------------------------------
# 4. A serviceless alert is rejected at the boundary
# --------------------------------------------------------------------------
class TestTheApiRejectsAnAlertWithNoService:
    @staticmethod
    def _payload(**overrides: Any) -> dict[str, Any]:
        payload = {
            "alert_name": "HighErrorRate",
            "service": "checkout",
            "source": "prometheus",
            "severity": "high",
            "message": "error rate 12%",
        }
        payload.update(overrides)
        return payload

    def test_an_empty_service_is_a_422_not_an_incident_of_its_own(self):
        from fastapi.testclient import TestClient

        from app.api import main as api

        dedup_api.reset_pipelines()
        with TestClient(api.app) as client:
            rejected = client.post(
                "/api/deduplication/process", json=self._payload(service="")
            )
            accepted = client.post("/api/deduplication/process", json=self._payload())

        assert rejected.status_code == 422, rejected.text
        assert "service" in rejected.text
        # The guard rejects the empty string, not the field.
        assert accepted.status_code == 200, accepted.text

    def test_the_model_itself_refuses_the_empty_string(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            dedup_api.ProcessAlertRequest(**self._payload(service=""))


# --------------------------------------------------------------------------
# 5a. The digest buffer has somewhere to go
# --------------------------------------------------------------------------
class _RecordingSink:
    def __init__(self, answer: bool | Exception = True):
        self.answer = answer
        self.calls: list[list[DigestEntry]] = []

    async def __call__(self, entries: list[DigestEntry]) -> bool:
        self.calls.append(list(entries))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def _digested_pipeline(clock: _Clock, sink: _RecordingSink) -> DeduplicationPipeline:
    pipeline = _starter_pipeline(clock)
    pipeline.suppression.set_digest_sink(sink)
    for _ in range(6):
        pipeline.process_alert(
            _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
        )
    assert pipeline.suppression.pending_digests(), "no digest group was collected"
    return pipeline


class TestDigestsAreDelivered:
    async def test_a_closed_window_is_handed_to_the_transport(self):
        clock = _Clock(TUESDAY_AFTERNOON)
        sink = _RecordingSink()
        pipeline = _digested_pipeline(clock, sink)

        # The rule asks for an hour's collection, so nothing is due yet.
        assert pipeline.suppression.has_due_digests() is False
        assert await pipeline.suppression.flush_digests() == {
            "status": "nothing_due",
            "groups": 0,
            "alerts": 0,
        }
        assert sink.calls == []

        clock.advance(minutes=61)
        assert pipeline.suppression.has_due_digests() is True
        result = await pipeline.suppression.flush_digests()

        assert result["status"] == "sent"
        assert result["groups"] == 1
        assert len(sink.calls) == 1
        delivered = sink.calls[0][0]
        assert delivered.service == "batch"
        assert delivered.alert_name == "DiskUsageWarning"
        assert delivered.count == result["alerts"]
        assert pipeline.suppression.pending_digests() == []

        stats = pipeline.suppression.get_suppression_stats()
        assert stats["digest_delivery"] == "wired"
        assert stats["digest_groups_delivered"] == 1
        assert stats["digest_alerts_delivered"] == delivered.count
        assert stats["digest_delivery_failures"] == 0
        assert stats["last_digest_delivery_error"] is None
        # Digested alerts still are not counted as suppressed.
        assert stats["total_alerts_suppressed"] == 0

    async def test_an_open_window_keeps_collecting(self):
        """Draining on every alert would send groups of one — the alert stream."""
        clock = _Clock(TUESDAY_AFTERNOON)
        sink = _RecordingSink()
        pipeline = _digested_pipeline(clock, sink)

        first = pipeline.suppression.pending_digests()[0].count
        clock.advance(minutes=30)
        pipeline.process_alert(
            _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
        )

        assert await pipeline.suppression.flush_digests() == {
            "status": "nothing_due",
            "groups": 0,
            "alerts": 0,
        }
        assert pipeline.suppression.pending_digests()[0].count == first + 1

    @pytest.mark.parametrize(
        "answer,detail",
        [
            (False, "the digest transport did not accept the groups"),
            (RuntimeError("webhook refused"), "RuntimeError: webhook refused"),
        ],
    )
    async def test_a_refused_hand_off_puts_the_groups_back(self, answer, detail):
        clock = _Clock(TUESDAY_AFTERNOON)
        sink = _RecordingSink(answer)
        pipeline = _digested_pipeline(clock, sink)
        collected = pipeline.suppression.pending_digests()[0].count

        clock.advance(minutes=61)
        result = await pipeline.suppression.flush_digests()

        assert result["status"] == "failed"
        assert result["detail"] == detail
        # Nothing was delivered, so nothing may be lost.
        remaining = pipeline.suppression.pending_digests()
        assert [e.count for e in remaining] == [collected]
        assert pipeline.suppression.has_due_digests() is True

        stats = pipeline.suppression.get_suppression_stats()
        assert stats["digest_alerts_delivered"] == 0
        assert stats["digest_delivery_failures"] == 1
        assert stats["last_digest_delivery_error"] == detail

    async def test_restoring_merges_with_whatever_fired_meanwhile(self):
        clock = _Clock(TUESDAY_AFTERNOON)
        sink = _RecordingSink(False)
        pipeline = _digested_pipeline(clock, sink)
        collected = pipeline.suppression.pending_digests()[0].count

        clock.advance(minutes=61)
        drained = pipeline.suppression.drain_due_digests()
        assert pipeline.suppression.pending_digests() == []

        # The same alert fires again while its group is out for delivery.
        for _ in range(6):
            pipeline.process_alert(
                _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
            )
        fresh = pipeline.suppression.pending_digests()[0].count

        pipeline.suppression.restore_digests(drained)

        merged = pipeline.suppression.pending_digests()
        assert len(merged) == 1
        assert merged[0].count == collected + fresh
        assert merged[0].first_seen == TUESDAY_AFTERNOON

    async def test_an_engine_with_no_sink_says_so_rather_than_reporting_success(self):
        pipeline = _starter_pipeline(_Clock(TUESDAY_AFTERNOON))

        assert await pipeline.suppression.flush_digests() == {
            "status": "not_wired",
            "groups": 0,
            "alerts": 0,
        }
        assert pipeline.suppression.get_suppression_stats()["digest_delivery"] == (
            "not_wired"
        )


class TestTheRouterIsTheDigestSeam:
    @pytest.fixture(autouse=True)
    def _clean_state(self):
        dedup_api.reset_pipelines()
        billing_runtime._BILLING_STORE = None
        billing_runtime._BILLING_API = None
        yield
        dedup_api.reset_pipelines()
        billing_runtime._BILLING_STORE = None
        billing_runtime._BILLING_API = None

    async def test_each_org_pipeline_is_created_with_a_sink_attached(self):
        pipeline = await dedup_api.get_pipeline()

        assert pipeline.suppression.get_suppression_stats()["digest_delivery"] == "wired"

    async def test_a_delivered_digest_carries_every_group(self, monkeypatch):
        from app.notifications import delivery

        sent: list[dict[str, Any]] = []

        async def _fake_notify(**kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"status": "sent", "results": []}

        monkeypatch.setattr(delivery, "notify", _fake_notify)

        entries = [
            DigestEntry(
                fingerprint="fp1",
                rule_name="Digest repeated non-critical alerts",
                service="batch",
                alert_name="DiskUsageWarning",
                severity="low",
                count=4,
                first_seen=TUESDAY_AFTERNOON,
                last_seen=TUESDAY_AFTERNOON,
            ),
            DigestEntry(
                fingerprint="fp2",
                rule_name="Digest repeated non-critical alerts",
                service="reports",
                alert_name="SlowQuery",
                severity="medium",
                count=2,
                first_seen=TUESDAY_AFTERNOON,
                last_seen=TUESDAY_AFTERNOON,
            ),
        ]

        assert await dedup_api._deliver_digest_groups(entries) is True

        assert len(sent) == 1
        note = sent[0]
        assert note["event"] == dedup_api.DIGEST_EVENT
        assert "6 repeated alert(s) in 2 group(s)" in note["title"]
        assert "batch: DiskUsageWarning" in note["body"]
        assert "reports: SlowQuery" in note["body"]
        assert note["data"]["alert_count"] == 6
        assert [g["fingerprint"] for g in note["data"]["groups"]] == ["fp1", "fp2"]

    @pytest.mark.parametrize("status", sorted(dedup_api.RETRYABLE_DIGEST_STATUSES))
    async def test_a_retryable_outcome_does_not_consume_the_backlog(
        self, monkeypatch, status
    ):
        from app.notifications import delivery

        async def _fake_notify(**kwargs: Any) -> dict[str, Any]:
            return {"status": status, "results": []}

        monkeypatch.setattr(delivery, "notify", _fake_notify)

        entry = DigestEntry(
            fingerprint="fp1",
            rule_name="Digest repeated non-critical alerts",
            service="batch",
            alert_name="DiskUsageWarning",
            severity="low",
            count=4,
            first_seen=TUESDAY_AFTERNOON,
            last_seen=TUESDAY_AFTERNOON,
        )

        assert await dedup_api._deliver_digest_groups([entry]) is False

    async def test_flushing_with_nothing_due_is_reported_as_such(self):
        from fastapi.testclient import TestClient

        from app.api import main as api

        with TestClient(api.app) as client:
            resp = client.post("/api/deduplication/digests/flush")

        assert resp.status_code == 200, resp.text
        # Not a bare 200: "nothing was due" and "it went out" are different
        # answers and the caller has to be able to tell them apart.
        assert resp.json()["status"] == "nothing_due"

    async def test_the_periodic_flush_covers_every_org_under_its_own_tenant(self):
        """A window closes with time, so an org that stopped alerting needs a timer.

        `POST /process` only flushes the org whose alert triggered it. Two orgs are
        set up here with due groups; the periodic helper has to reach both, and has
        to re-establish each org's tenant while it does — `_pipelines` is keyed by
        the *derived* org uuid, so nothing downstream could work the tenant out for
        itself.
        """
        from app.tenant_context import TenantConfig, run_with_tenant

        seen: list[tuple[str, int]] = []

        async def _tenant_recording_sink(entries: list[DigestEntry]) -> bool:
            from app.tenant_context import tenant_id

            seen.append((tenant_id(), sum(e.count for e in entries)))
            return True

        clock = _Clock(TUESDAY_AFTERNOON)
        for tenant, alerts in (("org-a", 5), ("org-b", 9)):
            pipeline = await run_with_tenant(
                TenantConfig(org_id=tenant), dedup_api.get_pipeline()
            )
            pipeline.suppression.set_clock(clock)
            pipeline.suppression.set_digest_sink(_tenant_recording_sink)
            for _ in range(alerts):
                pipeline.process_alert(
                    _alert("batch", "DiskUsageWarning", "disk at 81%", AlertSeverity.LOW)
                )

        assert len(dedup_api._pipelines) == 2
        # Nothing is due yet, so the helper attempts nothing and says nothing.
        assert await dedup_api.flush_all_due_digests() == []

        clock.advance(minutes=61)
        results = await dedup_api.flush_all_due_digests()

        assert [r["status"] for r in results] == ["sent", "sent"]
        # The rule is `alert_count > 3` and the count is taken before the alert is
        # recorded, so the 5th repeat is the first one digested: 5 alerts collect 1,
        # 9 collect 5. Each org's own number arrives under its own tenant.
        assert sorted(seen) == [("org-a", 1), ("org-b", 5)]

    async def test_a_digest_leaves_the_engine_over_the_real_post_route(
        self, monkeypatch
    ):
        """End to end on the path a monitoring system actually takes.

        Everything above tests a seam; this posts alerts to `POST
        /api/deduplication/process` and asserts one of them carried the digest out
        to `notify`. Without it the wiring could be correct in every part and still
        never run, which is the failure mode the whole item is about.
        """
        from fastapi.testclient import TestClient

        from app.api import main as api
        from app.notifications import delivery

        sent: list[dict[str, Any]] = []

        async def _fake_notify(**kwargs: Any) -> dict[str, Any]:
            sent.append(kwargs)
            return {"status": "sent", "results": []}

        monkeypatch.setattr(delivery, "notify", _fake_notify)

        payload = {
            "alert_name": "DiskUsageWarning",
            "service": "batch",
            "message": "disk at 81%",
            "severity": "low",
        }

        # Build this org's pipeline up front and freeze its clock before any alert
        # reaches it. A LOW alert posted at 02:00 real time would be suppressed by
        # the night rule and never digested at all, which is the wall-clock
        # dependence this whole file exists to remove — including from itself.
        pipeline = await dedup_api.get_pipeline()
        clock = _Clock(TUESDAY_AFTERNOON)
        pipeline.suppression.set_clock(clock)

        with TestClient(api.app) as client:
            for _ in range(6):
                resp = client.post("/api/deduplication/process", json=payload)
                assert resp.status_code == 200, resp.text
                # Nothing is due while the hour-long window is still open, so the
                # common request does no notification work at all.
                assert "digest_delivery" not in resp.json()

            # The window closes with time, not with traffic. Move this org's engine
            # past it and post once more: the arriving alert is what notices.
            clock.advance(minutes=61)

            resp = client.post("/api/deduplication/process", json=payload)

        assert resp.status_code == 200, resp.text
        # Two alerts, not six: the repetition rule is `alert_count > 3`, so the
        # fifth and sixth posts are the ones it collected. The seventh is an hour
        # later and starts a fresh count, which is why it is not in the group.
        assert resp.json()["digest_delivery"] == {
            "status": "sent",
            "groups": 1,
            "alerts": 2,
        }
        assert len(sent) == 1
        assert sent[0]["event"] == dedup_api.DIGEST_EVENT
        assert "batch: DiskUsageWarning" in sent[0]["body"]
        assert sent[0]["data"]["alert_count"] == 2
        assert pipeline.suppression.pending_digests() == []


# --------------------------------------------------------------------------
# 5b. Resolving an incident evicts it from every index that has one
# --------------------------------------------------------------------------
class TestResolvingAnIncidentEvictsItsAlerts:
    @staticmethod
    def _index_sizes(pipeline: DeduplicationPipeline) -> dict[str, int]:
        stats = pipeline.get_stats()
        return {
            "fingerprint": stats["fingerprint"]["total_entries"],
            "semantic": stats["semantic"]["total_alerts"],
            "temporal": stats["temporal"]["total_entries"],
            "cascade": stats["cascade"]["total_alerts"],
        }

    def test_every_layer_with_a_forget_api_is_told(self):
        pipeline = DeduplicationPipeline(PipelineConfig())
        for _ in range(3):
            pipeline.process_alert(
                _alert("payments-api", "PaymentGateway503", "503 from the gateway")
            )
        incident_id = pipeline.get_active_incidents()[0].incident_id

        assert self._index_sizes(pipeline) == {
            "fingerprint": 3,
            "semantic": 3,
            "temporal": 3,
            "cascade": 3,
        }

        assert pipeline.resolve_incident(incident_id) is True

        # Not "the match is refused later" — the candidate is gone. Refused work
        # is still work, and the indexes used to grow for the process's lifetime.
        assert self._index_sizes(pipeline) == {
            "fingerprint": 0,
            "semantic": 0,
            "temporal": 0,
            "cascade": 0,
        }
        # The incident itself is still readable; only its index entries went.
        assert len(pipeline.get_incident(incident_id).alerts) == 3

    def test_a_live_incident_is_untouched_by_another_being_resolved(self):
        pipeline = DeduplicationPipeline(PipelineConfig())
        doomed = pipeline.process_alert(
            _alert("payments-api", "PaymentGateway503", "503 from the gateway")
        )
        kept = pipeline.process_alert(
            _alert("search-index", "OOMKilled", "memory oom killed pod restart")
        )
        assert doomed.incident_id != kept.incident_id

        pipeline.resolve_incident(doomed.incident_id)

        assert self._index_sizes(pipeline) == {
            "fingerprint": 1,
            "semantic": 1,
            "temporal": 1,
            "cascade": 1,
        }
        again = pipeline.process_alert(
            _alert("search-index", "OOMKilled", "memory oom killed pod restart")
        )
        assert again.decision is Decision.ADD_TO_EXISTING
        assert again.incident_id == kept.incident_id

    def test_forgetting_an_unknown_incident_reports_zero_rather_than_guessing(self):
        pipeline = DeduplicationPipeline(PipelineConfig())
        pipeline.process_alert(_alert("payments-api", "Gateway503", "503s"))
        stranger = uuid4()

        assert pipeline._fingerprint_matcher.forget_incident(stranger) == 0
        assert pipeline._semantic_engine.forget_incident(stranger) == 0
        assert pipeline._temporal_correlator.forget_incident(stranger) == 0
        assert pipeline._cascade_detector.forget_incident(stranger) == 0
        assert self._index_sizes(pipeline) == {
            "fingerprint": 1,
            "semantic": 1,
            "temporal": 1,
            "cascade": 1,
        }

    def test_the_refire_still_opens_a_new_incident(self):
        """The behaviour the eviction has to preserve, through all six layers."""
        pipeline = DeduplicationPipeline(PipelineConfig())
        for _ in range(3):
            pipeline.process_alert(
                _alert("payments-api", "PaymentGateway503", "503 from the gateway")
            )
        resolved_id = pipeline.get_active_incidents()[0].incident_id
        pipeline.resolve_incident(resolved_id)

        refire = pipeline.process_alert(
            _alert("payments-api", "PaymentGateway503", "503 from the gateway")
        )

        assert refire.decision is Decision.NEW_INCIDENT
        assert refire.incident_id != resolved_id
        assert [i.incident_id for i in pipeline.get_active_incidents()] == [
            refire.incident_id
        ]

    @staticmethod
    def _layer3_only_pipeline() -> DeduplicationPipeline:
        """Temporal correlation alone, so the vote is the only thing deciding."""
        return DeduplicationPipeline(
            PipelineConfig(
                enable_layer1_fingerprint=False,
                enable_layer2_semantic=False,
                enable_layer4_topology=False,
                enable_layer5_ml=False,
                enable_layer6_suppression=False,
            )
        )

    @staticmethod
    def _burst(pipeline: DeduplicationPipeline, now: datetime) -> list[Any]:
        """Four alerts, resolved, then two more in the same 30-second window.

        `_find_in_window` returns the incident with the *most* matching alerts, so
        the four resolved ones out-number the one live one and win the vote. The
        pipeline then refuses the stale winner, and the second-place live incident
        is never even considered — the alert falls through to a new incident.
        """

        def at(name: str, offset: float) -> RawAlert:
            alert = _alert("payments-api", name, "something went wrong")
            alert.timestamp = now + timedelta(seconds=offset)
            return alert

        loud = [pipeline.process_alert(at("LoudAlert", i)) for i in range(4)]
        loud_incident = loud[0].incident_id
        assert [d.incident_id for d in loud] == [loud_incident] * 4
        assert pipeline.resolve_incident(loud_incident) is True

        first = pipeline.process_alert(at("QuietAlert", 5))
        second = pipeline.process_alert(at("QuietAlert", 6))
        return [loud_incident, first, second]

    def test_a_resolved_incident_cannot_outvote_a_live_one_in_layer_3(self):
        """Layer 3 picks the incident with the most alerts in the window.

        A resolved incident used to keep every one of its alerts in that vote, so
        it could beat a live incident and cost a correlation that was right: the
        pipeline refused the stale winner and the alert fell through to a brand new
        incident instead of joining the live one. Resolving now evicts the alerts,
        so the resolved incident is not on the ballot.
        """
        pipeline = self._layer3_only_pipeline()
        loud_incident, first, second = self._burst(pipeline, datetime.utcnow())

        # The first alert after the resolution has nothing live to join.
        assert first.decision is Decision.NEW_INCIDENT
        assert first.incident_id != loud_incident
        # The second joins it, because the only alert left in the window is its own.
        assert second.decision is Decision.ADD_TO_EXISTING
        assert second.incident_id == first.incident_id
        assert [i.incident_id for i in pipeline.get_active_incidents()] == [
            first.incident_id
        ]

    def test_without_the_eviction_the_live_incident_loses_the_vote(self, monkeypatch):
        """The same burst, with `forget_incident` put back to doing nothing.

        This is what the eviction buys, measured rather than asserted: leave the
        resolved incident's four alerts in the temporal index and the second quiet
        alert opens a *third* incident, because the resolved incident wins the vote
        4-to-1 and is then refused.
        """
        pipeline = self._layer3_only_pipeline()
        monkeypatch.setattr(
            pipeline._temporal_correlator,
            "forget_incident",
            lambda incident_id: 0,
        )

        loud_incident, first, second = self._burst(pipeline, datetime.utcnow())

        assert first.decision is Decision.NEW_INCIDENT
        assert second.decision is Decision.NEW_INCIDENT
        assert second.incident_id not in (loud_incident, first.incident_id)
        assert len(pipeline.get_active_incidents()) == 2


class TestForgetApisAreExact:
    def test_the_fingerprint_matcher_reports_what_it_removed(self):
        from app.deduplication.fingerprint import (
            FingerprintCalculator,
            FingerprintMatcher,
        )

        calculator = FingerprintCalculator()
        matcher = FingerprintMatcher()
        incident_id, other_id = uuid4(), uuid4()

        for _ in range(3):
            alert = _alert("payments-api", "Gateway503", "503s")
            matcher.register_alert(calculator.calculate(alert), alert, incident_id)
        other = _alert("search-index", "OOMKilled", "oom")
        other_fp = calculator.calculate(other)
        matcher.register_alert(other_fp, other, other_id)

        assert matcher.forget_incident(incident_id) == 3
        assert matcher.forget_incident(incident_id) == 0
        assert matcher.get_stats() == {
            "unique_fingerprints": 1,
            "total_entries": 1,
            "incidents_tracked": 1,
        }
        assert matcher.find_match(other_fp, other.timestamp) is not None

    def test_the_cascade_detector_drops_chains_rooted_in_a_forgotten_alert(self):
        from app.deduplication.temporal import CascadeDetector, TemporalConfig
        from app.deduplication.topology import ServiceDependencyGraph

        # A declared dependency, not the name-pattern guess: without the edge
        # `_calculate_cascade_confidence` tops out at 0.65 for a 5-second delay and
        # never reaches the 0.7 the detector requires, so there would be no chain
        # to forget and the test would pass for the wrong reason.
        graph = ServiceDependencyGraph()
        graph.add_dependency(upstream="orders-db", downstream="orders-api")

        detector = CascadeDetector(TemporalConfig())
        detector.set_dependency_graph(graph)
        incident_id = uuid4()

        now = datetime.utcnow()
        upstream = _alert("orders-db", "ConnectionsExhausted", "connections 500")
        upstream.timestamp = now
        downstream = _alert("orders-api", "HighLatency", "latency 3000 ms")
        downstream.timestamp = now + timedelta(seconds=5)

        detector.register_alert(upstream, incident_id)
        detector.register_alert(downstream, uuid4())
        chain = detector.detect_cascade(downstream)
        assert chain is not None and chain.root_alert_id == upstream.alert_id

        assert detector.forget_incident(incident_id) == 1

        assert detector.get_cascade_chain(chain.chain_id) is None
        assert detector.get_stats()["total_alerts"] == 1
        assert detector.detect_cascade(downstream) is None

    def test_the_semantic_engine_forgets_the_text_as_well_as_the_alert(self):
        engine = SemanticSimilarityEngine(
            SemanticConfig(use_entity_extraction=False, use_learned_equivalences=False)
        )
        incident_id = uuid4()
        alert = _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms")
        engine.register_alert(alert, incident_id)

        assert engine.forget_incident(incident_id) == 1

        stats = engine.get_stats()
        assert stats["total_alerts"] == 0
        assert stats["incidents_tracked"] == 0
        assert stats["cache_stats"]["total_entries"] == 0
        assert (
            engine.find_similar(
                _alert("payments-api", "HighLatency", "p99 latency 2500 ms over 500 ms")
            )
            is None
        )


def test_the_incident_id_type_is_still_a_uuid() -> None:
    """Cheap guard: the eviction API is keyed by incident id, not by index."""
    pipeline = DeduplicationPipeline(PipelineConfig())
    decision = pipeline.process_alert(_alert("payments-api", "Gateway503", "503s"))
    assert isinstance(decision.incident_id, UUID)
