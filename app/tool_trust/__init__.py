"""Trust boundary for calls from agents to MCP and connector tools."""

from app.tool_trust.gateway import (
    CapabilityGrant,
    CredentialScopeMetadata,
    ToolDefinition,
    ToolGatewayCode,
    ToolGatewayError,
    ToolIdentity,
    ToolTrustGateway,
    TrustedToolResult,
    approved_tool_execution,
    compatibility_gateway,
)

__all__ = [
    "CapabilityGrant",
    "CredentialScopeMetadata",
    "ToolDefinition",
    "ToolGatewayCode",
    "ToolGatewayError",
    "ToolIdentity",
    "ToolTrustGateway",
    "TrustedToolResult",
    "approved_tool_execution",
    "compatibility_gateway",
]
