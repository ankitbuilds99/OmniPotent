# API Changelog

All notable changes to the DevOps Copilot API will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- WebSocket authentication support (token in query param or first message)
- Rate limit headers (`X-RateLimit-Remaining`, `X-RateLimit-Reset`)

### Changed
- `/chat` endpoint now returns `tool_calls` array in response

### Deprecated
- `thread_id` in query params (use request body instead)

---

## [2.1.0] - 2026-07-15

### Added
- **SSE streaming** via `/chat/stream` endpoint
- **Structured reports** in `/chat` response (`report` field)
- **Calibrated confidence** in investigation results
- **Multi-tenant support** via `X-Tenant-ID` header

### Changed
- `POST /chat` response now includes `tokens_used` breakdown:
  ```json
  {
    "tokens_used": {
      "input": 1500,
      "output": 800,
      "cache_read": 500
    }
  }
  ```

### Fixed
- Rate limiter now respects `X-Forwarded-For` header

### Security
- Added request body size limit (1MB)
- Added message length limit (32KB)

---

## [2.0.0] - 2026-06-01

### Breaking Changes
- **Authentication required**: All endpoints except `/healthz` require Bearer token
- **Response format changed**: `answer` field renamed to `response`
- **Error format changed**: Now uses RFC 7807 Problem Details

### Migration Guide

#### Authentication
```bash
# Before (1.x)
curl https://api.example.com/chat -d '{"message": "..."}'

# After (2.0+)
curl https://api.example.com/chat \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "..."}'
```

#### Response Format
```json
// Before (1.x)
{"answer": "...", "thread_id": "..."}

// After (2.x)
{"response": "...", "thread_id": "...", "tokens_used": {...}}
```

### Added
- `/approve` endpoint for human-in-the-loop workflows
- `/audit` endpoint for audit trail queries
- `/feedback` endpoint for user feedback collection

---

## [1.0.0] - 2026-03-15

### Added
- Initial public API release
- `POST /chat` for investigations
- `GET /healthz` for health checks
- `GET /config` for runtime configuration

---

## API Versioning

The API uses URL path versioning:
- Current: `https://api.example.com/v2/chat`
- Legacy: `https://api.example.com/v1/chat` (deprecated, removal Q4 2026)

### Version Sunset Policy
- Deprecation notice: 6 months before removal
- Migration period: 3 months with dual support
- Legacy support: Security fixes only for 12 months
