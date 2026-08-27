// The upgrade modal quoted the two prices the API returned and then captioned
// them "Save 17%" — a figure from nowhere. GET /billing/features/{key} hands
// back the required plan's monthly and annual-monthly price, and the saving
// between them is ~15% on every plan the pricing API describes.

import { render, screen, fireEvent } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FeatureGate } from './FeatureGate'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function gateResponse(price: { monthly: number | null; annual_monthly: number | null } | null) {
  return {
    available: false,
    feature: 'auto_fix_pr',
    reason: 'Auto-fix PRs require the Team plan.',
    upgrade: {
      feature: 'auto_fix_pr',
      current_plan: 'starter',
      required_plan: 'team',
      required_plan_display: 'Team',
      upgrade_url: '/billing?plan=team',
      ...(price ? { price } : {}),
    },
  }
}

function installFetch(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => json(body)))
}

async function openUpgradeModal() {
  render(
    <FeatureGate feature="auto_fix_pr">
      <div>Auto-fix panel</div>
    </FeatureGate>
  )
  fireEvent.click(await screen.findByText('Click to upgrade'))
}

describe('FeatureGate upgrade modal', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('works the saving out from the prices it just quoted', async () => {
    installFetch(gateResponse({ monthly: 299, annual_monthly: 254 }))
    await openUpgradeModal()

    expect(await screen.findByText('Save 15%')).toBeInTheDocument()
    expect(screen.queryByText('Save 17%')).not.toBeInTheDocument()
  })

  it('claims no saving when the two prices are the same', async () => {
    installFetch(gateResponse({ monthly: 299, annual_monthly: 299 }))
    await openUpgradeModal()

    // Both prices are on screen, and paying annually saves nothing.
    expect(await screen.findByText('Upgrade to Team')).toBeInTheDocument()
    expect(screen.getAllByText('$299/mo').length).toBe(2)
    expect(screen.queryByText(/^Save \d+%$/)).not.toBeInTheDocument()
  })

  it('shows the required plan without a price block when none is quoted', async () => {
    installFetch(gateResponse(null))
    await openUpgradeModal()

    expect(await screen.findByText('Upgrade to Team')).toBeInTheDocument()
    expect(screen.queryByText('Monthly')).not.toBeInTheDocument()
  })
})
