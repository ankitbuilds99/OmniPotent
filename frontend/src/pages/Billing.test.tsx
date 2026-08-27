// Four separate ways this page showed a number nobody measured.
//
//  - The usage-history charts asked for `ai_investigations_count` and friends.
//    `UsageStats.to_dict()` (app/billing/usage_tracker.py) emits those keys
//    without the `_count` suffix, so every lookup was `undefined`, coerced to
//    0, and all four charts drew a flat line along the floor.
//  - The history arrives newest-first, and was plotted in that order: the x
//    axis ran backwards and the figure captioned "this month" was the oldest
//    month on the chart.
//  - `getSubscription()` caught every failure and returned null, which is the
//    same value the API returns for "no subscription". A 500 rendered as an
//    account on no plan at all.
//  - The annual-saving badge was hardcoded to 17%; every plan the API
//    describes saves ~15%.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Billing } from './Billing'

function features(overrides: Record<string, unknown> = {}) {
  return {
    ai_investigation: true,
    slack_integration: true,
    basic_deduplication: true,
    github_integration: true,
    multi_llm: false,
    auto_fix_pr: false,
    ai_postmortem: false,
    similar_incident_search: false,
    advanced_deduplication: false,
    mtta_mttr_dashboard: false,
    api_access: false,
    limit_history_days: 7,
    limit_llm_providers: 1,
    limit_integrations: 2,
    ...overrides,
  }
}

const PLANS = [
  {
    name: 'starter',
    display_name: 'Starter',
    description: 'For individuals',
    tier: 'starter',
    monthly_price: 0,
    annual_price: 0,
    annual_monthly_price: 0,
    // A free plan is not an annual offer, and must not drag the badge to 0%.
    annual_savings_percent: 0,
    max_users: 1,
    max_ai_investigations_monthly: 25,
    max_alerts_monthly: 100,
    features: features(),
  },
  {
    name: 'team',
    display_name: 'Team',
    description: 'For growing teams',
    tier: 'team',
    monthly_price: 299,
    annual_price: 3048,
    annual_monthly_price: 254,
    annual_savings_percent: 15.050167224080269,
    max_users: 10,
    max_ai_investigations_monthly: 500,
    max_alerts_monthly: 5000,
    features: features({ multi_llm: true, api_access: true }),
  },
  {
    name: 'enterprise',
    display_name: 'Enterprise',
    description: 'Custom solutions',
    tier: 'enterprise',
    monthly_price: 0,
    annual_price: 0,
    annual_monthly_price: 0,
    annual_savings_percent: 0,
    // What GET /billing/plans actually returns for an uncapped plan.
    max_users: 999999,
    max_ai_investigations_monthly: null,
    max_alerts_monthly: null,
    features: features({ multi_llm: true, api_access: true, sso_saml: true }),
  },
]

const USAGE = {
  plan: { name: 'team', display_name: 'Team', tier: 'team' },
  limits: {
    users: { limit: 10, used: 4, unlimited: false },
    ai_investigations: { limit: 500, used: 120, unlimited: false },
    alerts: { limit: 5000, used: 480, unlimited: false },
    history_days: 30,
    llm_providers: 3,
    integrations: 10,
  },
  features: features(),
}

/** As the API sends it: newest month first, no `_count` suffix anywhere. */
const HISTORY = [
  {
    year_month: '2026-08',
    ai_investigations: 12,
    alerts_processed: 480,
    api_calls: 9100,
    postmortems_generated: 7,
    auto_fix_prs: 2,
    similar_searches: 31,
    active_users: 4,
    storage_bytes: 2048,
  },
  {
    year_month: '2026-07',
    ai_investigations: 5,
    alerts_processed: 220,
    api_calls: 4300,
    postmortems_generated: 3,
    auto_fix_prs: 1,
    similar_searches: 12,
    active_users: 3,
    storage_bytes: 1024,
  },
  {
    year_month: '2026-06',
    ai_investigations: 1,
    alerts_processed: 90,
    api_calls: 1200,
    postmortems_generated: 0,
    auto_fix_prs: 0,
    similar_searches: 4,
    active_users: 2,
    storage_bytes: 512,
  },
]

const SUBSCRIPTION = {
  plan: { ...PLANS[1], is_current: true },
  status: 'active',
  billing_cycle: 'monthly',
  is_trialing: false,
  days_remaining_trial: null,
  current_period_end: '2026-09-24T00:00:00+00:00',
  days_until_renewal: 28,
  cancel_at_period_end: false,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(overrides: Record<string, () => Response> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      // Drop the query string: the page pins limits and month counts that are
      // not what any of these tests is about.
      const url = String(input).split('?')[0]
      if (overrides[url]) return overrides[url]()
      if (url === '/billing/plans') return json(PLANS)
      if (url === '/billing/subscription') return json(SUBSCRIPTION)
      if (url === '/billing/usage') return json(USAGE)
      if (url === '/billing/usage/history') return json(HISTORY)
      if (url === '/billing/invoices') return json([])
      if (url === '/billing/payment-methods') return json([])
      return json({ detail: `no route for ${url}` }, 405)
    })
  )
}

async function openTab(name: string) {
  render(<Billing onBack={() => {}} />)
  fireEvent.click(await screen.findByText(name))
}

describe('Billing usage history', () => {
  beforeEach(() => installFetch())

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('plots the counts the API actually returns', async () => {
    await openTab('Usage')

    // Every one of these was 0 while the charts asked for `*_count`.
    expect(await screen.findByText('12 in Aug 2026')).toBeInTheDocument()
    expect(screen.getByText('480 in Aug 2026')).toBeInTheDocument()
    expect(screen.getByText('9,100 in Aug 2026')).toBeInTheDocument()
    expect(screen.getByText('7 in Aug 2026')).toBeInTheDocument()
  })

  it('reports the newest month, not the oldest, as the latest figure', async () => {
    await openTab('Usage')
    await screen.findByText('12 in Aug 2026')

    // June's 1 investigation is what the old caption called "this month".
    expect(screen.queryByText(/^1 in /)).not.toBeInTheDocument()
  })

  it('runs the axis from the oldest month to the newest', async () => {
    await openTab('Usage')
    await screen.findByText('12 in Aug 2026')

    const june = screen.getAllByText('Jun')[0]
    const august = screen.getAllByText('Aug')[0]
    expect(
      june.compareDocumentPosition(august) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it('says there is no data rather than drawing an empty chart', async () => {
    installFetch({ '/billing/usage/history': () => json([]) })
    await openTab('Usage')

    expect((await screen.findAllByText('No data available')).length).toBe(4)
  })
})

describe('Billing subscription load failures', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('says the subscription could not be read instead of showing no plan', async () => {
    installFetch({
      '/billing/subscription': () => json({ detail: 'Billing store unavailable' }, 503),
    })
    render(<Billing onBack={() => {}} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Billing store unavailable/)
    expect(alert).toHaveTextContent(/cannot show which plan you are on/)
  })

  it('keeps the rest of the page usable when the subscription fails', async () => {
    installFetch({
      '/billing/subscription': () => json({ detail: 'Billing store unavailable' }, 503),
    })
    render(<Billing onBack={() => {}} />)
    await screen.findByRole('alert')

    // Plans, usage and invoices all loaded; only the subscription did not.
    expect(screen.getByText('AI Investigations')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Plans'))
    expect(await screen.findByText('Team')).toBeInTheDocument()
  })

  it('stays quiet when the org genuinely has no subscription', async () => {
    installFetch({ '/billing/subscription': () => json(null) })
    render(<Billing onBack={() => {}} />)
    await screen.findByText('Current Usage')

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('retries the whole load from the warning', async () => {
    let broken = true
    installFetch({
      '/billing/subscription': () =>
        broken ? json({ detail: 'Billing store unavailable' }, 503) : json(SUBSCRIPTION),
    })
    render(<Billing onBack={() => {}} />)
    await screen.findByRole('alert')

    broken = false
    fireEvent.click(screen.getByText('Retry'))

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    // The header chip only renders once the page knows the plan.
    expect(screen.getByText('Current plan:')).toBeInTheDocument()
  })
})

describe('Billing plan limits and savings', () => {
  beforeEach(() => installFetch())

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('does not advertise 999999 users on the uncapped plan', async () => {
    await openTab('Plans')

    expect(await screen.findByText('Unlimited users')).toBeInTheDocument()
    expect(screen.queryByText('999999 users')).not.toBeInTheDocument()
  })

  it('counts a single seat in the singular', async () => {
    await openTab('Plans')

    expect(await screen.findByText('1 user')).toBeInTheDocument()
  })

  it('takes the annual saving from the plans instead of inventing 17%', async () => {
    await openTab('Plans')

    expect(await screen.findByText(/Save up to 15%/)).toBeInTheDocument()
    expect(screen.queryByText(/17%/)).not.toBeInTheDocument()
  })
})
