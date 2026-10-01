// Save was inert twice over: `isNew` was derived from
// `id.startsWith("rb-") && !id.includes("-")`, which is false for every string,
// so a brand-new runbook was PUT at an id the server had never seen; and the
// handler only acted `if (res.ok)`, so any failure was swallowed silently.

import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RunbookBuilder } from './RunbookBuilder'

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

// Shaped like a real GET /api/runbooks/{id}: every field the PUT body carries
// is present here, so a test that saves without editing can assert the two
// match.
const DETAIL = {
  id: 'restart-service',
  name: 'Restart service',
  description: 'restart it',
  service: 'checkout',
  trigger: 'manual',
  execution_mode: 'requires_approval',
  enabled: true,
  labels: { severity: 'high' },
  steps: [
    {
      id: 's1',
      name: 'Restart',
      description: 'Roll the deployment',
      step_type: 'command',
      action: 'kubectl rollout restart deployment/checkout',
      params: { namespace: 'prod' },
      timeout_seconds: 120,
      continue_on_failure: false,
      requires_approval: true,
      rollback_step: 's0',
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
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /api/runbooks') return json([{ id: 'restart-service' }])
    if (key === 'GET /api/runbooks/executions') return json([])
    if (key === 'GET /api/runbooks/restart-service') return json(DETAIL)
    return json({ detail: `no route for ${key}` }, 405)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

describe('RunbookBuilder save', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('PUTs an edit to a loaded runbook and confirms it saved', async () => {
    installFetch({
      'PUT /api/runbooks/restart-service': () => json({ ...DETAIL, name: 'Restart service' }),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Save Draft')

    fireEvent.click(screen.getByText('Save Draft'))

    await waitFor(() => {
      const put = calls.find(c => c.method === 'PUT')
      expect(put).toBeDefined()
      expect(put!.url).toBe('/api/runbooks/restart-service')
    })
    expect(calls.some(c => c.method === 'POST')).toBe(false)
    expect(await screen.findByText(/^Saved /)).toBeInTheDocument()
  })

  it('POSTs a runbook that has never been saved instead of PUTting a phantom id', async () => {
    // No runbooks on the server, so the builder starts from EmptyRunbook().
    installFetch({
      'GET /api/runbooks': () => json([]),
      'POST /api/runbooks': () => json({ ...DETAIL, id: 'rb-abc12345' }),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Save Draft')

    fireEvent.click(screen.getByText('Save Draft'))

    await waitFor(() => {
      const post = calls.find(c => c.method === 'POST')
      expect(post).toBeDefined()
      expect(post!.url).toBe('/api/runbooks')
    })
    // Never a PUT at the client-generated `rb-<timestamp>` id.
    expect(calls.some(c => c.method === 'PUT')).toBe(false)
  })

  it('saves again with PUT at the id the server assigned', async () => {
    installFetch({
      'GET /api/runbooks': () => json([]),
      'POST /api/runbooks': () => json({ ...DETAIL, id: 'rb-abc12345' }),
      'PUT /api/runbooks/rb-abc12345': () => json({ ...DETAIL, id: 'rb-abc12345' }),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Save Draft')

    fireEvent.click(screen.getByText('Save Draft'))
    await waitFor(() => expect(calls.some(c => c.method === 'POST')).toBe(true))
    await screen.findByText(/^Saved /)

    fireEvent.click(screen.getByText('Save Draft'))
    await waitFor(() => {
      const put = calls.find(c => c.method === 'PUT')
      expect(put).toBeDefined()
      expect(put!.url).toBe('/api/runbooks/rb-abc12345')
    })
    expect(calls.filter(c => c.method === 'POST')).toHaveLength(1)
  })

  it('tells the user when the save fails instead of pretending it worked', async () => {
    // Default override table 405s anything unrouted, i.e. the old broken backend.
    installFetch()
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Save Draft')

    fireEvent.click(screen.getByText('Save Draft'))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Save failed')
    expect(screen.queryByText(/^Saved /)).not.toBeInTheDocument()
  })
})

// The save is a PUT -- full replacement. The loader only kept `{id, type, name,
// params}` and the payload builder wrote the rest from defaults, so opening the
// builder (which auto-loads runbooks[0]) and pressing Save Draft without typing
// anything blanked every command, cleared every approval gate, and erased the
// descriptions and labels. These pin the round trip.
describe('RunbookBuilder round trip', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  async function loadAndSave() {
    installFetch({ 'PUT /api/runbooks/restart-service': () => json(DETAIL) })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Save Draft')
    fireEvent.click(screen.getByText('Save Draft'))
    await waitFor(() => expect(calls.some(c => c.method === 'PUT')).toBe(true))
    return calls.find(c => c.method === 'PUT')!.body as Record<string, unknown>
  }

  it('saves back every runbook field it loaded', async () => {
    const body = await loadAndSave()

    expect(body.name).toBe('Restart service')
    expect(body.description).toBe('restart it')
    expect(body.service).toBe('checkout')
    expect(body.execution_mode).toBe('requires_approval')
    expect(body.labels).toEqual({ severity: 'high' })
    expect(body.enabled).toBe(true)
  })

  it('preserves backend step types instead of coercing them to command', async () => {
    const backendDetail = {
      ...DETAIL,
      steps: [{
        ...DETAIL.steps[0],
        step_type: 'kubernetes',
        action: 'rollout_restart',
        params: { deployment: 'checkout', namespace: 'prod' },
      }],
    }
    installFetch({
      'GET /api/runbooks/restart-service': () => json(backendDetail),
      'PUT /api/runbooks/restart-service': () => json(backendDetail),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Save Draft')
    fireEvent.click(screen.getByText('Save Draft'))

    await waitFor(() => expect(calls.some(c => c.method === 'PUT')).toBe(true))
    const payload = calls.find(c => c.method === 'PUT')!.body as Record<string, unknown>
    expect((payload.steps as Record<string, unknown>[])[0].step_type).toBe('kubernetes')
  })

  it('does not blank the command or drop the approval gate on an untouched save', async () => {
    const body = await loadAndSave()
    const step = (body.steps as Record<string, unknown>[])[0]

    expect(step.action).toBe('kubectl rollout restart deployment/checkout')
    expect(step.requires_approval).toBe(true)
    expect(step.description).toBe('Roll the deployment')
    expect(step.timeout_seconds).toBe(120)
    expect(step.params).toEqual({ namespace: 'prod' })
    expect(step.rollback_step).toBe('s0')
  })

  it('shows the loaded command and approval gate in the editor', async () => {
    installFetch()
    render(<RunbookBuilder onBack={() => {}} />)

    fireEvent.click(await screen.findByText('Restart'))

    // The Command box read `config.command`, a key the server never sends, so it
    // rendered empty for every existing step.
    const command = await screen.findByDisplayValue('kubectl rollout restart deployment/checkout')
    expect(command.tagName).toBe('TEXTAREA')
    expect(screen.getByDisplayValue('Roll the deployment')).toBeInTheDocument()

    const gate = screen.getByLabelText('Requires approval before running') as HTMLInputElement
    expect(gate.checked).toBe(true)
  })

  it('sends an edited command as `action`, the field the executor runs', async () => {
    installFetch({ 'PUT /api/runbooks/restart-service': () => json(DETAIL) })
    render(<RunbookBuilder onBack={() => {}} />)
    fireEvent.click(await screen.findByText('Restart'))

    fireEvent.change(await screen.findByDisplayValue('kubectl rollout restart deployment/checkout'), {
      target: { value: 'kubectl delete pod -l app=checkout' },
    })
    fireEvent.click(screen.getByText('Save Draft'))

    await waitFor(() => expect(calls.some(c => c.method === 'PUT')).toBe(true))
    const body = calls.find(c => c.method === 'PUT')!.body as Record<string, unknown>
    expect((body.steps as Record<string, unknown>[])[0].action).toBe('kubectl delete pod -l app=checkout')
  })
})

// Test Run and Publish were <button>s with no onClick: they highlighted on
// hover, did nothing on click, and gave no feedback either way.
describe('RunbookBuilder actions', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('Publish saves the runbook with enabled set', async () => {
    installFetch({
      'GET /api/runbooks/restart-service': () => json({ ...DETAIL, enabled: false }),
      'PUT /api/runbooks/restart-service': () => json(DETAIL),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    expect(await screen.findByText('draft')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Publish'))

    await waitFor(() => expect(calls.some(c => c.method === 'PUT')).toBe(true))
    const body = calls.find(c => c.method === 'PUT')!.body as Record<string, unknown>
    expect(body.enabled).toBe(true)
    expect(await screen.findByText('enabled')).toBeInTheDocument()
  })

  it('Test Run saves first, then executes, and reports the outcome', async () => {
    installFetch({
      'PUT /api/runbooks/restart-service': () => json(DETAIL),
      'POST /api/runbooks/restart-service/execute': () =>
        json({ id: 'exec-1', status: 'waiting_approval' }),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Test Run')

    fireEvent.click(screen.getByText('Test Run'))

    await waitFor(() =>
      expect(calls.some(c => c.url === '/api/runbooks/restart-service/execute')).toBe(true)
    )
    // Running the version on screen means saving it first.
    const order = calls.filter(c => c.method !== 'GET').map(c => c.method)
    expect(order[0]).toBe('PUT')
    expect(await screen.findByText(/waiting for approval/i)).toBeInTheDocument()
  })

  it('surfaces a failed test run instead of staying silent', async () => {
    installFetch({
      'PUT /api/runbooks/restart-service': () => json(DETAIL),
      'POST /api/runbooks/restart-service/execute': () =>
        json({ detail: 'executor unavailable' }, 503),
    })
    render(<RunbookBuilder onBack={() => {}} />)
    await screen.findByText('Test Run')

    fireEvent.click(screen.getByText('Test Run'))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Test run failed')
  })

  it('counts real executions rather than printing a hardcoded 0', async () => {
    installFetch({
      'GET /api/runbooks/executions': () =>
        json([
          { id: 'e1', runbook_id: 'restart-service' },
          { id: 'e2', runbook_id: 'restart-service' },
          { id: 'e3', runbook_id: 'some-other-runbook' },
        ]),
    })
    render(<RunbookBuilder onBack={() => {}} />)

    expect(await screen.findByText('2 executions')).toBeInTheDocument()
  })

  it('says the execution count is unavailable when it cannot be read', async () => {
    installFetch({
      'GET /api/runbooks/executions': () => json({ detail: 'nope' }, 500),
    })
    render(<RunbookBuilder onBack={() => {}} />)

    expect(await screen.findByText('execution count unavailable')).toBeInTheDocument()
    expect(screen.queryByText('0 executions')).not.toBeInTheDocument()
  })
})
