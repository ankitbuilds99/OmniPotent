// The Service Map used to call `/api/kubernetes/services` (a route that has
// never existed), query Prometheus from the browser at localhost:9090, and then
// synthesise the whole map from incidents: an error rate accumulated from
// incident severities, requests_per_sec/latency hardcoded to 0, a star topology
// from whichever node was named "gateway", and an edge from every service to
// every database. These tests pin the replacement.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ServiceMap, layout, classifyStatus } from './ServiceMap'

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

const TOPOLOGY = {
  nodes: [
    { id: 'api-gateway', name: 'api-gateway', health_status: 'unknown' },
    { id: 'checkout', name: 'checkout', health_status: 'unknown' },
    { id: 'orders-db', name: 'orders-db', health_status: 'unknown' },
    { id: 'unlinked-worker', name: 'unlinked-worker', health_status: 'unknown' },
  ],
  edges: [
    { source: 'api-gateway', target: 'checkout' },
    { source: 'checkout', target: 'orders-db' },
  ],
  count: 4,
}

function installFetch(handlers: Record<string, () => Response> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      calls.push({ url, method })
      for (const [key, fn] of Object.entries(handlers)) {
        const [m, p] = key.split(' ')
        if (m === method && url.startsWith(p)) return fn()
      }
      if (url.startsWith('/api/knowledge-graph/topology')) return json(TOPOLOGY)
      if (url.startsWith('/api/knowledge-graph/service-metrics')) {
        return json({
          detail:
            'Live service metrics require a Prometheus endpoint. Set PROMETHEUS_URL ' +
            '(e.g. http://prometheus:9090) on the API to enable them.',
        }, 503)
      }
      return json({ detail: `no route for ${method} ${url}` }, 404)
    })
  )
}

describe('ServiceMap', () => {
  beforeEach(() => {
    calls = []
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('reads the topology route and never the 404 kubernetes one', async () => {
    installFetch()
    render(<ServiceMap onBack={() => {}} />)

    await screen.findByText('api-gateway')
    expect(calls.some((c) => c.url.startsWith('/api/knowledge-graph/topology'))).toBe(true)
    expect(calls.some((c) => c.url.includes('/api/kubernetes/services'))).toBe(false)
    // Prometheus is proxied server-side; the browser must not dial :9090 itself.
    expect(calls.some((c) => c.url.includes('9090'))).toBe(false)
    // The old fallback derived the map from these two.
    expect(calls.some((c) => c.url.includes('/api/incidents'))).toBe(false)
    expect(calls.some((c) => c.url.includes('/oncall/alerts'))).toBe(false)
  })

  it('shows "—" instead of 0 when there is no Prometheus, and says why', async () => {
    installFetch()
    render(<ServiceMap onBack={() => {}} />)

    await screen.findByText('api-gateway')
    expect(screen.getByText(/Live metrics unavailable/)).toHaveTextContent('PROMETHEUS_URL')
    // Four services x three metric cells, all unmeasured.
    expect(screen.getAllByText('—')).toHaveLength(12)
    expect(screen.queryByText('0.0')).not.toBeInTheDocument()
    expect(screen.queryByText('0.00')).not.toBeInTheDocument()
    expect(screen.queryByText('0ms')).not.toBeInTheDocument()
    // Unmeasured is "unknown", not a green "healthy" badge.
    fireEvent.click(screen.getByText('api-gateway'))
    expect(await screen.findByTestId('service-status')).toHaveTextContent('unknown')
  })

  it('renders the numbers Prometheus actually returned', async () => {
    installFetch({
      'GET /api/knowledge-graph/service-metrics': () =>
        json({
          source: 'prometheus',
          url: 'http://prom:9090',
          services: {
            checkout: { requests_per_sec: 120.5, error_rate: 3.25, latency_p99: 412.5 },
          },
        }),
    })
    render(<ServiceMap onBack={() => {}} />)

    await screen.findByText('checkout')
    await waitFor(() => expect(screen.getByText('120.5')).toBeInTheDocument())
    expect(screen.getByText('3.25')).toBeInTheDocument()
    expect(screen.getByText('413ms')).toBeInTheDocument()
    expect(screen.queryByText(/Live metrics unavailable/)).not.toBeInTheDocument()
    // Services Prometheus said nothing about stay blank rather than reading 0.
    expect(screen.getAllByText('—')).toHaveLength(9)
  })

  it('draws only recorded edges, not a gateway star or a service-to-database mesh', async () => {
    installFetch()
    render(<ServiceMap onBack={() => {}} />)

    await screen.findByText('api-gateway')
    // Two recorded relationships. The old code produced 4: gateway->checkout,
    // gateway->unlinked-worker, checkout->orders-db and unlinked-worker->orders-db.
    expect(screen.getAllByTestId('dependency-edge')).toHaveLength(2)

    fireEvent.click(screen.getByText('checkout'))
    expect(await screen.findByText('Depends on (1)')).toBeInTheDocument()
    expect(screen.getByText('Depended on by (1)')).toBeInTheDocument()

    fireEvent.click(screen.getByText('unlinked-worker'))
    expect(await screen.findByText('No recorded dependencies.')).toBeInTheDocument()
  })

  it('surfaces a topology failure instead of rendering an empty map', async () => {
    installFetch({
      'GET /api/knowledge-graph/topology': () => json({ detail: 'Authorization required' }, 401),
    })
    render(<ServiceMap onBack={() => {}} />)

    expect(await screen.findByText('Could not load the service map')).toBeInTheDocument()
    expect(screen.getByText('Authorization required')).toBeInTheDocument()
    expect(screen.queryByText('No services in the knowledge graph')).not.toBeInTheDocument()
  })

  it('lays every service out at its own position, past the old 8-slot table', () => {
    const ids = Array.from({ length: 9 }, (_, i) => `svc-${i}`)
    const positions = layout(ids, [])
    const distinct = new Set([...positions.values()].map((p) => `${p.x},${p.y}`))
    expect(positions.size).toBe(9)
    expect(distinct.size).toBe(9)
  })

  it('lays dependencies out below the services that depend on them', () => {
    const positions = layout(
      ['api-gateway', 'checkout', 'orders-db'],
      [
        { from: 'api-gateway', to: 'checkout' },
        { from: 'checkout', to: 'orders-db' },
      ]
    )
    expect(positions.get('api-gateway')!.y).toBeLessThan(positions.get('checkout')!.y)
    expect(positions.get('checkout')!.y).toBeLessThan(positions.get('orders-db')!.y)
  })

  it('places every node in a pure cycle instead of dropping it', () => {
    const positions = layout(
      ['a', 'b'],
      [
        { from: 'a', to: 'b' },
        { from: 'b', to: 'a' },
      ]
    )
    expect(positions.size).toBe(2)
  })

  it('only calls a service healthy when something measured it', () => {
    const none = { requests_per_sec: null, error_rate: null, latency_p99: null }
    expect(classifyStatus(none, undefined)).toBe('unknown')
    expect(classifyStatus(none, 'healthy')).toBe('healthy')
    expect(classifyStatus({ ...none, error_rate: 0 }, undefined)).toBe('healthy')
    expect(classifyStatus({ ...none, error_rate: 2 }, undefined)).toBe('degraded')
    expect(classifyStatus({ ...none, error_rate: 9 }, undefined)).toBe('unhealthy')
    expect(classifyStatus({ ...none, latency_p99: 2500 }, undefined)).toBe('unhealthy')
  })
})
