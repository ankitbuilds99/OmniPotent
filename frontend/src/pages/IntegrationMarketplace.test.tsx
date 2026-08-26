// Disconnect only acted `if (res.ok)`, so the 405 from the missing DELETE route
// left the card saying "Connected" with no explanation; and a rejected
// /admin/integrations list (401, or the single-tenant 400 "Admin endpoints
// require multi-tenant mode…") rendered as a silent "nothing is connected".

import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { IntegrationMarketplace } from './IntegrationMarketplace'

interface Call {
  url: string
  method: string
}

let calls: Call[] = []

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
    calls.push({ url, method })
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /admin/integrations') return json({ integrations: ['slack'] })
    if (key === 'GET /integrations/status') return json({})
    return json({ detail: `no route for ${key}` }, 405)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

describe('IntegrationMarketplace', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('reports why the list is empty instead of implying nothing is connected', async () => {
    installFetch({
      'GET /admin/integrations': () =>
        json({ detail: 'Admin endpoints require multi-tenant mode + a tenant API key.' }, 400),
    })
    render(<IntegrationMarketplace onBack={() => {}} />)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(
      'Admin endpoints require multi-tenant mode + a tenant API key.'
    )
  })

  it('DELETEs the integration and refreshes on disconnect', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let connected = ['slack']
    installFetch({
      'GET /admin/integrations': () => json({ integrations: connected }),
      'DELETE /admin/integrations/slack': () => {
        connected = []
        return json({ status: 'disconnected', name: 'slack' })
      },
    })
    render(<IntegrationMarketplace onBack={() => {}} />)
    await screen.findByText('1 connected')

    fireEvent.click(screen.getByRole('button', { name: /disconnect/i }))

    await waitFor(() =>
      expect(calls).toContainEqual({ url: '/admin/integrations/slack', method: 'DELETE' })
    )
    await screen.findByText('0 connected')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says so when the disconnect call fails rather than pretending it worked', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    // No DELETE override: the default handler 405s, i.e. the old missing route.
    installFetch()
    render(<IntegrationMarketplace onBack={() => {}} />)
    await screen.findByText('1 connected')

    fireEvent.click(screen.getByRole('button', { name: /disconnect/i }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('no route for DELETE /admin/integrations/slack')
    // Still shown as connected, because it still is.
    expect(screen.getByText('1 connected')).toBeInTheDocument()
  })
})
