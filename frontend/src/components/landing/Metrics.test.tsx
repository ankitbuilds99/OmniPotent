// The "By the Numbers" section quoted two counts it had no way to check —
// "48 AI Tools" and "9 MCP Servers" — while MCP_CATALOG had grown to 11 servers
// and 64 tools. Underneath it drew three "Live Performance" graphs captioned
// "Real-time", each of them a `Math.random()` walk clamped to look healthy.
//
// These tests pin both halves of the fix: the counts come from GET /config, and
// nothing on the page claims to be showing live infrastructure.
//
// Note the setup file stubs IntersectionObserver with a no-op, so framer-motion's
// useInView never fires and AnimatedNumber stays at 0. The counts are therefore
// asserted through the connector strip, which is plain text, rather than through
// the animated card values.

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Metrics } from './Metrics'

/** A trimmed GET /config: the shape the component reads, not the whole payload. */
const CONFIG = {
  servers: [
    { name: 'datadog', label: 'Datadog', tools: ['a', 'b', 'c'] },
    { name: 'github', label: 'GitHub', tools: ['d', 'e'] },
    { name: 'deploys', label: 'Deploys', tools: ['f'] },
  ],
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Metrics section', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('counts servers and tools from /config instead of quoting a literal', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(CONFIG)))
    render(<Metrics />)

    expect(await screen.findByText('6 tools across 3 MCP servers')).toBeInTheDocument()
    // The catalog's own labels, so a build that adds a connector says so.
    expect(screen.getByText('Datadog')).toBeInTheDocument()
    expect(screen.getByText('Deploys')).toBeInTheDocument()
  })

  it('omits the counted cards rather than guessing when /config is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'nope' }, 503)))
    render(<Metrics />)

    // The claims no endpoint settles still render — they are labelled targets.
    expect(await screen.findByText('MTTR Reduction')).toBeInTheDocument()
    expect(screen.getByText('LLM Providers')).toBeInTheDocument()
    // The counted ones do not, and no stale number stands in for them.
    expect(screen.queryByText('AI Tools')).not.toBeInTheDocument()
    expect(screen.queryByText('MCP Servers')).not.toBeInTheDocument()
    expect(screen.queryByText(/tools across/)).not.toBeInTheDocument()
  })

  it('no longer presents random walks as live performance', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(CONFIG)))
    render(<Metrics />)
    await screen.findByText('6 tools across 3 MCP servers')

    expect(screen.queryByText('Live Performance')).not.toBeInTheDocument()
    expect(screen.queryByText('Real-time')).not.toBeInTheDocument()
    expect(screen.queryByText('Request Rate')).not.toBeInTheDocument()
    expect(screen.queryByText('CPU Usage')).not.toBeInTheDocument()
  })
})
