// Disconnect only acted `if (res.ok)`, so a failed DELETE left the card saying
// "Connected" with no explanation; and a rejected /admin/integrations list (401,
// or the single-tenant 400 "Admin endpoints require multi-tenant mode…")
// rendered as a silent "nothing is connected".
//
// The connect flow was worse. It asked for one API key and an "Instance URL
// (optional)" it never sent, offered per-feature scope checkboxes that were
// uncontrolled and unreadable, saved the key under the *catalog id* — a name
// `_INTEGRATION_KEYS` never forwards to any connector — and then announced
// "Connection Successful! … AI can now query your data" for an endpoint that
// stores a string and returns {"status": "saved"} without contacting anyone.

import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { IntegrationMarketplace } from './IntegrationMarketplace'

interface Call {
  url: string
  method: string
  body: unknown
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
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /admin/integrations') return json({ integrations: [] })
    if (key === 'GET /integrations/status') return json({})
    return json({ detail: `no route for ${key}` }, 405)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

/** Open a catalog card's modal by its "Connect" / "Replace credentials" button. */
function cardFor(name: string): HTMLElement {
  return screen.getByRole('heading', { name, level: 3 }).closest('div.rounded-xl') as HTMLElement
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

  describe('what counts as connected', () => {
    it('trusts /integrations/status, not the presence of a stored name', async () => {
      installFetch({
        'GET /integrations/status': () => json({ datadog: true, github: false }),
      })
      render(<IntegrationMarketplace onBack={() => {}} />)

      expect(await screen.findByText('1 connected')).toBeInTheDocument()
      expect(screen.getAllByText('Connected')).toHaveLength(1)
    })

    it('separates a stored credential from a working one', async () => {
      // Every required Sentry secret is present, but nothing has proved them.
      installFetch({
        'GET /admin/integrations': () =>
          json({ integrations: ['SENTRY_API_TOKEN', 'SENTRY_ORG', 'SENTRY_PROJECT'] }),
      })
      render(<IntegrationMarketplace onBack={() => {}} />)

      expect(await screen.findByText('0 connected, 1 awaiting first use')).toBeInTheDocument()
      expect(screen.getByText('Credentials saved')).toBeInTheDocument()
      expect(screen.queryByText('Connected')).not.toBeInTheDocument()
    })

    it('does not call a half-configured integration configured', async () => {
      installFetch({
        'GET /admin/integrations': () => json({ integrations: ['SENTRY_API_TOKEN'] }),
      })
      render(<IntegrationMarketplace onBack={() => {}} />)

      expect(await screen.findByText('0 connected')).toBeInTheDocument()
      expect(screen.queryByText('Credentials saved')).not.toBeInTheDocument()
    })
  })

  describe('integrations this build cannot connect', () => {
    it('stops offering a Connect button for a tool with no connector', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      const grafana = cardFor('Grafana')
      const button = grafana.querySelector('button:last-of-type') as HTMLButtonElement
      expect(button).toBeDisabled()
      expect(button.textContent).toBe('Not available')
      expect(
        screen.getByText(/This build has no Grafana connector\./)
      ).toBeInTheDocument()
    })

    it('no longer labels a working connector "Coming Soon"', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      // Terraform has tools in MCP_CATALOG; it was in the hardcoded list anyway.
      expect(screen.queryByText('Coming Soon')).not.toBeInTheDocument()
      expect(
        screen.getByText(/TERRAFORM_CLOUD_TOKEN and TERRAFORM_ORGANIZATION/)
      ).toBeInTheDocument()
    })
  })

  describe('the connect modal', () => {
    it('collects the secrets the connector reads, under their own names', async () => {
      installFetch({ 'POST /admin/integrations': () => json({ status: 'saved' }) })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      fireEvent.click(cardFor('Datadog').querySelector('button:last-of-type')!)

      fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'dd-api' } })
      fireEvent.change(screen.getByLabelText('Application key'), { target: { value: 'dd-app' } })
      fireEvent.change(screen.getByLabelText(/^Site/), { target: { value: 'datadoghq.eu' } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect Datadog' }))

      await waitFor(() =>
        expect(calls.filter((c) => c.url === '/admin/integrations' && c.method === 'POST')).toHaveLength(3)
      )
      const posted = calls
        .filter((c) => c.url === '/admin/integrations' && c.method === 'POST')
        .map((c) => c.body)
      expect(posted).toEqual([
        { name: 'DD_API_KEY', value: 'dd-api' },
        { name: 'DD_APP_KEY', value: 'dd-app' },
        // The EU-region slot the old "Instance URL (optional)" box pretended to be.
        { name: 'DD_SITE', value: 'datadoghq.eu' },
      ])
    })

    it('says the credential was stored, not that a connection was made', async () => {
      installFetch({ 'POST /admin/integrations': () => json({ status: 'saved' }) })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      fireEvent.click(cardFor('Datadog').querySelector('button:last-of-type')!)
      fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'dd-api' } })
      fireEvent.change(screen.getByLabelText('Application key'), { target: { value: 'dd-app' } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect Datadog' }))

      expect(await screen.findByText('Credentials saved')).toBeInTheDocument()
      expect(
        screen.getByText(/stores credentials without contacting Datadog/)
      ).toBeInTheDocument()
      expect(screen.queryByText('Connection Successful!')).not.toBeInTheDocument()
      expect(screen.queryByText(/AI can now query your/)).not.toBeInTheDocument()
    })

    it('reports the backend refusal instead of a green tick', async () => {
      installFetch({
        'POST /admin/integrations': () =>
          json({ detail: 'Admin endpoints require multi-tenant mode + a tenant API key.' }, 400),
      })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      fireEvent.click(cardFor('Datadog').querySelector('button:last-of-type')!)
      fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'dd-api' } })
      fireEvent.change(screen.getByLabelText('Application key'), { target: { value: 'dd-app' } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect Datadog' }))

      const alerts = await screen.findAllByRole('alert')
      expect(alerts.map((a) => a.textContent).join(' ')).toContain(
        'Admin endpoints require multi-tenant mode + a tenant API key.'
      )
      expect(screen.queryByText('Credentials saved')).not.toBeInTheDocument()
    })

    it('verifies GitHub against GitHub before claiming it is connected', async () => {
      installFetch({
        'POST /github/connect': () => json({ status: 'connected', repo: 'acme/checkout' }),
        'GET /integrations/status': () => json({ github: true }),
      })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('1 connected')

      fireEvent.click(screen.getByRole('button', { name: 'Replace credentials' }))
      fireEvent.change(await screen.findByLabelText('Personal access token'), {
        target: { value: 'ghp_x' },
      })
      fireEvent.change(screen.getByLabelText('Repository'), { target: { value: 'acme/checkout' } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect GitHub' }))

      await waitFor(() =>
        expect(calls.filter((c) => c.url === '/github/connect')).toHaveLength(1)
      )
      expect(calls.find((c) => c.url === '/github/connect')!.body).toEqual({
        token: 'ghp_x',
        repo: 'acme/checkout',
      })
      // GitHub is the one integration whose success screen can say "Connected".
      expect(await screen.findByText(/answered with the repository, so the token works/))
        .toBeInTheDocument()
      // The secret never goes to /admin/integrations under a name nothing reads.
      expect(calls.filter((c) => c.url === '/admin/integrations' && c.method === 'POST')).toHaveLength(0)
    })

    it('will not save until the required fields are filled', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      fireEvent.click(cardFor('Datadog').querySelector('button:last-of-type')!)
      fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'dd-api' } })
      fireEvent.click(screen.getByRole('button', { name: 'Connect Datadog' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Application key required.')
      expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
    })

    it('drops the controls whose values could never be transmitted', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      fireEvent.click(cardFor('Datadog').querySelector('button:last-of-type')!)
      await screen.findByLabelText('API key')

      expect(screen.queryByLabelText(/Instance URL/)).not.toBeInTheDocument()
      // SetIntegrationRequest is {name, value}; per-feature scopes have nowhere to go.
      expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    })
  })

  describe('disconnect', () => {
    it('deletes every secret the integration stored, then refreshes', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      let stored = ['SENTRY_API_TOKEN', 'SENTRY_ORG', 'SENTRY_PROJECT']
      installFetch({
        'GET /admin/integrations': () => json({ integrations: stored }),
        'DELETE /admin/integrations/SENTRY_API_TOKEN': () => {
          stored = stored.filter((n) => n !== 'SENTRY_API_TOKEN')
          return json({ status: 'disconnected' })
        },
        'DELETE /admin/integrations/SENTRY_ORG': () => {
          stored = stored.filter((n) => n !== 'SENTRY_ORG')
          return json({ status: 'disconnected' })
        },
        'DELETE /admin/integrations/SENTRY_PROJECT': () => {
          stored = stored.filter((n) => n !== 'SENTRY_PROJECT')
          return json({ status: 'disconnected' })
        },
      })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected, 1 awaiting first use')

      fireEvent.click(screen.getByRole('button', { name: /disconnect/i }))

      await waitFor(() =>
        expect(calls.filter((c) => c.method === 'DELETE').map((c) => c.url)).toEqual([
          '/admin/integrations/SENTRY_API_TOKEN',
          '/admin/integrations/SENTRY_ORG',
          '/admin/integrations/SENTRY_PROJECT',
        ])
      )
      await screen.findByText('0 connected')
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('says so when the disconnect call fails rather than pretending it worked', async () => {
      vi.spyOn(window, 'confirm').mockReturnValue(true)
      // No DELETE override: the default handler 405s.
      installFetch({
        'GET /admin/integrations': () =>
          json({ integrations: ['SENTRY_API_TOKEN', 'SENTRY_ORG', 'SENTRY_PROJECT'] }),
      })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected, 1 awaiting first use')

      fireEvent.click(screen.getByRole('button', { name: /disconnect/i }))

      const alert = await screen.findByRole('alert')
      expect(alert.textContent).toContain(
        'no route for DELETE /admin/integrations/SENTRY_API_TOKEN'
      )
      // Still shown as stored, because it still is.
      expect(screen.getByText('0 connected, 1 awaiting first use')).toBeInTheDocument()
    })
  })
})
