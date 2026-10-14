# DevOps Copilot User Guide

## Quick Start

### 1. Sign In
Visit your DevOps Copilot instance and sign in using:
- **GitHub OAuth** - Recommended for developers
- **Google OAuth** - Alternative single sign-on
- **Email/Password** - Traditional authentication

### 2. Start an Investigation
Type a natural language query describing your issue:
- "Why is the checkout API throwing 500 errors?"
- "What deploy caused the payment failures?"
- "Are any pods crashing in the production cluster?"

### 3. Review Results
The AI will analyze logs, metrics, and traces to identify:
- **Root Cause** - The underlying issue
- **Evidence** - Supporting data points
- **Affected Services** - Impact scope
- **Recommendations** - Suggested fixes

## Features

### Investigation Console
The main interface for incident investigation.

**Suggestion Prompts**: Pre-built queries for common issues.

**Chat Interface**: Natural conversation with the AI assistant.

**Real-time Streaming**: See investigation progress as it happens.

### History
View and manage past investigations.

**Search**: Find investigations by query or date.

**Filter**: Filter by status (resolved, investigating, etc.).

**Export**: Download reports in Markdown, HTML, or JSON.

### Team Workspaces
Collaborate with your team on investigations.

**Roles**:
- **Owner** - Full access, billing, can delete workspace
- **Admin** - Manage team members and settings
- **Member** - Run investigations
- **Viewer** - Read-only access

**Invitations**: Invite team members via email.

### Integrations

#### Slack
Share investigation results to Slack channels.

1. Configure `SLACK_WEBHOOK_URL` in your environment
2. Click "Share to Slack" on any investigation

#### Microsoft Teams
Post reports to Teams channels via webhooks.

1. Create an Incoming Webhook in Teams
2. Set `TEAMS_WEBHOOK_URL` in your environment

#### PagerDuty
Create and manage incidents directly from investigations.

1. Set `PAGERDUTY_API_KEY` in your environment
2. Use the PagerDuty integration from investigation results

#### Jira
Create issues and link investigations to tickets.

1. Configure Jira credentials:
   - `JIRA_BASE_URL`
   - `JIRA_EMAIL`
   - `JIRA_API_TOKEN`
2. Create issues from investigation results

### Saved Queries
Bookmark frequently used investigation queries.

**Save**: Click the bookmark icon next to any query.

**Organize**: View saved queries in the side panel.

**Quick Access**: Click to instantly run a saved query.

### Export Reports
Download investigation results for documentation.

**Formats**:
- **Markdown** - Great for documentation
- **HTML** - Standalone viewable report
- **JSON** - Machine-readable format

### Status Page
Monitor service health and view incident history.

**Services**: Real-time status of all components.

**Incidents**: Historical incident timeline.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Enter` | Send message |
| `Shift+Enter` | New line in message |
| `Ctrl+K` | New investigation |
| `Escape` | Close modal |

## API Access

### Authentication
Use API keys for programmatic access:

```bash
curl -H "X-API-Key: <API_KEY>" \
  https://your-instance.com/api/chat \
  -d '{"message": "Why is the API slow?"}'
```

### Rate Limits
- Default: 60 requests/minute per IP
- With API key: Up to 1000 requests/minute
- Rate limit headers included in responses

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/healthz` | GET | Health check |
| `/config` | GET | Get configuration |
| `/chat` | POST | Start investigation |
| `/approve` | POST | Approve pending action |
| `/stream/{thread_id}` | GET | Stream investigation |

## Troubleshooting

### "Supabase is not configured"
Environment variables may not be set correctly:
1. Check `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`
2. Ensure backend has matching configuration

### Rate Limited
You've exceeded the request limit:
1. Wait for the retry period (shown in error)
2. Contact admin for increased limits
3. Use an API key for higher limits

### Investigation Timeout
Long-running investigations may timeout:
1. Try a more specific query
2. Break down complex questions
3. Check backend logs for errors

## Getting Help

- **Documentation**: Check this guide and API docs
- **GitHub Issues**: Report bugs at [github.com/your-org/omnipotent](https://github.com/your-org/omnipotent/issues)
- **Slack Support**: Join #omnipotent-help
