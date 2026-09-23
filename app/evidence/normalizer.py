"""Adapters from existing connector/MCP results to the public EvidenceItem."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel, JsonValue

from app.contracts.evidence import EvidenceItem
from app.evidence.models import (
    EvidenceContext,
    EvidenceSource,
    artifact_hash,
    canonical_artifact_json,
    json_value,
)


class EvidenceNormalizationError(ValueError):
    """A connector result could not be safely represented as evidence."""


class EvidenceRedactor:
    """Redacts secret-bearing fields and common inline credential formats."""

    _secret_key_pattern = re.compile(
        r"token|secret|password|passwd|credential|authorization|api[_-]?key|private[_-]?key|access[_-]?key",
        re.IGNORECASE,
    )
    _bearer_pattern = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
    _sensitive_parameter_pattern = re.compile(
        r"(?i)([\w.-]*(?:token|secret|password|passwd|credential|authorization|api[_-]?key|private[_-]?key|access[_-]?(?:key|token))\s*[:=]\s*)[^\s&,;]+"
    )
    _private_key_pattern = re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
    )

    def __init__(self, known_secrets: tuple[str, ...] = ()) -> None:
        self._known_secrets = tuple(secret for secret in known_secrets if secret)

    def redact(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): "[REDACTED]" if self._secret_key_pattern.search(str(key)) else self.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            text = self._private_key_pattern.sub("[REDACTED]", value)
            text = self._bearer_pattern.sub(r"\1[REDACTED]", text)
            text = self._sensitive_parameter_pattern.sub(r"\1[REDACTED]", text)
            for secret in self._known_secrets:
                text = text.replace(secret, "[REDACTED]")
            return text
        return value


def _to_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_to_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # MCP SDK content blocks and many connector wrappers expose public fields
    # through model_dump/dict; private attributes and arbitrary reprs are not
    # persisted because they can contain credentials or runtime state.
    for method_name in ("model_dump", "dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _to_plain(method())
            except Exception as exc:
                raise EvidenceNormalizationError("connector result serialization failed") from exc
    raise EvidenceNormalizationError(f"unsupported connector result type: {type(value).__name__}")


def _summary(payload: JsonValue, explicit: str | None, limit: int) -> str:
    if explicit is not None:
        return explicit[:limit]
    if isinstance(payload, str):
        return payload[:limit]
    return canonical_artifact_json(payload)[:limit]


def _safe_text(redactor: EvidenceRedactor, value: str | None) -> str | None:
    if value is None:
        return None
    redacted = redactor.redact(value)
    return redacted[:4096]


class EvidenceNormalizer:
    """Converts arbitrary existing tool results without changing the tool API."""

    def __init__(self, redactor: EvidenceRedactor | None = None) -> None:
        self._redactor = redactor or EvidenceRedactor()

    def normalize(
        self,
        result: Any,
        *,
        context: EvidenceContext,
        source: EvidenceSource,
        query_or_operation: str,
        observed_at: datetime,
        observation_window_start: datetime | None = None,
        observation_window_end: datetime | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        service: str | None = None,
        summary: str | None = None,
        artifact_reference: str | None = None,
        evidence_id: Any = None,
        supports_hypothesis_ids: tuple[Any, ...] = (),
        contradicts_hypothesis_ids: tuple[Any, ...] = (),
        metadata: dict[str, JsonValue] | None = None,
    ) -> EvidenceItem:
        if not query_or_operation.strip():
            raise EvidenceNormalizationError("query_or_operation is required")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise EvidenceNormalizationError("observed_at must be timezone-aware")
        try:
            plain = _to_plain(result)
            redacted = self._redactor.redact(plain)
            payload = json_value(redacted)
        except Exception as exc:
            if isinstance(exc, EvidenceNormalizationError):
                raise
            raise EvidenceNormalizationError("connector result is not valid JSON evidence") from exc

        digest = artifact_hash(payload)
        clean_metadata = self._redactor.redact(metadata or {})
        age_seconds = max(0.0, (context.now - observed_at).total_seconds())
        safe_source = _safe_text(self._redactor, source.source) or "[REDACTED]"
        safe_source_identity = _safe_text(self._redactor, source.source_identity) or "[REDACTED]"
        evidence_values: dict[str, Any] = {
            "source": safe_source,
            "evidence_type": source.source_type.value,
            "observed_at": observed_at,
            "payload": payload,
            "digest": digest,
            "tenant_id": context.tenant_id,
            "incident_id": context.incident_id,
            "source_type": source.source_type.value,
            "domain": source.domain,
            "source_identity": safe_source_identity,
            "tool_name": _safe_text(self._redactor, source.tool_name),
            "tool_version": _safe_text(self._redactor, source.tool_version),
            "connector_version": _safe_text(self._redactor, source.connector_version),
            "query_or_operation": _safe_text(self._redactor, query_or_operation),
            "observation_window_start": observation_window_start,
            "observation_window_end": observation_window_end,
            "freshness_seconds": age_seconds,
            "entity_type": _safe_text(self._redactor, entity_type),
            "entity_id": _safe_text(self._redactor, entity_id),
            "service": _safe_text(self._redactor, service),
            "summary": _safe_text(self._redactor, _summary(payload, summary, 4096)),
            "artifact_hash": digest,
            "artifact_reference": _safe_text(self._redactor, artifact_reference),
            "trust_level": "unverified",
            "supports_hypothesis_ids": supports_hypothesis_ids,
            "contradicts_hypothesis_ids": contradicts_hypothesis_ids,
            "instruction_allowed": False,
            "metadata": clean_metadata,
        }
        if evidence_id is not None:
            evidence_values["evidence_id"] = evidence_id
        try:
            return EvidenceItem.model_validate(evidence_values)
        except Exception as exc:
            raise EvidenceNormalizationError("normalized evidence failed contract validation") from exc

    def normalize_mcp_result(self, result: Any, **kwargs: Any) -> EvidenceItem:
        """Adapter entry point for MCP outputs; source semantics are caller supplied."""
        return self.normalize(result, **kwargs)
