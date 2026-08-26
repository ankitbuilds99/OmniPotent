// `fetchIncidents` manufactured an investigation for any incident whose list row
// carried `has_investigation`: a fabricated `inv-${id}`, a hardcoded "running"
// status, an empty agent list and the literal root cause "Investigation in
// progress...". A finished investigation with a real root cause therefore
// rendered as one still running with nothing to show. The agent-status union
// also used "completed"/"failed" while the backend emits "complete"/"error", so
// no agent tile could ever render as done.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Incidents } from './Incidents'

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

const INCIDENT_ID = 'c0ffee00-0000-4000-8000-000000000001'

const LIST_ROW = {
  id: INCIDENT_ID,
  title: 'Checkout API returning 500s',
  severity: 'critical',
  status: 'investigating',
  service: 'checkout-api',
  triggered_at: '2026-01-02T03:00:00+00:00',
  acknowledged_at: null,
  resolved_at: null,
  assignee: null,
  tags: [],
  has_investigation: true,
  investigation_confidence: 0.87,
}

const INVESTIGATION = {
  id: '11111111-2222-4333-8444-555555555555',
  incident_id: INCIDENT_ID,
  status: 'completed',
  started_at: '2026-01-02T03:01:00+00:00',
  completed_at: '2026-01-02T03:04:00+00:00',
  overall_confidence: 0.87,
  root_cause: 'A leaked transaction exhausted the connection pool.',
  recommended_actions: ['Restart the settlement worker', 'Cap transaction lifetime'],
  agents: {
    log_analyzer: {
      agent_type: 'log_analyzer',
      status: 'complete',
      progress: 100,
      current_action: null,
      findings: [
        { finding: 'Pool saturation at 03:02', confidence: 0.9, timestamp: '2026-01-02T03:02:00+00:00' },
      ],
    },
  },
  safety_gates: [
    {
      gate_type: 'confidence',
      status: 'passed',
      threshold: 0.8,
      current_value: 0.87,
      message: null,
    },
  ],
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(overrides: Record<string, () => Response> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      const key = `${method} ${url}`
      if (overrides[key]) return overrides[key]()
      if (key === 'GET /api/incidents') return json([LIST_ROW])
      if (key === `GET /api/incidents/${INCIDENT_ID}/investigation`) return json(INVESTIGATION)
      return json({ detail: `no route for ${key}` }, 405)
    })
  )
}

async function openDetail() {
  render(<Incidents onBack={() => {}} />)
  fireEvent.click(await screen.findByText('Checkout API returning 500s'))
}

describe('Incidents', () => {
  beforeEach(() => {
    calls = []
    installFetch()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('fetches the real investigation instead of synthesising one', async () => {
    await openDetail()

    expect(
      await screen.findByText('A leaked transaction exhausted the connection pool.')
    ).toBeInTheDocument()
    // The literal every incident used to show.
    expect(screen.queryByText('Investigation in progress...')).not.toBeInTheDocument()

    expect(
      calls.some(c => c.url === `/api/incidents/${INCIDENT_ID}/investigation`)
    ).toBe(true)
  })

  it('shows the recommended actions the server returned', async () => {
    await openDetail()

    // Rendered as list items with a "• " prefix, hence the regex.
    expect(await screen.findByText(/Restart the settlement worker/)).toBeInTheDocument()
    expect(screen.getByText(/Cap transaction lifetime/)).toBeInTheDocument()
  })

  it('has no Quick Actions buttons that do nothing', async () => {
    // Four buttons — "Rollback last deployment", "Scale up replicas", "Restart
    // service", "Run diagnostic runbook" — rendered here with no onClick at all.
    await openDetail()
    await screen.findByText('A leaked transaction exhausted the connection pool.')

    expect(screen.queryByText('Rollback last deployment')).not.toBeInTheDocument()
    expect(screen.queryByText('Scale up replicas')).not.toBeInTheDocument()
    expect(screen.queryByText('Restart service')).not.toBeInTheDocument()
    expect(screen.queryByText('Run diagnostic runbook')).not.toBeInTheDocument()
  })

  it('says so when the investigation cannot be loaded', async () => {
    installFetch({
      [`GET /api/incidents/${INCIDENT_ID}/investigation`]: () =>
        json({ detail: 'Investigation not found' }, 404),
    })
    render(<Incidents onBack={() => {}} />)
    fireEvent.click(await screen.findByText('Checkout API returning 500s'))

    expect(await screen.findByText(/Investigation not found/)).toBeInTheDocument()
  })

  it('reports a failed acknowledge rather than closing as though it worked', async () => {
    installFetch({
      // Acknowledge is only offered on an incident nobody has picked up yet.
      'GET /api/incidents': () => json([{ ...LIST_ROW, status: 'triggered' }]),
      [`POST /api/incidents/${INCIDENT_ID}/acknowledge`]: () =>
        json({ detail: 'Incident not found' }, 404),
    })
    render(<Incidents onBack={() => {}} />)
    fireEvent.click(await screen.findByText('Checkout API returning 500s'))
    fireEvent.click(await screen.findByText('Acknowledge'))

    // `await apiFetch(...)` with no `.ok` check used to close the detail view and
    // refresh the list for a 404 exactly as for a 200.
    expect(await screen.findByText(/Incident not found/)).toBeInTheDocument()
  })

  it('declares an incident through the API', async () => {
    // "Declare Incident" had no onClick: it rendered in the danger colour, in
    // the header of the incidents page, and did nothing when pressed.
    installFetch({
      'POST /api/incidents': () => json({ ...LIST_ROW, id: 'new-id' }),
    })
    render(<Incidents onBack={() => {}} />)
    await screen.findByText('Checkout API returning 500s')

    fireEvent.click(screen.getByText('Declare Incident'))
    fireEvent.change(screen.getByPlaceholderText('Checkout API returning 500s'), {
      target: { value: 'Payments latency spike' },
    })
    fireEvent.change(screen.getByPlaceholderText('payments-api'), {
      target: { value: 'payments-api' },
    })
    fireEvent.click(screen.getByText('Declare'))

    await waitFor(() => {
      const post = calls.find(c => c.method === 'POST' && c.url === '/api/incidents')
      expect(post?.body).toMatchObject({
        title: 'Payments latency spike',
        service: 'payments-api',
        severity: 'high',
      })
    })
  })

  it('surfaces a failed list load instead of an empty page', async () => {
    installFetch({ 'GET /api/incidents': () => json({ detail: 'Not authenticated' }, 401) })
    render(<Incidents onBack={() => {}} />)

    expect(await screen.findByText(/Not authenticated/)).toBeInTheDocument()
  })
})
