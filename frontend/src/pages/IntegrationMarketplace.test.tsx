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

/** A trimmed MCP_CATALOG, in the shape GET /config returns it. */
const SERVERS = [
  {
    name: 'datadog',
    label: 'Datadog',
    custom: true,
    tools: ['search_logs', 'get_error_summary', 'cluster_logs', 'get_metric'],
  },
  { name: 'kubernetes', label: 'Kubernetes', custom: true, tools: ['list_pods', 'describe_pod'] },
  { name: 'traces', label: 'Traces', custom: true, tools: ['search_traces', 'get_trace'] },
]

function installFetch(overrides: Record<string, () => Response> = {}) {
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /admin/integrations') return json({ integrations: [] })
    if (key === 'GET /integrations/status') return json({})
    if (key === 'GET /config') return json({ servers: SERVERS })
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

  it('does not show an admin error for an intentional single-tenant deployment', async () => {
    installFetch({
      'GET /admin/integrations': () =>
        json({ detail: 'Admin endpoints require multi-tenant mode + a tenant API key.' }, 400),
    })
    render(<IntegrationMarketplace onBack={() => {}} />)

    await screen.findByText('Integrations')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
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

    // Kubernetes has no API key to ask for. Its only credential is
    // KUBE_CONFIG_PATH, a path the *server* opens with
    // config.load_kube_config(config_file=…), and no endpoint in app/ accepts a
    // file upload — so a browser cannot put a kubeconfig where that path leads.
    it('does not ask for a Kubernetes credential a browser cannot supply', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      const kubernetes = cardFor('Kubernetes')
      const button = kubernetes.querySelector('button:last-of-type') as HTMLButtonElement
      expect(button).toBeDisabled()
      expect(button.textContent).toBe('Not available')
      expect(kubernetes.textContent).toContain('There is no Kubernetes API key to enter')
      // The old form's field, which would have flipped the server out of
      // offline mode and into a load_kube_config failure.
      expect(screen.queryByLabelText(/Kubeconfig path/)).not.toBeInTheDocument()
    })

    it('offers no credential management for a connector the deployment configures', async () => {
      // Live from the process environment, with nothing stored for this org.
      installFetch({ 'GET /integrations/status': () => json({ kubernetes: true }) })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('1 connected')

      const kubernetes = cardFor('Kubernetes')
      expect(kubernetes.textContent).toContain('Connected')
      expect(kubernetes.textContent).toContain('Configured by the deployment')
      // Both buttons would have failed: there is no stored secret to delete,
      // and no form to re-enter.
      expect(kubernetes.querySelector('button')).toBeNull()
    })
  })

  describe('claims the catalog used to make up', () => {
    it('states capabilities from the real tool list, not invented features', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      const datadog = cardFor('Datadog')
      expect(datadog.textContent).toContain('search_logs')
      expect(datadog.textContent).toContain('+1 more') // 4 tools, 3 shown
      // MCP_CATALOG's datadog server has no such tools; "APM traces" belongs to
      // a different server entirely.
      for (const invented of ['Dashboards', 'Monitors', 'APM traces']) {
        expect(datadog.textContent).not.toContain(invented)
      }
    })

    it('claims nothing for an entry with no connector behind it', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      // Grafana used to advertise four capabilities and a five-minute setup.
      const grafana = cardFor('Grafana')
      for (const invented of ['Dashboard queries', 'Annotations', 'Data sources']) {
        expect(grafana.textContent).not.toContain(invented)
      }
    })

    it('quotes no setup time, because nothing measures one', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      expect(screen.queryByText(/Setup: \d+ min/)).not.toBeInTheDocument()
      expect(document.body.textContent).not.toMatch(/Setup: /)
    })

    it('lists the trace connector it has always been able to configure', async () => {
      installFetch({ 'POST /admin/integrations': () => json({ status: 'saved' }) })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      // TRACES_API_URL is in _INTEGRATION_KEYS and /integrations/status reports
      // on `traces`, but the catalog listed eleven connectors that do not exist
      // and omitted this one, which does.
      const traces = cardFor('Distributed tracing')
      expect(traces.textContent).toContain('search_traces')

      fireEvent.click(traces.querySelector('button:last-of-type')!)
      fireEvent.change(await screen.findByLabelText(/Query API base URL/), {
        target: { value: 'https://jaeger.example.com' },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Connect Distributed tracing' }))

      await waitFor(() =>
        expect(calls.filter((c) => c.url === '/admin/integrations' && c.method === 'POST')).toHaveLength(1)
      )
      expect(calls.find((c) => c.method === 'POST')!.body).toEqual({
        name: 'TRACES_API_URL',
        value: 'https://jaeger.example.com',
      })
    })

    // The same omission as `traces`, one row over, and the traces fix walked
    // past it: `deploys` is in MCP_CATALOG with three tools and has always been
    // reported by GET /integrations/status, but no catalog row meant the
    // connector answering "what shipped, and when" could not be seen — let alone
    // diagnosed — from the page whose whole job is showing what is connected.
    it('lists the deploy connector that /integrations/status has always reported on', async () => {
      installFetch({ 'GET /integrations/status': () => json({ deploys: true }) })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('1 connected')

      const deploys = cardFor('Deploy tracker')
      expect(deploys.textContent).toContain('Connected')
      // DEPLOYS_API_URL is read through `_isecret` but is not in
      // `_INTEGRATION_KEYS`, so a per-org secret would never reach the
      // subprocess — there is no credential form to offer, only an explanation.
      expect(deploys.textContent).toContain('Configured by the deployment')
      expect(deploys.textContent).toContain('DEPLOYS_API_URL, DEPLOYS_API_TOKEN')
      expect(deploys.querySelector('button')).toBeNull()
    })

    // The variable names used to live only inside the `unsupported` prose, which
    // the card renders only when the connector is *not* working. So a card that
    // said "Connected — configured by the deployment" named nothing at all, and
    // the reader most likely to want the variable was the one who could not see
    // it. Kubernetes is the long-standing case; the deploys card above is new.
    it('names the deployment variables behind a connector it cannot configure', async () => {
      installFetch({ 'GET /integrations/status': () => json({ kubernetes: true }) })
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('1 connected')

      const kubernetes = cardFor('Kubernetes')
      expect(kubernetes.textContent).toContain('Connected')
      expect(kubernetes.textContent).toContain('KUBE_CONFIG_PATH')
    })

    // Prometheus genuinely has no MCP server, but the card used to go on to say
    // a PROMETHEUS_URL could be set "but nothing in the console reads it". That
    // is false in the expensive direction: it backs
    // GET /api/knowledge-graph/service-metrics, which is the only thing
    // measuring service health anywhere in the product. An operator who
    // believed it would leave the Service Map and the dashboard's Services card
    // permanently unmeasured.
    it('does not tell an operator that PROMETHEUS_URL is read by nothing', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      const prometheus = cardFor('Prometheus')
      expect(prometheus.textContent).not.toContain('nothing in the console reads it')
      expect(prometheus.textContent).toContain('Service Map')
      expect(prometheus.textContent).toContain('PROMETHEUS_URL')
    })
  })

  // Ten of the twenty-three catalog logos were dead URLs — CloudWatch 403, and
  // 404 for New Relic, Opsgenie, GitLab, Bitbucket, Google Cloud, Terraform,
  // Confluence and Sentry, two of which (Sentry, Terraform) are live connectors.
  // They are repointed, but a hardcoded third-party URL rots on someone else's
  // schedule, so the failure needs a floor that is not an empty grey tile.
  describe('a logo that will not load', () => {
    it('falls back to the initial rather than an empty tile', async () => {
      installFetch()
      render(<IntegrationMarketplace onBack={() => {}} />)
      await screen.findByText('0 connected')

      const datadog = cardFor('Datadog')
      const img = datadog.querySelector('img') as HTMLImageElement
      expect(img).toBeTruthy()

      fireEvent.error(img)

      expect(datadog.querySelector('img')).toBeNull()
      expect(datadog.textContent).toContain('D')
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
        'GET /admin/integrations': () => json({ integrations: ['GITHUB_TOKEN', 'GITHUB_REPO'] }),
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
