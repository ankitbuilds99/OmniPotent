"""Hallucination Detection for AI-Generated Content.

Detects potential hallucinations including:
- Fabricated facts
- Wrong attribution
- Temporal errors
- Logical fallacies
- Overconfidence
- Conflation of unrelated events
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from app.verification.evidence_collector import Evidence, EvidenceSource

log = logging.getLogger("omnipotent.verification.hallucination_detector")


class HallucinationType(str, Enum):
    FABRICATED_FACT = "fabricated_fact"
    WRONG_ATTRIBUTION = "wrong_attribution"
    TEMPORAL_ERROR = "temporal_error"
    LOGICAL_FALLACY = "logical_fallacy"
    OVERCONFIDENCE = "overconfidence"
    CONFLATION = "conflation"
    UNSUPPORTED_CLAIM = "unsupported_claim"


@dataclass
class HallucinationResult:
    """Result of hallucination detection."""
    detected: bool
    hallucination_type: HallucinationType | None = None
    explanation: str = ""
    severity: float = 0.0
    affected_claims: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "type": self.hallucination_type.value if self.hallucination_type else None,
            "explanation": self.explanation,
            "severity": self.severity,
            "affected_claims": self.affected_claims,
        }


class HallucinationDetector:
    """Detects potential hallucinations in AI-generated content."""

    def __init__(self, llm_client: Any = None):
        self.llm = llm_client
        self._fact_cache: dict[str, bool] = {}

    async def check_for_hallucinations(
        self,
        claim_text: str,
        evidence: list[Evidence],
        cited_sources: list[str] | None = None,
        cited_values: dict[str, Any] | None = None,
    ) -> HallucinationResult:
        """Check if a claim contains hallucinations."""
        cited_sources = cited_sources or []
        cited_values = cited_values or {}

        grounding_result = await self._check_fact_grounding(claim_text, evidence, cited_values)
        if grounding_result.detected:
            return grounding_result

        citation_result = await self._check_source_citations(cited_sources, evidence)
        if citation_result.detected:
            return citation_result

        temporal_result = await self._check_temporal_validity(claim_text, evidence)
        if temporal_result.detected:
            return temporal_result

        if self.llm:
            logical_result = await self._check_logical_consistency(claim_text)
            if logical_result.detected:
                return logical_result

        return HallucinationResult(detected=False)

    async def _check_fact_grounding(
        self,
        claim_text: str,
        evidence: list[Evidence],
        cited_values: dict[str, Any],
    ) -> HallucinationResult:
        """Check if claimed facts are grounded in evidence."""
        numeric_claims = self._extract_numeric_claims(claim_text)

        for value, unit, context in numeric_claims:
            found = False
            for ev in evidence:
                if self._value_in_evidence(value, unit, ev):
                    found = True
                    break

            if not found:
                return HallucinationResult(
                    detected=True,
                    hallucination_type=HallucinationType.FABRICATED_FACT,
                    explanation=f"Claimed value '{value} {unit}' not found in evidence",
                    severity=0.8,
                    affected_claims=[context],
                )

        for key, claimed_value in cited_values.items():
            if isinstance(claimed_value, (int, float)):
                found = False
                for ev in evidence:
                    if ev.metric_name and key.lower() in ev.metric_name.lower():
                        if ev.metric_value is not None:
                            tolerance = max(abs(claimed_value) * 0.1, 0.01)
                            if abs(ev.metric_value - claimed_value) <= tolerance:
                                found = True
                                break

                if not found:
                    return HallucinationResult(
                        detected=True,
                        hallucination_type=HallucinationType.FABRICATED_FACT,
                        explanation=f"Cited value for '{key}' ({claimed_value}) not verified in evidence",
                        severity=0.7,
                        affected_claims=[key],
                    )

        return HallucinationResult(detected=False)

    async def _check_source_citations(
        self,
        cited_sources: list[str],
        evidence: list[Evidence],
    ) -> HallucinationResult:
        """Check if source citations are accurate."""
        for source in cited_sources:
            source_found = any(
                source.lower() in ev.source.value.lower()
                or (ev.metric_name and source.lower() in ev.metric_name.lower())
                for ev in evidence
            )

            if not source_found:
                return HallucinationResult(
                    detected=True,
                    hallucination_type=HallucinationType.WRONG_ATTRIBUTION,
                    explanation=f"Cited source '{source}' not found in available evidence",
                    severity=0.6,
                    affected_claims=[source],
                )

        return HallucinationResult(detected=False)

    async def _check_temporal_validity(
        self,
        claim_text: str,
        evidence: list[Evidence],
    ) -> HallucinationResult:
        """Check if temporal claims are accurate."""
        time_refs = self._extract_time_references(claim_text)

        for time_ref, event_desc in time_refs:
            expected_time = self._parse_time_reference(time_ref)
            if expected_time is None:
                continue

            matching_evidence = [
                ev for ev in evidence
                if self._time_matches(expected_time, ev.timestamp)
            ]

            if not matching_evidence:
                return HallucinationResult(
                    detected=True,
                    hallucination_type=HallucinationType.TEMPORAL_ERROR,
                    explanation=f"Event '{event_desc[:50]}...' claimed at '{time_ref}' but no evidence supports this timing",
                    severity=0.5,
                    affected_claims=[event_desc],
                )

        return HallucinationResult(detected=False)

    async def _check_logical_consistency(
        self,
        claim_text: str,
    ) -> HallucinationResult:
        """Check for logical fallacies in the claim using LLM."""
        if not self.llm:
            return HallucinationResult(detected=False)

        prompt = f"""Analyze the following claim for logical fallacies or inconsistencies:

Claim: {claim_text}

Check for:
1. Circular reasoning (conclusion is used as a premise)
2. False causation (assuming correlation implies causation)
3. Contradictory statements within the claim
4. Unsupported conclusions (jumping to conclusions)
5. Overgeneralization (from specific to universal)

Respond with JSON only:
{{
  "has_fallacy": true/false,
  "fallacy_type": "type if any",
  "explanation": "brief explanation"
}}
"""

        try:
            response = await self.llm.complete(prompt=prompt, temperature=0.1)

            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(response[start:end])
                if result.get("has_fallacy"):
                    return HallucinationResult(
                        detected=True,
                        hallucination_type=HallucinationType.LOGICAL_FALLACY,
                        explanation=result.get("explanation", "Logical inconsistency detected"),
                        severity=0.6,
                        affected_claims=[claim_text[:100]],
                    )

        except Exception as e:
            log.debug("LLM logical consistency check failed: %s", e)

        return HallucinationResult(detected=False)

    def _extract_numeric_claims(self, text: str) -> list[tuple[float, str, str]]:
        """Extract numeric claims from text."""
        patterns = [
            r"(\d+\.?\d*)\s*(ms|milliseconds?)",
            r"(\d+\.?\d*)\s*(s|seconds?)",
            r"(\d+\.?\d*)\s*(%|percent)",
            r"(\d+\.?\d*)\s*(MB|GB|KB|bytes?)",
            r"(\d+\.?\d*)\s*(requests?/s|rps|qps)",
            r"(\d+\.?\d*)\s*(errors?)",
            r"(\d+\.?\d*)\s*(x|times)",
        ]

        claims: list[tuple[float, str, str]] = []
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                value = float(match.group(1))
                unit = match.group(2)
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                context = text[start:end]
                claims.append((value, unit, context))

        return claims

    def _value_in_evidence(
        self,
        value: float,
        unit: str,
        evidence: Evidence,
    ) -> bool:
        """Check if a numeric value exists in evidence."""
        if evidence.source == EvidenceSource.METRICS and evidence.metric_value is not None:
            tolerance = max(abs(value) * 0.1, 0.001)
            if abs(evidence.metric_value - value) <= tolerance:
                return True

            unit_lower = unit.lower()
            if "%" in unit_lower or "percent" in unit_lower:
                if abs(evidence.metric_value * 100 - value) <= tolerance * 100:
                    return True

        if evidence.data:
            data_str = json.dumps(evidence.data, default=str)
            if str(value) in data_str or str(int(value)) in data_str:
                return True

        return False

    def _extract_time_references(self, text: str) -> list[tuple[str, str]]:
        """Extract time references from text."""
        patterns = [
            r"at\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*(?:AM|PM))?)",
            r"(\d{1,2}\s+(?:minutes?|hours?|days?)\s+ago)",
            r"(yesterday|today|last\s+\w+)",
            r"(\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?)",
        ]

        refs: list[tuple[str, str]] = []
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                time_ref = match.group(1)
                start = max(0, match.start() - 50)
                end = min(len(text), match.end() + 20)
                context = text[start:end].strip()
                refs.append((time_ref, context))

        return refs

    def _parse_time_reference(self, time_ref: str) -> datetime | None:
        """Parse a time reference string to datetime."""
        now = datetime.now(timezone.utc)
        time_ref_lower = time_ref.lower()

        if "minute" in time_ref_lower:
            try:
                match = re.search(r"(\d+)", time_ref)
                if match:
                    minutes = int(match.group(1))
                    return now - timedelta(minutes=minutes)
            except Exception:
                pass

        if "hour" in time_ref_lower:
            try:
                match = re.search(r"(\d+)", time_ref)
                if match:
                    hours = int(match.group(1))
                    return now - timedelta(hours=hours)
            except Exception:
                pass

        if "day" in time_ref_lower:
            try:
                match = re.search(r"(\d+)", time_ref)
                if match:
                    days = int(match.group(1))
                    return now - timedelta(days=days)
            except Exception:
                pass

        if "yesterday" in time_ref_lower:
            return now - timedelta(days=1)

        if "today" in time_ref_lower:
            return now

        try:
            if "T" in time_ref:
                return datetime.fromisoformat(time_ref.replace("Z", "+00:00"))
            elif "-" in time_ref:
                return datetime.strptime(time_ref, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass

        return None

    def _time_matches(self, expected: datetime, evidence_time: datetime) -> bool:
        """Check if evidence time matches expected time within tolerance."""
        tolerance = timedelta(minutes=30)
        return abs(evidence_time - expected) <= tolerance


class ConfidenceOverconfidenceChecker:
    """Checks for overconfidence in AI-generated claims."""

    def __init__(self, historical_accuracy: dict[str, float] | None = None):
        self.historical_accuracy = historical_accuracy or {}

    def check_overconfidence(
        self,
        stated_confidence: float,
        evidence_count: int,
        evidence_quality: float,
        claim_type: str = "general",
    ) -> tuple[bool, str]:
        """Check if stated confidence is justified by evidence."""
        min_evidence_for_confidence = {
            0.9: 5,
            0.8: 3,
            0.7: 2,
            0.6: 1,
        }

        for threshold, required_evidence in sorted(
            min_evidence_for_confidence.items(), reverse=True
        ):
            if stated_confidence >= threshold:
                if evidence_count < required_evidence:
                    return True, f"Confidence {stated_confidence:.0%} requires at least {required_evidence} evidence pieces, found {evidence_count}"

        if stated_confidence > 0.7 and evidence_quality < 0.5:
            return True, f"High confidence {stated_confidence:.0%} but evidence quality is low ({evidence_quality:.0%})"

        historical = self.historical_accuracy.get(claim_type)
        if historical and stated_confidence > historical + 0.15:
            return True, f"Stated confidence {stated_confidence:.0%} exceeds historical accuracy of {historical:.0%} for {claim_type} claims"

        return False, ""
