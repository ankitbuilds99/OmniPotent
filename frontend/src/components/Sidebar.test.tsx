// The left rail asserted three things it had no basis for: an "online" pill
// beside the provider that was a hardcoded string, a "connected" badge on all
// eleven MCP servers that was also a hardcoded string, and -- because the
// logs/metrics server was renamed "logs-metrics" -> "datadog" in the backend
// catalog and this file was never updated -- no logs source picker at all.
// These tests pin the replacements.

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const CATALOG = [
  { name: 'datadog', label: 'Datadog', custom: true, tools: ['search_logs'] },
  { name: 'repo', label: 'Repository', custom: true, tools: ['read_file'] },
  { name: 'github', label: 'GitHub', custom: true, tools: ['get_commit_diff'] },
  { name: 'terraform', label: 'Terraform', custom: true, tools: ['list_workspaces'] },
]

const CONFIG = {
  provider: 'anthropic',
  model: 'claude-opus-4-8',
  fast_model: 'claude-haiku-4-5',
  offline_mode: true,
  servers: CATALOG,
  github: { connected: false, repo: null, mode: 'offline' },
  sources: { repo_path: '/srv/checkout', logs_path: '/srv/logs/sample_data' },
  has_key: false,
}

const ALL_OFFLINE = {
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

/** `useConfig` keeps a module-level store, so each case needs a fresh module
 *  graph or the second render reuses the first one's config. */
async function renderSidebar(overrides: {
  config?: Partial<typeof CONFIG>
  integrations?: Record<string, boolean> | null
} = {}) {
  vi.resetModules()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/config')) return json({ ...CONFIG, ...overrides.config })
      if (url.endsWith('/integrations/status')) {
        if (overrides.integrations === null) return json({ detail: 'nope' }, 500)
        return json(overrides.integrations ?? ALL_OFFLINE)
      }
      return json({ detail: `no route for ${url}` }, 404)
    })
  )
  const { Sidebar } = await import('./Sidebar')
  render(<Sidebar />)
  await screen.findByText('Anthropic')
}

describe('Sidebar', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('says there is no API key rather than "online" when none is configured', async () => {
    await renderSidebar()

    expect(await screen.findByText('no API key')).toBeInTheDocument()
    expect(screen.queryByText('online')).not.toBeInTheDocument()
  })

  it('says online once a key is configured', async () => {
    await renderSidebar({ config: { has_key: true } })

    expect(await screen.findByText('online')).toBeInTheDocument()
    expect(screen.queryByText('no API key')).not.toBeInTheDocument()
  })

  it('never labels an unconfigured connector "connected"', async () => {
    await renderSidebar()

    // Datadog, GitHub and Terraform all need credentials and have none.
    await waitFor(() => expect(screen.getAllByText('offline demo')).toHaveLength(3))
    expect(screen.queryByText('connected')).not.toBeInTheDocument()
    // The repo card reads a checkout on this disk; there is nothing to connect.
    expect(screen.getByText('local')).toBeInTheDocument()
  })

  it('marks exactly the connectors that have credentials as live', async () => {
    await renderSidebar({ integrations: { ...ALL_OFFLINE, datadog: true, github: true } })

    await waitFor(() => expect(screen.getAllByText('live')).toHaveLength(2))
    // Terraform still has none.
    expect(screen.getAllByText('offline demo')).toHaveLength(1)
  })

  it('admits it does not know when the status call fails', async () => {
    await renderSidebar({ integrations: null })

    await waitFor(() => expect(screen.getAllByText('unknown')).toHaveLength(3))
    expect(screen.queryByText('connected')).not.toBeInTheDocument()
    expect(screen.queryByText('live')).not.toBeInTheDocument()
  })

  it('renders the logs source picker, which the renamed server key had hidden', async () => {
    await renderSidebar()

    // Two pickers now: the repo checkout and the logs directory. Before the
    // rename fix only the repo one rendered.
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Change' })).toHaveLength(2))
    expect(screen.getByTitle('/srv/logs/sample_data')).toBeInTheDocument()
    expect(screen.getByTitle('/srv/checkout')).toBeInTheDocument()
  })
})
