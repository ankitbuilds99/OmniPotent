export const meta = {
  name: 'onboarding-surface-map',
  description: 'Map the real backend endpoints the onboarding wizard and integration marketplace should be calling',
  phases: [
    { title: 'Map', detail: 'parallel readers over integrations, alert-config, knowledge and marketplace routes' },
    { title: 'Verify', detail: 'confirm each named route actually exists and its exact request shape' },
  ],
}

const SCHEMA = {
  type: 'object',
  properties: {
    endpoints: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          method: { type: 'string' },
          path: { type: 'string', description: 'Full mounted path as the browser must call it, including any /api prefix' },
          file: { type: 'string' },
          line: { type: 'integer' },
          request_shape: { type: 'string', description: 'Exact JSON body fields with types, or "none"' },
          response_shape: { type: 'string' },
          auth: { type: 'string', description: 'What credential/permission it requires' },
          notes: { type: 'string' },
        },
        required: ['method', 'path', 'file', 'request_shape', 'response_shape'],
      },
    },
    verdict: { type: 'string', description: 'Plain-language answer to the question asked, including "no such surface exists" if that is the truth' },
  },
  required: ['endpoints', 'verdict'],
}

const REPO = '/Users/ankitpandey/OmniPotent/OmniPotent'

const COMMON = `You are reading the Python backend of the repo at ${REPO}. The FastAPI app is assembled in app/api/main.py — read how each router is included there, because mount prefixes differ per router (some are /api/..., some are bare). Report the path exactly as a browser must call it. Do not guess: open the router files and read the actual handler signatures and Pydantic models. If no surface exists for what is asked, say so plainly in the verdict rather than inventing one.`

const QUESTIONS = [
  {
    key: 'integrations',
    prompt: `${COMMON}

QUESTION: What backend endpoints exist for listing, connecting/configuring, and testing third-party integrations (GitHub, Slack, Prometheus, PagerDuty, Grafana, Datadog, etc.)?

Look at app/integrations/, app/api/main.py, and anything matching "integration", "connector", "credential", "provider". For each endpoint report the exact request body fields (including whether an instance URL / base URL / scopes field is accepted), the response shape, and whether anything actually TESTS the credential (a live connectivity check) versus merely storing it. Also report how the frontend currently calls them: grep frontend/src for the paths.`,
  },
  {
    key: 'alerting',
    prompt: `${COMMON}

QUESTION: What backend endpoints exist for persisting (a) an "auto-investigate alerts" on/off setting and (b) a Slack alert channel destination?

Search app/ for settings/config/preferences persistence: app/notifications/preferences.py, anything with "auto_investigate", "autoinvestigate", "auto-investigate", "settings", "org_settings", "alert_rules", "webhooks/alertmanager". Report which of the two settings has a real persistence surface and which does not. Include the notifications preferences PUT shape if it is the closest fit, and say explicitly whether it can store a Slack channel name and an auto-investigate flag or not.`,
  },
  {
    key: 'knowledge',
    prompt: `${COMMON}

QUESTION: What backend endpoints exist for adding team knowledge — runbooks, a service catalog, terminology/glossary, and documented best practices?

Search app/knowledge/, app/runbooks/, app/services/, and the API router includes. For each of the four categories say whether a real create/import endpoint exists, its exact path and body shape. Also report what the frontend already has: which pages (frontend/src/pages/*.tsx) already provide a UI for each category and what route path in frontend/src/App.tsx reaches them.`,
  },
  {
    key: 'marketplace',
    prompt: `${COMMON}

QUESTION: Read frontend/src/pages/IntegrationMarketplace.tsx in full and report exactly what its connect wizard collects and what it sends.

Report: every field the 3-step wizard collects (step by step), the exact fetch call(s) it makes with method/path/body, which collected fields are discarded, and what step 3 claims. Then cross-reference the backend endpoint it calls and report whether the backend validates/tests the credential or just stores it, and whether the backend accepts an instance URL or scope list at all. Quote the relevant line numbers.`,
  },
]

phase('Map')
const mapped = await pipeline(
  QUESTIONS,
  q => agent(q.prompt, { label: `map:${q.key}`, phase: 'Map', schema: SCHEMA }),
  (res, q) => {
    if (!res) return null
    return agent(
      `${COMMON}

Another agent reported the following about "${q.key}". Verify it adversarially: open every file:line it cites and confirm the path, the mount prefix, the request body fields and the auth requirement are EXACTLY as stated. Correct anything wrong. Delete any endpoint that does not exist. Your verdict must state which claims were wrong.

REPORT TO VERIFY:
${JSON.stringify(res, null, 2)}`,
      { label: `verify:${q.key}`, phase: 'Verify', schema: SCHEMA }
    ).then(v => ({ key: q.key, verified: v }))
  }
)

return mapped.filter(Boolean)
