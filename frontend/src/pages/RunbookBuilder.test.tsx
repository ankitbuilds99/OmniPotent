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

const DETAIL = {
  id: 'restart-service',
  name: 'Restart service',
  description: 'restart it',
  trigger: 'manual',
  enabled: true,
  steps: [{ id: 's1', name: 'Restart', step_type: 'command', params: {} }],
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
