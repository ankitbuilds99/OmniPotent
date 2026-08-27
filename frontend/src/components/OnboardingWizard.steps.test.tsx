// Everything after the playground step used to be a mime of a setup flow.
//
//   - IntegrationsStep pushed a catalog id into a local array, drew the same
//     green tick the wizard uses for a completed step, and counted "4
//     integrations selected" while /integrations/status still said false for
//     every one of them.
//   - AlertsStep collected an auto-investigate toggle and a "#alerts" channel
//     name in useState and dropped both on unmount — neither had a persistence
//     target even in principle.
//   - KnowledgeStep's four Import/Configure/Add buttons had no onClick at all.
//
// These tests pin the replacements to the requests the backend actually serves.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { OnboardingWizard } from './OnboardingWizard'

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

/** GET /integrations/status — ten booleans read from the deployment's env. */
const STATUS_ALL_OFF = {
  slack: false,
  github: false,
  pagerduty: false,
  datadog: false,
  sentry: false,
  kubernetes: false,
  traces: false,
  deploys: false,
  argocd: false,
  terraform: false,
}

/** GET /api/notifications/preferences, trimmed to what AlertsStep reads. */
const PREFS = {
  channels: [
    { id: 'email', name: 'Email', enabled: true, configured: true, unavailable: '', fields: [] },
    { id: 'slack', name: 'Slack', enabled: true, configured: false, unavailable: '', fields: [] },
  ],
  rules: [
    { event: 'alert_triggered', description: 'Alert fired', unproduced: '', channels: { email: true, slack: false } },
  ],
  quiet_hours: { enabled: false, start: '22:00', end: '08:00' },
  digest_mode: 'realtime',
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function sse(frames: unknown[]): Response {
  const text = frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')
  return new Response(new TextEncoder().encode(text), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

type Overrides = Record<string, (method: string) => Response>

/**
 * Routes the endpoints the wizard's later steps touch. Anything unrouted comes
 * back 404 so a request to a path the backend does not serve fails the test
 * rather than passing silently.
 */
function installFetch(overrides: Overrides = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })

      for (const [suffix, respond] of Object.entries(overrides)) {
        if (url.endsWith(suffix)) return respond(method)
      }
      if (url.endsWith('/chat/stream')) return sse([{ type: 'done', status: 'completed' }])
      if (url.endsWith('/integrations/status')) return json(STATUS_ALL_OFF)
      if (url.endsWith('/api/notifications/preferences')) return json(PREFS)
      return json({ detail: `no route for ${method} ${url}` }, 404)
    })
  )
}

function callsTo(suffix: string): Call[] {
  return calls.filter((c) => c.url.endsWith(suffix))
}

/** Walk the wizard to the "Connect Your Tools" step. */
async function gotoIntegrations() {
  render(<OnboardingWizard onComplete={() => {}} onSkip={() => {}} />)
  fireEvent.click(screen.getByRole('button', { name: /try the playground first/i }))
  fireEvent.click(await screen.findByRole('button', { name: /run investigation/i }))
  fireEvent.click(await screen.findByRole('button', { name: /connect your tools/i }))
  await screen.findByRole('heading', { name: 'Connect Your Tools' })
}

async function gotoAlerts() {
  await gotoIntegrations()
  fireEvent.click(screen.getByRole('button', { name: /^continue$/i }))
  await screen.findByRole('heading', { name: 'Alert Notifications' })
}

async function gotoKnowledge() {
  await gotoAlerts()
  fireEvent.click(screen.getByRole('button', { name: /^skip$/i }))
  await screen.findByRole('heading', { name: 'Add Team Knowledge' })
}

beforeEach(() => {
  calls = []
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('OnboardingWizard integrations step', () => {
  it('reports what /integrations/status says rather than what was clicked', async () => {
    installFetch()
    await gotoIntegrations()

    await waitFor(() => expect(callsTo('/integrations/status').length).toBe(1))
    expect(
      await screen.findByText(
        'Nothing is connected yet — OmniPotent will investigate against its demo data.'
      )
    ).toBeInTheDocument()
    // The old tile was a button whose click alone produced a green tick.
    expect(screen.queryByText(/integrations? selected/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Datadog/ })).not.toBeInTheDocument()
  })

  it('ticks only the integrations the server reports live', async () => {
    installFetch({
      '/integrations/status': () => json({ ...STATUS_ALL_OFF, datadog: true }),
    })
    await gotoIntegrations()

    expect(await screen.findByText('1 of 4 connected.')).toBeInTheDocument()
    // Every tile prints its own verdict, so "not connected" is a measurement
    // here, not the absence of one.
    expect(screen.getByText('Connected — using live data')).toBeInTheDocument()
    expect(
      screen.getAllByText('Not connected — investigations use the bundled demo data')
    ).toHaveLength(3)
  })

  it('names the secrets an unconnectable tool needs instead of offering a fake connect', async () => {
    installFetch()
    await gotoIntegrations()
    await screen.findByText('1 of 4 connected.').catch(() => null)

    expect(
      await screen.findByText(/Needs PAGERDUTY_API_TOKEN\. Add them on the Integrations page/)
    ).toBeInTheDocument()
    // Slack has a connector but no per-org secret this console can write.
    expect(
      screen.getByText(/Slack posting is configured by the deployment \(SLACK_BOT_TOKEN\)/)
    ).toBeInTheDocument()
  })

  it('connects GitHub through the endpoint that verifies the credential', async () => {
    let connected = false
    installFetch({
      '/github/connect': () => {
        connected = true
        return json({ status: 'connected', repo: 'acme/checkout' })
      },
      '/integrations/status': () => json({ ...STATUS_ALL_OFF, github: connected }),
    })
    await gotoIntegrations()

    fireEvent.change(await screen.findByLabelText('Personal access token'), {
      target: { value: 'ghp_secret' },
    })
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: 'acme/checkout' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect GitHub' }))

    await waitFor(() => expect(callsTo('/github/connect')).toHaveLength(1))
    const call = callsTo('/github/connect')[0]
    expect(call.method).toBe('POST')
    expect(call.body).toEqual({ token: 'ghp_secret', repo: 'acme/checkout' })
    // Status is re-read afterwards: the tick comes from the server's answer.
    expect(await screen.findByText('1 of 4 connected.')).toBeInTheDocument()
  })

  it('shows the rejection when GitHub refuses the credential', async () => {
    installFetch({
      '/github/connect': () => json({ detail: 'GitHub rejected the token (401).' }, 400),
    })
    await gotoIntegrations()

    fireEvent.change(await screen.findByLabelText('Personal access token'), {
      target: { value: 'ghp_wrong' },
    })
    fireEvent.change(screen.getByLabelText('Repository'), { target: { value: 'acme/checkout' } })
    fireEvent.click(screen.getByRole('button', { name: 'Connect GitHub' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('GitHub rejected the token (401).')
    expect(
      screen.getAllByText('Not connected — investigations use the bundled demo data')
    ).toHaveLength(4)
  })
})

describe('OnboardingWizard alerts step', () => {
  it('saves the alert routing through the preferences API', async () => {
    installFetch({
      '/api/notifications/preferences': (method) => (method === 'PUT' ? json(PREFS) : json(PREFS)),
    })
    await gotoAlerts()

    const toggle = await screen.findByRole('button', {
      name: 'Tell me in Slack when an alert fires',
    })
    await waitFor(() => expect(toggle).toBeEnabled())
    fireEvent.click(toggle)
    fireEvent.change(screen.getByLabelText('Slack incoming-webhook URL'), {
      target: { value: 'https://hooks.slack.com/services/T/B/x' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save and continue/i }))

    await waitFor(() =>
      expect(callsTo('/api/notifications/preferences').filter((c) => c.method === 'PUT')).toHaveLength(1)
    )
    const put = callsTo('/api/notifications/preferences').find((c) => c.method === 'PUT')!
    expect(put.body).toEqual({
      channels: [
        {
          id: 'slack',
          enabled: true,
          config: { webhook: 'https://hooks.slack.com/services/T/B/x' },
        },
      ],
      rules: [{ event: 'alert_triggered', channels: { slack: true } }],
    })
    // Only advances once the write succeeded.
    await screen.findByRole('heading', { name: 'Add Team Knowledge' })
  })

  it('shows the current routing instead of a switch defaulted to on', async () => {
    installFetch()
    await gotoAlerts()

    const toggle = await screen.findByRole('button', {
      name: 'Tell me in Slack when an alert fires',
    })
    // PREFS routes alert_triggered to email only.
    await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'false'))
  })

  it('stays on the step and says why when the save fails', async () => {
    installFetch({
      '/api/notifications/preferences': (method) =>
        method === 'PUT' ? json({ detail: 'manage_integrations permission required' }, 403) : json(PREFS),
    })
    await gotoAlerts()

    const toggle = await screen.findByRole('button', {
      name: 'Tell me in Slack when an alert fires',
    })
    await waitFor(() => expect(toggle).toBeEnabled())
    fireEvent.click(toggle)
    fireEvent.click(screen.getByRole('button', { name: /save and continue/i }))

    // The "no webhook stored" notice is also an alert, so match on the text.
    expect(
      await screen.findByText(/Could not save: manage_integrations permission required/)
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Alert Notifications' })).toBeInTheDocument()
  })

  it('no longer offers a control over a setting the console cannot reach', async () => {
    installFetch()
    await gotoAlerts()

    // Auto-investigation is COPILOT_AUTO_INVESTIGATE, a deployment env setting
    // with no API. It is stated as a fact, not offered as a switch.
    expect(screen.queryByRole('button', { name: /auto-investigate alerts/i })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Slack Alert Channel')).not.toBeInTheDocument()
    expect(await screen.findByText(/COPILOT_AUTO_INVESTIGATE/)).toBeInTheDocument()
    // An invented SLA for a workflow nobody measured.
    expect(screen.queryByText(/within 2-5 minutes/)).not.toBeInTheDocument()
  })

  it('skips without writing anything', async () => {
    installFetch()
    await gotoKnowledge()

    expect(callsTo('/api/notifications/preferences').filter((c) => c.method === 'PUT')).toHaveLength(0)
  })
})

describe('OnboardingWizard knowledge step', () => {
  it('writes a runbook through POST /api/runbooks', async () => {
    installFetch({ '/api/runbooks': () => json({ id: 'rb_1', name: 'Restart workers' }) })
    await gotoKnowledge()

    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[0])
    fireEvent.change(screen.getByLabelText('Runbook name'), {
      target: { value: 'Restart workers' },
    })
    fireEvent.change(screen.getByLabelText('What it does'), {
      target: { value: 'Drain traffic, roll the deployment.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(callsTo('/api/runbooks')).toHaveLength(1))
    expect(callsTo('/api/runbooks')[0].method).toBe('POST')
    expect(callsTo('/api/runbooks')[0].body).toMatchObject({
      name: 'Restart workers',
      description: 'Drain traffic, roll the deployment.',
    })
    expect(await screen.findByText(/Added "Restart workers"/)).toBeInTheDocument()
  })

  it('registers a service under an id the graph will accept', async () => {
    installFetch({ '/api/knowledge-graph/services': () => json({ id: 'checkout-service' }) })
    await gotoKnowledge()

    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[1])
    fireEvent.change(screen.getByLabelText('Service name'), {
      target: { value: 'Checkout Service' },
    })
    fireEvent.change(screen.getByLabelText('Owning team (optional)'), {
      target: { value: 'payments' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(callsTo('/api/knowledge-graph/services')).toHaveLength(1))
    expect(callsTo('/api/knowledge-graph/services')[0].body).toMatchObject({
      id: 'checkout-service',
      name: 'Checkout Service',
      team: 'payments',
    })
  })

  it('files terminology as a tagged document, the only type that exists', async () => {
    installFetch({ '/api/knowledge/documents': () => json({ id: 'doc_1' }) })
    await gotoKnowledge()

    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[2])
    fireEvent.change(screen.getByLabelText('Term'), { target: { value: 'CDP' } })
    fireEvent.change(screen.getByLabelText('What it means'), {
      target: { value: 'Customer Data Platform.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(callsTo('/api/knowledge/documents')).toHaveLength(1))
    expect(callsTo('/api/knowledge/documents')[0].body).toMatchObject({
      title: 'CDP',
      content: 'Customer Data Platform.',
      doc_type: 'documentation',
      tags: ['glossary'],
    })
  })

  it('reports the server’s refusal instead of appearing to have saved', async () => {
    installFetch({
      '/api/runbooks': () => json({ detail: 'Invalid execution_mode' }, 422),
    })
    await gotoKnowledge()

    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[0])
    fireEvent.change(screen.getByLabelText('Runbook name'), { target: { value: 'Restart workers' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid execution_mode')
    expect(screen.queryByText(/^Added /)).not.toBeInTheDocument()
  })

  it('has no inert action buttons left', async () => {
    installFetch()
    await gotoKnowledge()

    // "Import" and "Configure" were labels on buttons with no handler.
    expect(screen.queryByRole('button', { name: 'Import' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Configure' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Add' })).toHaveLength(4)
  })
})
