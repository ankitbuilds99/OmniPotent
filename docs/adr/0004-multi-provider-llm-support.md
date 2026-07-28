# ADR-0004: Multi-Provider LLM Support

## Status
Accepted

## Context
Relying on a single LLM provider creates risk:
- Provider outages cause complete service failure
- Rate limiting blocks investigations during incidents
- Cost optimization requires provider flexibility
- Some providers better for certain tasks

## Decision
Support multiple LLM providers with automatic failover.

### Supported Providers
| Provider | Primary Model | Fast Model |
|----------|---------------|------------|
| Anthropic (default) | Claude Sonnet 4 | Claude Haiku 4.5 |
| OpenAI | GPT-4o | GPT-4o-mini |
| Google | Gemini 1.5 Pro | Gemini 1.5 Flash |
| Groq | Llama 3.3 70B | Llama 3.1 8B |
| DeepSeek | DeepSeek Chat | DeepSeek Chat |

### Failover Behavior
1. Primary provider fails with retryable error
2. Circuit breaker tracks failures
3. After threshold, switch to `COPILOT_FALLBACK_PROVIDER`
4. Fallback uses its own default models

## Consequences

### Positive
- Service continues during provider outages
- Cost optimization by provider selection
- A/B testing different models
- Regulatory compliance (data residency)

### Negative
- Different providers have different capabilities
- Prompt engineering may need adjustment
- Testing matrix increases

### Mitigations
- Eval suite runs against all supported providers
- Provider-specific prompt adjustments in `_build()`
- Circuit breaker prevents cascading to unhealthy fallback
