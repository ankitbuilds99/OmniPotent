"""Incremental tool trust boundary for agent-initiated connector calls.

Tool results are evidence data, never instructions. The compatibility gateway
wraps existing LangChain MCP tools without changing their implementations.
Trust policy, limits, schemas, and hooks are injected by the caller.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from langchain_core.tools import BaseTool, ToolException
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, field_validator

from app import audit, policy, tenant_context
from app.contracts.common import DomainContract, require_aware_datetime
from app.evidence.models import EvidenceContext, EvidenceSource, EvidenceSourceType


class ToolGatewayCode(StrEnum):
    TENANT_MISMATCH = "tenant_mismatch"
    TENANT_REQUIRED = "tenant_required"
    UNKNOWN_TOOL = "unknown_tool"
    CAPABILITY_DENIED = "capability_denied"
    CAPABILITY_EXPIRED = "capability_expired"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    REQUEST_TOO_LARGE = "request_too_large"
    RESPONSE_TOO_LARGE = "response_too_large"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    TOOL_FAILURE = "tool_failure"


class ToolGatewayError(ToolException):
    """Stable, sanitized error exposed to existing LangChain tool callers."""

    def __init__(self, code: ToolGatewayCode, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(f"Tool gateway rejected the call ({code.value}).")


class ToolIdentity(DomainContract):
    server: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    version: str | None = Field(default=None, max_length=128)

    @property
    def key(self) -> str:
        return f"{self.server}:{self.name}"


class CredentialScopeMetadata(DomainContract):
    """Labels describing which credential scopes a connector uses, never values."""

    provider: str = Field(min_length=1, max_length=128)
    scopes: tuple[str, ...] = ()
    tenant_bound: bool = True


class CapabilityGrant(DomainContract):
    """Trusted, short-lived capability assertion supplied outside model input."""

    grant_id: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=256)
    subject: str = Field(min_length=1, max_length=256)
    capabilities: frozenset[str]
    issued_at: datetime
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def aware_times(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)

    @field_validator("capabilities")
    @classmethod
    def capabilities_are_nonempty(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not capability.strip() for capability in value):
            raise ValueError("capabilities cannot contain empty values")
        return value


class ToolDefinition(DomainContract):
    """Trusted registration data supplied by integration configuration."""

    identity: ToolIdentity
    required_capability: str = Field(min_length=1, max_length=256)
    response_schema: type[BaseModel] | None = Field(default=None, exclude=True)
    credential_scope: CredentialScopeMetadata
    timeout_seconds: float = Field(gt=0)
    max_request_bytes: int = Field(gt=0)
    max_response_bytes: int = Field(gt=0)

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)


class ToolProvenance(DomainContract):
    schema_version: str = "2.0"
    call_id: str
    tenant_id: str
    incident_id: str
    source_identity: str
    capability: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    evidence_id: str
    evidence_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware(cls, value: datetime) -> datetime:
        return require_aware_datetime(value)


class TrustedToolMetadata(DomainContract):
    identity: ToolIdentity
    tenant_id: str
    capability: str
    credential_scope: CredentialScopeMetadata
    provenance: ToolProvenance
    latency_ms: float = Field(ge=0)


class TrustedToolResult(DomainContract):
    """A trusted metadata envelope around explicitly untrusted connector data."""

    schema_version: str = "2.0"
    metadata: TrustedToolMetadata
    untrusted_content: JsonValue
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_injection_flags: tuple[str, ...] = ()
    instruction_allowed: Literal[False] = False

    def to_compatibility_text(self) -> str:
        """Serialize as a legacy tool string while preserving metadata/content split."""
        return self.model_dump_json()


RateLimitHook = Callable[[str, str, str], bool | Awaitable[bool]]
CapabilityAuthorizer = Callable[[CapabilityGrant, ToolDefinition, str], bool | Awaitable[bool]]
_approval_context: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tool_gateway_approval_context", default=False
)


@contextmanager
def approved_tool_execution(approved: bool):
    """Mark the immediate ToolNode invocation as human-approved, then restore."""
    token = _approval_context.set(approved)
    try:
        yield
    finally:
        _approval_context.reset(token)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _plain(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return _plain(value.dict())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class ToolTrustGateway:
    """Validates and observes tool calls before and after existing connectors."""

    def __init__(
        self,
        definitions: Mapping[str, ToolDefinition],
        *,
        authorizer: CapabilityAuthorizer | None = None,
        rate_limit_hook: RateLimitHook | None = None,
        request_context: Callable[[], tuple[str, str]] | None = None,
        compatibility_mode: bool = False,
    ) -> None:
        self._definitions = dict(definitions)
        self._authorizer = authorizer
        self._rate_limit_hook = rate_limit_hook
        self._request_context = request_context or self._default_request_context
        self._compatibility_mode = compatibility_mode

    @staticmethod
    def _default_request_context() -> tuple[str, str]:
        settings_tenant = tenant_context.get_tenant()
        if settings_tenant is None:
            return tenant_context.storage_org_id(), "default"
        # Thread/incident context is explicitly installed by the compatibility adapter.
        return settings_tenant.org_id, "default"

    async def invoke(
        self,
        *,
        identity: ToolIdentity,
        tool: BaseTool | None,
        args: Mapping[str, Any],
        grant: CapabilityGrant | None = None,
        incident_id: str | None = None,
        response: Callable[..., Awaitable[Any]] | None = None,
    ) -> TrustedToolResult:
        definition = self._definitions.get(identity.key)
        if definition is None or definition.identity != identity or (tool is None and response is None):
            raise self._deny(ToolGatewayCode.UNKNOWN_TOOL, identity, tenant_context.tenant_id())

        resolved_tenant, contextual_incident = self._request_context()
        incident = incident_id or contextual_incident
        from app.config import get_settings

        if get_settings().copilot_multi_tenant and tenant_context.get_tenant() is None:
            raise self._deny(ToolGatewayCode.TENANT_REQUIRED, identity, resolved_tenant)
        if not resolved_tenant or resolved_tenant in {"-", "unknown"}:
            raise self._deny(ToolGatewayCode.TENANT_REQUIRED, identity, resolved_tenant)
        supplied_tenant = args.get("tenant_id") or args.get("org_id")
        if supplied_tenant is not None and str(supplied_tenant) != resolved_tenant:
            raise self._deny(ToolGatewayCode.TENANT_MISMATCH, identity, resolved_tenant)

        if grant is not None:
            now = datetime.now(timezone.utc)
            if grant.expires_at <= now or grant.issued_at > now:
                raise self._deny(ToolGatewayCode.CAPABILITY_EXPIRED, identity, resolved_tenant)
            if grant.tenant_id != resolved_tenant:
                raise self._deny(ToolGatewayCode.TENANT_MISMATCH, identity, resolved_tenant)
            authorized = definition.required_capability in grant.capabilities
            if authorized and self._authorizer is not None:
                authorized = await _maybe_await(self._authorizer(grant, definition, resolved_tenant))
        elif self._compatibility_mode:
            authorized = self._legacy_authorized(identity, definition)
        else:
            authorized = False
        if not authorized:
            raise self._deny(ToolGatewayCode.CAPABILITY_DENIED, identity, resolved_tenant)

        request_payload = _plain(dict(args))
        try:
            request_bytes = _canonical(request_payload)
        except (TypeError, ValueError) as exc:
            raise self._deny(ToolGatewayCode.INVALID_REQUEST, identity, resolved_tenant) from exc
        if len(request_bytes) > definition.max_request_bytes:
            raise self._deny(ToolGatewayCode.REQUEST_TOO_LARGE, identity, resolved_tenant)
        if tool is not None:
            schema = getattr(tool, "args_schema", None)
            if schema is not None:
                try:
                    if isinstance(schema, type) and issubclass(schema, BaseModel):
                        schema.model_validate(request_payload)
                    elif isinstance(schema, Mapping):
                        validator_type = validator_for(schema)
                        validator_type.check_schema(schema)
                        validator_type(schema).validate(request_payload)
                    else:
                        TypeAdapter(schema).validate_python(request_payload)
                except Exception as exc:
                    raise self._deny(ToolGatewayCode.INVALID_REQUEST, identity, resolved_tenant) from exc

        if self._rate_limit_hook is not None:
            try:
                allowed = await _maybe_await(self._rate_limit_hook(resolved_tenant, identity.key, definition.required_capability))
            except Exception as exc:
                raise self._deny(ToolGatewayCode.RATE_LIMITED, identity, resolved_tenant) from exc
            if not allowed:
                raise self._deny(ToolGatewayCode.RATE_LIMITED, identity, resolved_tenant)

        call = response or getattr(tool, "coroutine", None)
        if call is None:
            raise self._deny(ToolGatewayCode.UNKNOWN_TOOL, identity, resolved_tenant)
        started = time.perf_counter()
        try:
            raw = await asyncio.wait_for(call(**dict(args)), timeout=definition.timeout_seconds)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            self._audit("timeout", identity, resolved_tenant, definition.required_capability)
            raise ToolGatewayError(ToolGatewayCode.TIMEOUT, retryable=True) from exc
        except ToolGatewayError:
            raise
        except Exception as exc:
            self._audit("tool_failure", identity, resolved_tenant, definition.required_capability)
            raise ToolGatewayError(ToolGatewayCode.TOOL_FAILURE, retryable=False) from exc

        try:
            artifact: Any = None
            if tool is not None and getattr(tool, "response_format", "content") == "content_and_artifact":
                if not isinstance(raw, tuple) or len(raw) != 2:
                    raise ValueError("tool response did not include content and artifact")
                raw_content, raw_artifact = raw
                content_part = _plain(raw_content)
                artifact = _plain(raw_artifact)
                content = {"content": content_part, "artifact": artifact}
            else:
                content = _plain(raw)
            if definition.response_schema is not None:
                value_to_validate = artifact if artifact is not None else content
                validated = definition.response_schema.model_validate(value_to_validate).model_dump(mode="json")
                if artifact is not None:
                    content = {"content": content["content"], "artifact": validated}
                else:
                    content = validated
            content = TypeAdapter(JsonValue).validate_python(content)
            response_bytes = _canonical(content)
        except Exception as exc:
            self._audit("invalid_response", identity, resolved_tenant, definition.required_capability)
            raise ToolGatewayError(ToolGatewayCode.INVALID_RESPONSE) from exc
        if len(response_bytes) > definition.max_response_bytes:
            raise self._deny(ToolGatewayCode.RESPONSE_TOO_LARGE, identity, resolved_tenant)

        from app.evidence.normalizer import EvidenceNormalizer, EvidenceRedactor
        from app.evidence.provenance import create_provenance

        now = datetime.now(timezone.utc)
        normalizer = EvidenceNormalizer(redactor=EvidenceRedactor())
        evidence = normalizer.normalize_mcp_result(
            content,
            context=EvidenceContext(tenant_id=resolved_tenant, incident_id=incident, now=now),
            source=EvidenceSource(
                source=identity.server,
                source_type=EvidenceSourceType.MCP_TOOL,
                source_identity=identity.key,
                tool_name=identity.name,
                tool_version=identity.version,
            ),
            query_or_operation=identity.name,
            observed_at=now,
        )
        content = evidence.payload
        response_bytes = _canonical(content)
        evidence_provenance = create_provenance(evidence, recorded_at=now)
        from app import guardrails

        serialized_content = json.loads(response_bytes)
        scan_text = serialized_content if isinstance(serialized_content, str) else _canonical(serialized_content).decode()
        injection_flags = tuple(guardrails.scan_for_injection(scan_text))
        result = TrustedToolResult(
            metadata=TrustedToolMetadata(
                identity=identity,
                tenant_id=resolved_tenant,
                capability=definition.required_capability,
                credential_scope=definition.credential_scope,
                provenance=ToolProvenance(
                    call_id=hashlib.sha256(request_bytes + response_bytes + str(now.timestamp()).encode()).hexdigest(),
                    tenant_id=resolved_tenant,
                    incident_id=incident,
                    source_identity=identity.key,
                    capability=definition.required_capability,
                    request_sha256=hashlib.sha256(request_bytes).hexdigest(),
                    response_sha256=hashlib.sha256(response_bytes).hexdigest(),
                    observed_at=now,
                    evidence_id=str(evidence.evidence_id),
                    evidence_digest=evidence.canonical_digest,
                    provenance_digest=evidence_provenance.provenance_digest or "",
                ),
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
            untrusted_content=content,
            content_sha256=hashlib.sha256(response_bytes).hexdigest(),
            prompt_injection_flags=injection_flags,
            instruction_allowed=False,
        )
        self._audit("completed", identity, resolved_tenant, definition.required_capability,
                    injection_flags=list(injection_flags), response_sha256=result.content_sha256)
        return result

    def wrap(self, tool: BaseTool, server: str, *, incident_id: str = "default") -> BaseTool:
        """Wrap an existing async LangChain tool, preserving its public schema/API."""
        identity = ToolIdentity(server=server, name=tool.name)
        if identity.key not in self._definitions:
            raise ValueError(f"tool is not registered with the gateway: {identity.key}")
        inner = getattr(tool, "coroutine", None)
        if inner is None:
            raise ValueError(f"tool {identity.key} has no async coroutine")

        async def guarded(**kwargs: Any) -> str | tuple[str, JsonValue]:
            result = await self.invoke(identity=identity, tool=tool, args=kwargs,
                                       incident_id=incident_id, response=inner)
            serialized = result.to_compatibility_text()
            if getattr(tool, "response_format", "content") == "content_and_artifact":
                return serialized, result.untrusted_content
            return serialized

        tool.coroutine = guarded
        return tool

    def _legacy_authorized(self, identity: ToolIdentity, definition: ToolDefinition) -> bool:
        if identity.name not in policy.classified_tools():
            return False
        if policy.classify(identity.name)["decision"] == "approve" and not _approval_context.get():
            return False
        from app.config import get_settings
        from app.tenancy.models import can

        settings = get_settings()
        tenant = tenant_context.get_tenant()
        if settings.copilot_multi_tenant:
            return bool(tenant and tenant.org_id and can(tenant.role, "run_investigation"))
        # In single-tenant mode, tools keep their previous behavior but unknown
        # tools still fail closed through classified_tools().
        return True

    def _deny(self, code: ToolGatewayCode, identity: ToolIdentity, tenant_id: str) -> ToolGatewayError:
        self._audit(code.value, identity, tenant_id, None)
        return ToolGatewayError(code)

    @staticmethod
    def _audit(event: str, identity: ToolIdentity, tenant_id: str, capability: str | None, **fields: Any) -> None:
        audit.record(
            f"tool_gateway.{event}", tool_server=identity.server, tool_name=identity.name,
            tenant_id=tenant_id, capability=capability, **fields,
        )


def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
        return value
    return value


def compatibility_gateway(
    tools: list[BaseTool],
    server: str,
    *,
    incident_id: str,
    timeout_seconds: float,
    max_request_bytes: int,
    max_response_bytes: int,
) -> list[BaseTool]:
    """Create explicit trusted registrations for classified legacy MCP tools.

    Registry entries derive from trusted server/tool identity and existing
    policy classification; model-provided names or tenant ids never register tools.
    """
    definitions: dict[str, ToolDefinition] = {}
    for tool in tools:
        identity = ToolIdentity(server=server, name=tool.name)
        if tool.name not in policy.classified_tools():
            # Keep the discovered tool visible for migration compatibility, but
            # calls will fail closed because no gateway definition exists.
            continue
        definitions[identity.key] = ToolDefinition(
            identity=identity,
            required_capability=f"tool.invoke:{identity.key}",
            credential_scope=CredentialScopeMetadata(provider=server),
            timeout_seconds=timeout_seconds,
            max_request_bytes=max_request_bytes,
            max_response_bytes=max_response_bytes,
        )
    gateway = ToolTrustGateway(definitions, compatibility_mode=True)
    wrapped = []
    for tool in tools:
        key = f"{server}:{tool.name}"
        if key in definitions and getattr(tool, "coroutine", None) is not None:
            gateway.wrap(tool, server, incident_id=incident_id)
        else:
            # An unclassified tool remains discoverable for backward-compatible
            # prompt construction but its invocation cannot reach the connector.
            inner = getattr(tool, "coroutine", None)
            if inner is not None:
                async def denied(*args: Any, **kwargs: Any) -> Any:
                    audit.record(
                        "tool_gateway.unknown_tool", tool_server=server,
                        tool_name=tool.name, tenant_id=tenant_context.tenant_id(),
                    )
                    raise ToolGatewayError(ToolGatewayCode.UNKNOWN_TOOL)
                tool.coroutine = denied
        wrapped.append(tool)
    return wrapped
