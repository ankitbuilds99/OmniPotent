// Four defects in the SLO page, all of the same family — something on screen
// that no request produced:
//
//  * "Time to Exhaustion" divided the remaining budget *in minutes* by the burn
//    rate, a dimensionless multiplier, and labelled the answer "h". The API
//    already returns `projected_exhaustion`; the page ignored it.
//  * An unreadable or unmeasured budget fell back to a burn rate of 0, which
//    renders as a green tick — the badge a perfectly behaved service earns.
//  * All three buttons under "Actions" had no onClick whatsoever.
//  * The detail panel held a snapshot of the SLO, so a reload refreshed the
//    grid behind it and left the open panel showing the old readings.

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SLOTracker } from './SLOTracker'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** An entry as GET /api/slos returns it. */
function sloRow(overrides: Record<string, unknown> = {}) {
  return {
    id: 'slo-1',
    name: 'Checkout availability',
    service: 'checkout',
    sli_type: 'availability',
    target: 0.999,
    window: 'rolling_30d',
    alert_burn_rate_threshold: 2,
    ...overrides,
  }
}

/** A body as GET /api/slos/{id}/budget returns it. */
function budget(overrides: Record<string, unknown> = {}) {
  return {
    remaining: 0.42,
    remaining_percent: 42,
    burn_rate: 3.5,
    status: 'at_risk',
    current_sli: 0.9962,
    good_count: 9962,
    total_count: 10000,
    budget_minutes_total: 43.2,
    budget_minutes_remaining: 18.1,
    projected_exhaustion: null,
    ...overrides,
  }
}

const BURN_RATE_WINDOWS = {
  slo_id: 'slo-1',
  short_window: '1h',
  short_window_burn_rate: 14.4,
  long_window: '6h',
  long_window_burn_rate: 6.2,
  is_alerting: true,
  severity: 'critical',
  message: 'Burning 14.4x the sustainable rate.',
}

interface Routes {
  slos?: () => Response
  budget?: () => Response
  burnRate?: () => Response
  patch?: () => Response
  investigate?: () => Response
}

/** Route by path, ignoring the query string; `method` picks the write handlers. */
function installFetch(routes: Routes = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input).split('?')[0]
    const method = (init?.method || 'GET').toUpperCase()

    if (url.endsWith('/api/coordinator/investigations')) {
      return routes.investigate
        ? routes.investigate()
        : json({ status: 'started', execution_id: 'exec-77' })
    }
    if (url.endsWith('/burn-rate')) {
      return routes.burnRate ? routes.burnRate() : json(BURN_RATE_WINDOWS)
    }
    if (url.endsWith('/budget')) {
      return routes.budget ? routes.budget() : json(budget())
    }
    if (method === 'PATCH') {
      return routes.patch ? routes.patch() : json({ id: 'slo-1' })
    }
    if (url.endsWith('/api/slos')) {
      return routes.slos ? routes.slos() : json([sloRow()])
    }
    return json({ detail: `unrouted ${method} ${url}` }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

/** Render, click the one SLO card, and hand back the slide-over panel. */
async function openDetailPanel() {
  const { container } = render(<SLOTracker onBack={() => {}} />)
  fireEvent.click(await screen.findByText('Checkout availability'))
  return container.querySelector('.fixed.inset-y-0.right-0') as HTMLElement
}

/** The card's burn-rate cell — "no data" is also the wording of the status badge. */
function burnRateCell() {
  return screen.getAllByText('Burn Rate')[0].parentElement as HTMLElement
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('SLO time to exhaustion', () => {
  it('reports the projection the tracker made, in the units it is in', async () => {
    // 18.1 minutes / 3.5x — the old arithmetic — is 5, which the page printed
    // as "5h". Pick a projection no division of those numbers could produce.
    // The extra 90s keeps the floor off the hour boundary while the test runs.
    const inThreeDays = new Date(Date.now() + 74 * 60 * 60 * 1000 + 90_000)
    installFetch({ budget: () => json(budget({ projected_exhaustion: inThreeDays.toISOString() })) })
    await openDetailPanel()

    expect(await screen.findByText('in 3d 2h')).toBeInTheDocument()
    expect(screen.queryByText('5h')).not.toBeInTheDocument()
  })

  it('says nothing is projected rather than "N/A" when the tracker projects nothing', async () => {
    installFetch({ budget: () => json(budget({ projected_exhaustion: null, burn_rate: 0 })) })
    await openDetailPanel()

    expect(await screen.findByText('not projected')).toBeInTheDocument()
  })

  it('calls a spent budget exhausted instead of estimating a time to spend it', async () => {
    installFetch({
      budget: () =>
        json(budget({ status: 'breached', budget_minutes_remaining: 0, projected_exhaustion: null })),
    })
    await openDetailPanel()

    expect(await screen.findByText('already exhausted')).toBeInTheDocument()
  })
})

describe('SLO burn rate with no measurement', () => {
  it('shows no data rather than a green 0.0x when nothing was sampled', async () => {
    installFetch({ budget: () => json(budget({ status: 'no_data', total_count: 0, burn_rate: 0 })) })
    render(<SLOTracker onBack={() => {}} />)
    await screen.findByText('Checkout availability')

    expect(within(burnRateCell()).getByText('no data')).toBeInTheDocument()
    expect(screen.queryByText('0.0x')).not.toBeInTheDocument()
  })

  it('still shows a real zero burn rate as the measurement it is', async () => {
    installFetch({
      budget: () => json(budget({ status: 'healthy', total_count: 10000, burn_rate: 0 })),
    })
    render(<SLOTracker onBack={() => {}} />)
    await screen.findByText('Checkout availability')

    expect(within(burnRateCell()).getByText('0.0x')).toBeInTheDocument()
  })
})

describe('SLO detail panel actions', () => {
  it('loads the burn-rate windows when asked for detailed metrics', async () => {
    installFetch()
    await openDetailPanel()

    fireEvent.click(screen.getByText('View Detailed Metrics'))

    expect(await screen.findByText('14.40x')).toBeInTheDocument()
    expect(screen.getByText('6.20x')).toBeInTheDocument()
    expect(screen.getByText('yes · critical')).toBeInTheDocument()
  })

  it('reports a failure to load metrics instead of staying silent', async () => {
    installFetch({ burnRate: () => json({ detail: 'tracker offline' }, 503) })
    await openDetailPanel()

    fireEvent.click(screen.getByText('View Detailed Metrics'))

    expect(await screen.findByRole('alert')).toHaveTextContent('tracker offline')
  })

  it('saves a new alert threshold to the SLO', async () => {
    const fetchMock = installFetch()
    await openDetailPanel()

    fireEvent.click(screen.getByText('Configure Alerts'))
    const input = screen.getByLabelText('Alert when the burn rate exceeds')
    // Seeded from the SLO the API returned, not blank and not invented.
    expect(input).toHaveValue(2)

    fireEvent.change(input, { target: { value: '6' } })
    fireEvent.click(screen.getByText('Save threshold'))

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === 'PATCH'
      )
      expect(patch).toBeDefined()
      expect(String(patch![0])).toContain('/api/slos/slo-1')
      expect(JSON.parse((patch![1] as RequestInit).body as string)).toEqual({
        alert_burn_rate_threshold: 6,
      })
    })
  })

  it('refuses a threshold that is not a burn rate', async () => {
    const fetchMock = installFetch()
    await openDetailPanel()

    fireEvent.click(screen.getByText('Configure Alerts'))
    fireEvent.change(screen.getByLabelText('Alert when the burn rate exceeds'), {
      target: { value: '0' },
    })
    fireEvent.click(screen.getByText('Save threshold'))

    expect(await screen.findByRole('alert')).toHaveTextContent('above 0')
    expect(
      fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
    ).toBe(false)
  })

  it('starts a real investigation and names the execution it started', async () => {
    const fetchMock = installFetch()
    await openDetailPanel()

    fireEvent.click(screen.getByText('Investigate with AI'))

    expect(await screen.findByText(/execution exec-77/)).toBeInTheDocument()
    const post = fetchMock.mock.calls.find(([input]) =>
      String(input).includes('/api/coordinator/investigations')
    )
    const body = JSON.parse((post![1] as RequestInit).body as string)
    expect(body.incident_id).toBe('slo-slo-1')
    expect(body.affected_services).toEqual(['checkout'])
    expect(body.context.slo_id).toBe('slo-1')
  })

  it('reports a rejected investigation rather than looking inert', async () => {
    installFetch({ investigate: () => json({ detail: 'coordinator unavailable' }, 502) })
    await openDetailPanel()

    fireEvent.click(screen.getByText('Investigate with AI'))

    expect(await screen.findByRole('alert')).toHaveTextContent('coordinator unavailable')
  })
})

describe('SLO detail panel freshness', () => {
  it('follows the reloaded list instead of the snapshot it was opened with', async () => {
    let burnRate = 3.5
    installFetch({ budget: () => json(budget({ burn_rate: burnRate })) })

    const panel = await openDetailPanel()
    expect(within(panel).getByText('3.5x')).toBeInTheDocument()

    // Saving the threshold reloads the list — the same path the 30s poll takes.
    // The service is burning twice as fast by the time it comes back.
    burnRate = 7.1
    fireEvent.click(within(panel).getByText('Configure Alerts'))
    fireEvent.click(within(panel).getByText('Save threshold'))

    await waitFor(() => expect(within(panel).getByText('7.1x')).toBeInTheDocument())
    expect(within(panel).queryByText('3.5x')).not.toBeInTheDocument()
  })
})
