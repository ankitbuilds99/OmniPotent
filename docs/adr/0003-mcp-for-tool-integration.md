# ADR-0003: Use MCP for Tool Integration

## Status
Accepted

## Context
The agent needs to interact with multiple external systems:
- Observability (Datadog, Sentry)
- Infrastructure (Kubernetes, GitHub)
- Alerting (PagerDuty)
- Custom tools (incident memory, repo analysis)

Options considered:
1. **Direct SDK integration**: Tight coupling, no isolation
2. **LangChain Tools**: Python-only, in-process
3. **MCP (Model Context Protocol)**: Process isolation, language-agnostic
4. **Custom RPC**: Full control, maintenance burden

## Decision
Use MCP (Model Context Protocol) for tool integration.

### Rationale
- **Isolation**: Each tool server is a separate process
- **Standardization**: Anthropic-backed protocol with growing adoption
- **Discovery**: Tools registered at runtime, not hardcoded
- **Testing**: Easy to swap live APIs for fixtures
- **Language-agnostic**: Servers can be written in any language

## Consequences

### Positive
- Adding a tool = adding a dict entry, zero agent changes
- Offline testing with fixture servers
- Process crash doesn't take down the agent
- Clear security boundary (tools can't access agent state)

### Negative
- IPC overhead for tool calls
- Additional processes to manage
- Protocol still evolving

### Mitigations
- `MultiServerMCPClient` manages process lifecycle
- Connection pooling reduces IPC overhead
- Pin MCP SDK version for stability
