# DevOps Copilot API Reference

## Base URL
```
https://your-instance.com
```

## Authentication

### Bearer Token (Legacy)
```bash
Authorization: Bearer your_api_token
```

### API Key (Recommended)
```bash
X-API-Key: dcp_your_api_key_here
```

## Rate Limiting

All endpoints are rate limited. Headers included in responses:

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Max requests per window |
| `X-RateLimit-Remaining` | Remaining requests |
| `Retry-After` | Seconds until limit resets (429 only) |

---

## Endpoints

### Health Check

Check if the service is running.

```http
GET /healthz
```

**Response**
```json
{
  "status": "ok",
  "version": "0.2.0",
  "websocket": {
    "enabled": true
  }
}
```

---

### Configuration

Get current configuration.

```http
GET /config
```

**Response**
```json
{
  "model": "claude-3-opus",
  "tools": ["datadog", "repo", "pagerduty"],
  "features": {
    "websocket": true,
    "streaming": true
  }
}
```

---

### Start Investigation

Start a new incident investigation.

```http
POST /chat
```

**Request Body**
```json
{
  "message": "Why is the checkout API throwing 500 errors?",
  "thread_id": "optional-existing-thread-id",
  "context": {
    "service": "checkout",
    "severity": "high"
  }
}
```

**Response** (Success)
```json
{
  "thread_id": "abc123",
  "status": "complete",
  "result": {
    "root_cause": "Database connection pool exhausted",
    "confidence": "high",
    "evidence": [
      "Connection timeout errors in logs",
      "Pool usage at 100% in metrics"
    ],
    "affected_services": ["checkout", "inventory"],
    "recommendation": "Increase pool size or add read replicas"
  }
}
```

**Response** (Awaiting Approval)
```json
{
  "thread_id": "abc123",
  "status": "awaiting_approval",
  "pending_action": {
    "type": "restart_service",
    "target": "checkout-api",
    "reason": "Service appears hung"
  }
}
```

---

### Approve Action

Approve or reject a pending action.

```http
POST /approve
```

**Request Body**
```json
{
  "thread_id": "abc123",
  "approved": true,
  "comment": "Approved - outside peak hours"
}
```

**Response**
```json
{
  "thread_id": "abc123",
  "status": "complete",
  "action_result": {
    "success": true,
    "message": "Service restarted successfully"
  }
}
```

---

### Stream Investigation

Stream real-time investigation progress via SSE.

```http
GET /stream/{thread_id}
```

**Response** (Server-Sent Events)
```
event: progress
data: {"step": "Searching logs...", "progress": 0.2}

event: progress
data: {"step": "Analyzing metrics...", "progress": 0.5}

event: tool_call
data: {"tool": "datadog.search_logs", "args": {"query": "error"}}

event: complete
data: {"result": {...}}
```

---

### WebSocket Connection

Real-time bidirectional communication.

```http
WS /ws
```

**Connect**
```javascript
const ws = new WebSocket('wss://your-instance.com/ws');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data.type, data.payload);
};

ws.send(JSON.stringify({
  type: 'chat',
  payload: {
    message: 'Why is the API slow?'
  }
}));
```

---

### List Investigations

Get investigation history.

```http
GET /investigations
```

**Query Parameters**
| Parameter | Type | Description |
|-----------|------|-------------|
| `limit` | integer | Max results (default: 50) |
| `offset` | integer | Pagination offset |
| `status` | string | Filter by status |
| `from` | datetime | Start date |
| `to` | datetime | End date |

**Response**
```json
{
  "investigations": [
    {
      "id": "abc123",
      "query": "Why is checkout slow?",
      "status": "resolved",
      "created_at": "2024-01-15T10:30:00Z",
      "tokens_used": 1234
    }
  ],
  "total": 100,
  "limit": 50,
  "offset": 0
}
```

---

### Share to Slack

Share investigation to Slack.

```http
POST /share/slack
```

**Request Body**
```json
{
  "thread_id": "abc123",
  "channel": "#incidents",
  "webhook_url": "https://hooks.slack.com/..."
}
```

**Response**
```json
{
  "success": true,
  "message": "Report posted to Slack"
}
```

---

### Integration Status

Check status of configured integrations.

```http
GET /integrations/status
```

**Response**
```json
{
  "slack": {
    "configured": true,
    "type": "webhook"
  },
  "pagerduty": {
    "configured": true
  },
  "jira": {
    "configured": false
  },
  "teams": {
    "configured": true
  }
}
```

---

## Teams API

### Create Team

```http
POST /api/teams
```

**Request Body**
```json
{
  "name": "Platform Team",
  "slug": "platform"
}
```

### List Teams

```http
GET /api/teams
```

### Invite Member

```http
POST /api/teams/{team_id}/invitations
```

**Request Body**
```json
{
  "email": "user@example.com",
  "role": "member"
}
```

### Accept Invitation

```http
POST /api/teams/invitations/{invitation_id}/accept
```

---

## API Keys

### Create API Key

```http
POST /api/keys
```

**Request Body**
```json
{
  "name": "CI Pipeline",
  "rate_limit": 500,
  "scopes": ["read", "write"],
  "expires_in_days": 90
}
```

**Response**
```json
{
  "key": "dcp_abc123...",
  "key_id": "key_xyz",
  "name": "CI Pipeline",
  "created_at": "2024-01-15T10:30:00Z"
}
```

> **Important**: The raw key is only shown once. Store it securely.

### List API Keys

```http
GET /api/keys
```

### Revoke API Key

```http
DELETE /api/keys/{key_id}
```

---

## Error Responses

All errors follow this format:

```json
{
  "error": "Error type",
  "message": "Human-readable description",
  "details": {}
}
```

### HTTP Status Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 201 | Created |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 429 | Rate Limited |
| 500 | Internal Error |

---

## SDKs

### Python
```python
from omnipotent import Client

client = Client(api_key="dcp_...")
result = client.investigate("Why is the API slow?")
print(result.root_cause)
```

### JavaScript
```javascript
import { OmniPotent } from '@omnipotent/sdk';

const client = new OmniPotent({ apiKey: 'dcp_...' });
const result = await client.investigate('Why is the API slow?');
console.log(result.rootCause);
```
