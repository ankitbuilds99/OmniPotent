// GET /api/status carries per-component uptime in two top-level maps keyed by
// component id — `uptimes_30d` and `uptimes_90d`. The page read it off the
// entries of `components` instead, where it only ever appears inside `groups`.
// The result: no service showed an uptime at all, and the 30d/90d toggle
// changed nothing but its own colour.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { StatusPage } from './StatusPage'

function component(id: string, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    name,
    description: '',
    status: 'operational',
    status_display: 'Operational',
    group: null,
    display_order: 0,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

const STATUS = {
  overall_status: 'operational',
  overall_status_display: 'All Systems Operational',
  components: [
    component('api', 'API'),
    component('redis', 'Redis'),
    // Never sampled: absent from both maps, and no inline figure either.
    component('workers', 'Workers'),
  ],
  groups: {},
  uptimes_30d: { api: 99.982, redis: 0.0 },
  uptimes_90d: { api: 99.41, redis: 87.5 },
  active_incidents: [],
  recent_incidents: [],
  upcoming_maintenances: [],
  updated_at: '2026-08-24T10:00:00Z',
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(body: unknown = STATUS) {
  vi.stubGlobal('fetch', vi.fn(async () => json(body)))
}

describe('Status page uptime', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('reads each service uptime from the map the API sends it in', async () => {
    installFetch()
    render(<StatusPage />)

    expect(await screen.findByText('99.98% uptime')).toBeInTheDocument()
  })

  it('shows the 90-day figures when the 90d view is selected', async () => {
    installFetch()
    render(<StatusPage />)
    await screen.findByText('99.98% uptime')

    fireEvent.click(screen.getByText('90d'))

    await waitFor(() => expect(screen.getByText('99.41% uptime')).toBeInTheDocument())
    expect(screen.getByText('87.50% uptime')).toBeInTheDocument()
    expect(screen.queryByText('99.98% uptime')).not.toBeInTheDocument()
  })

  it('separates a measured 0% from a service nobody measured', async () => {
    installFetch()
    render(<StatusPage />)
    await screen.findByText('99.98% uptime')

    // Redis is genuinely down and the map says so; that is a reading, not a gap.
    expect(screen.getByText('0.00% uptime')).toBeInTheDocument()
    // Workers appears in neither map, so the page says nothing about its uptime
    // rather than inventing a 0.
    expect(screen.getByText('Workers')).toBeInTheDocument()
    expect(screen.getAllByText(/% uptime$/)).toHaveLength(2)
  })
})
