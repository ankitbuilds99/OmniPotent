// The public pricing section restated two numbers instead of reading them.
// "Save 15%" beside the yearly toggle was hardcoded — right by luck today,
// wrong the moment pricing moves — and the seat line tested `max_users === -1`
// for "unlimited" when GET /billing/plans returns 999999, so the enterprise
// card advertised "999999 users".

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Pricing } from './Pricing'

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
    max_users: 999999,
    max_ai_investigations_monthly: null,
    max_alerts_monthly: null,
    features: features({ multi_llm: true, api_access: true, sso_saml: true }),
  },
]

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Pricing section', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('calls an uncapped plan unlimited rather than quoting the sentinel', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(PLANS)))
    render(<Pricing />)

    expect(await screen.findByText('Unlimited users')).toBeInTheDocument()
    expect(screen.queryByText('999999 users')).not.toBeInTheDocument()
  })

  it('takes the yearly saving from the plans it loaded', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(PLANS)))
    render(<Pricing />)
    await screen.findByText('Unlimited users')

    expect(screen.getByText(/Save up to 15%/)).toBeInTheDocument()
  })

  it('honors the is_popular flag the API sends instead of forcing it onto team', async () => {
    // Every plan says "not popular". The old code overwrote that with
    // `tier === 'team'` and badged Team anyway; the API's answer now stands.
    const flagged = PLANS.map((p) => ({ ...p, is_popular: false }))
    vi.stubGlobal('fetch', vi.fn(async () => json(flagged)))
    render(<Pricing />)
    await screen.findByText('Unlimited users')

    expect(screen.queryByText('Most Popular')).not.toBeInTheDocument()
  })

  it('says pricing is unavailable when the API is down, instead of inventing a price list', async () => {
    // FALLBACK_PLANS used to render here: six made-up plans ($299/$699/...)
    // indistinguishable from a real quote. Now the section says so honestly
    // and offers the contact address.
    vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'nope' }, 503)))
    render(<Pricing />)

    expect(
      await screen.findByText('Pricing is temporarily unavailable')
    ).toBeInTheDocument()
    expect(screen.queryByText(/Save up to/)).not.toBeInTheDocument()
    expect(screen.queryByText('Start Free Trial')).not.toBeInTheDocument()

    const contact = screen.getByRole('link', { name: 'Contact Us' })
    expect(contact.getAttribute('href')).toContain('mailto:')
    // The old hardcoded addresses pointed at a domain the project doesn't own.
    expect(contact.getAttribute('href')).not.toContain('omnipotent.ai')
  })
})
