# ADR-0002: Use LangGraph for Agent Orchestration

## Status
Accepted

## Context
We need an orchestration layer for the DevOps investigation agent that:
- Supports complex, multi-step workflows
- Enables human-in-the-loop approval flows
- Provides state persistence for long-running investigations
- Allows for iteration and reflection loops

Options considered:
1. **LangChain LCEL**: Simple chains, limited state management
2. **LangGraph**: State machine with persistence, interrupts, cycles
3. **Custom orchestration**: Full control, high maintenance burden
4. **CrewAI**: Multi-agent focus, less control over flow

## Decision
Use LangGraph for agent orchestration.

### Rationale
- **State persistence**: Built-in checkpointing enables investigation resumption
- **Human-in-the-loop**: Native `interrupt()` support for approval flows
- **Cycles**: Supports agent → tools → reflect → agent loops
- **Observability**: Integrates with LangSmith for tracing
- **Ecosystem**: Compatible with LangChain tools and MCP

## Consequences

### Positive
- Clean separation of nodes (plan, agent, tools, reflect, report)
- State machine makes flow explicit and testable
- Checkpointing enables crash recovery
- Easy to add new nodes or modify flow

### Negative
- Learning curve for state machine concepts
- Tied to LangChain ecosystem
- Debug complexity with async execution

### Mitigations
- Comprehensive documentation in `docs/ARCHITECTURE.md`
- Trace logging at each node transition
- Unit tests for each node in isolation
