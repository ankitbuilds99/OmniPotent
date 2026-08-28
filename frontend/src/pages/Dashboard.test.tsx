// The Services card used to render `healthyServices: serviceSet.size` beside
// `serviceCount: serviceSet.size` — the same expression twice — so it read
// "N healthy" out of N no matter what the fleet was doing, and no outage could
// ever change the number. Worse, `serviceSet` was built from
// `runbooks.map(r => r.service)`, so it counted services that happen to have a
// runbook rather than services that exist. These tests pin the replacement.

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { readServiceHealth } from './Dashboard'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const ok = (r: Response): PromiseSettledResult<Response> => ({ status: 'fulfilled', value: r })
const rejected = (reason: unknown): PromiseSettledResult<Response> => ({
  status: 'rejected',
  reason,
})

const TOPOLOGY = {
  nodes: [
    { id: 'api-gateway', name: 'api-gateway', health_status: 'unknown' },
    { id: 'checkout', name: 'checkout', health_status: 'unknown' },
    { id: 'orders-db', name: 'orders-db', health_status: 'unknown' },
  ],
  edges: [],
  count: 3,
}

const NO_PROMETHEUS = json(
  {
    detail:
      'Live service metrics require a Prometheus endpoint. Set PROMETHEUS_URL ' +
      '(e.g. http://prometheus:9090) on the API to enable them.',
  },
  503
)

describe('readServiceHealth', () => {
  it('reports health unmeasured rather than a healthy fleet when there is no Prometheus', async () => {
    const health = await readServiceHealth(ok(json(TOPOLOGY)), ok(NO_PROMETHEUS))

    expect(health.total).toBe(3)
    // Not 3, and not 0 either: 0 healthy would read as a total outage.
    expect(health.healthy).toBeNull()
    expect(health.note).toContain('PROMETHEUS_URL')
  })

  it('counts the services Prometheus says are healthy, not every service', async () => {
    const health = await readServiceHealth(
      ok(json(TOPOLOGY)),
      ok(
        json({
          source: 'prometheus',
          services: {
            'api-gateway': { requests_per_sec: 90, error_rate: 0.1, latency_p99: 120 },
            checkout: { requests_per_sec: 40, error_rate: 8.5, latency_p99: 2400 },
            'orders-db': { requests_per_sec: 10, error_rate: 2, latency_p99: 700 },
          },
        })
      )
    )

    expect(health.total).toBe(3)
    // One healthy, one unhealthy, one degraded. The old card said 3 of 3.
    expect(health.healthy).toBe(1)
    expect(health.note).toBeNull()
  })

  it('falls back to the health the graph recorded when nothing measured a service', async () => {
    const health = await readServiceHealth(
      ok(
        json({
          nodes: [
            { id: 'a', name: 'a', health_status: 'healthy' },
            { id: 'b', name: 'b', health_status: 'unhealthy' },
          ],
        })
      ),
      ok(NO_PROMETHEUS)
    )

    expect(health.total).toBe(2)
    expect(health.healthy).toBe(1)
    // Everything the graph knows is known, so there is nothing left to caveat.
    expect(health.note).toBeNull()
  })

  it('says how many services went unmeasured when only some were', async () => {
    const health = await readServiceHealth(
      ok(json(TOPOLOGY)),
      ok(json({ services: { checkout: { error_rate: 0.2, latency_p99: 100 } } }))
    )

    expect(health.healthy).toBe(1)
    expect(health.note).toContain('2 of 3 unmeasured')
  })

  it('does not claim a service count when the inventory itself failed', async () => {
    const health = await readServiceHealth(
      ok(json({ detail: 'Authorization required' }, 401)),
      ok(NO_PROMETHEUS)
    )

    expect(health.total).toBe(0)
    expect(health.healthy).toBeNull()
    expect(health.note).toContain('service inventory unavailable')

    const offline = await readServiceHealth(rejected(new TypeError('Failed to fetch')), ok(NO_PROMETHEUS))
    expect(offline.healthy).toBeNull()
    expect(offline.note).toContain('service inventory unavailable')
  })
})

describe('Dashboard Services card', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/knowledge-graph/topology')) return json(TOPOLOGY)
        if (url.includes('/api/knowledge-graph/service-metrics')) return NO_PROMETHEUS.clone()
        // All three answer with a bare array, which is what the page reads.
        if (url.includes('/api/runbooks/executions')) return json([])
        if (url.includes('/api/runbooks')) {
          return json([
            {
              id: 'rb-1',
              name: 'Restart checkout',
              description: '',
              service: 'checkout',
              trigger: 'manual',
              execution_mode: 'manual',
              step_count: 2,
              labels: {},
              enabled: true,
            },
          ])
        }
        if (url.includes('/oncall/alerts')) return json([])
        return json({ detail: `no route for ${url}` }, 404)
      })
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('counts the real inventory, not the services that happen to have a runbook', async () => {
    const { Dashboard } = await import('./Dashboard')
    render(<Dashboard onBack={() => {}} />)

    // One runbook, three services. The old count was 1.
    await waitFor(() => expect(screen.getByText('Services')).toBeInTheDocument())
    const card = (await screen.findByText('health unmeasured')).closest('div')!
    expect(card).toHaveTextContent('Services')
    expect(card).toHaveTextContent('3')
    expect(card).not.toHaveTextContent('healthy')
  })

  it('does not call an empty alert list a healthy fleet', async () => {
    // The Services card above refuses to claim health without a measurement.
    // The alerts panel used to claim it on strictly less evidence — "no active
    // alerts - all systems operational" — from the same render of the same page.
    const { Dashboard } = await import('./Dashboard')
    render(<Dashboard onBack={() => {}} />)

    expect(await screen.findByText('No active alerts')).toBeInTheDocument()
    expect(screen.queryByText(/all systems operational/)).not.toBeInTheDocument()
  })
})

describe('Dashboard controls', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/knowledge-graph/topology')) return json(TOPOLOGY)
        if (url.includes('/api/knowledge-graph/service-metrics')) return NO_PROMETHEUS.clone()
        if (url.includes('/api/runbooks/executions')) return json([])
        if (url.includes('/api/runbooks')) return json([])
        if (url.includes('/oncall/alerts')) {
          return json([
            {
              id: 'a-1',
              title: 'Checkout error rate high',
              description: null,
              severity: 'critical',
              status: 'triggered',
              source: 'datadog',
              triggered_at: '2026-08-28T14:06:02Z',
              team_id: 't-1',
            },
          ])
        }
        return json({ detail: `no route for ${url}` }, 404)
      })
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  // Both were rendered as blue links with no onClick, no handler and no prop
  // behind them, so a click did nothing whatsoever. "Investigate" sat on every
  // alert row, which is the control an operator reaches for first.
  it('offers no alert controls that do nothing when clicked', async () => {
    const { Dashboard } = await import('./Dashboard')
    const { fireEvent } = await import('@testing-library/react')
    render(<Dashboard onBack={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Alerts' }))
    await screen.findByText('Checkout error rate high')

    expect(screen.queryByRole('button', { name: 'Investigate' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'View All Rules' })).toBeNull()
    // The controls that do work are still there.
    expect(screen.getByRole('button', { name: 'Ack' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resolve' })).toBeInTheDocument()
  })

  // The tab fetches nothing, so "connect X to view metrics" was a promise no
  // credential could keep — and named CloudWatch, which this build has no
  // connector for at all, plus Prometheus, which is not connectable from here.
  it('does not invite connecting a connector that would not fill the metrics tab', async () => {
    const { Dashboard } = await import('./Dashboard')
    const { fireEvent } = await import('@testing-library/react')
    const onServiceMap = vi.fn()
    render(<Dashboard onBack={() => {}} onServiceMap={onServiceMap} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Metrics' }))

    expect(screen.queryByText(/Connect to Datadog, Prometheus, or CloudWatch/)).toBeNull()
    expect(screen.queryByText(/CloudWatch/)).toBeNull()
    // It points at the screen that does render per-service metrics instead.
    fireEvent.click(screen.getByRole('button', { name: 'Open Service Map' }))
    expect(onServiceMap).toHaveBeenCalled()
  })
})
