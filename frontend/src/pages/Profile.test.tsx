// Two defects on one page. The "Role" row was the string literal "Owner", so a
// viewer was told they could manage the team and billing until an action 403'd.
// And identity was read from Supabase alone, so on a deployment that signs
// users in through the backend's own OAuth — Supabase unconfigured — someone
// who had just signed in with GitHub saw "Not available", "Unknown", an avatar
// reading "N" and a panel telling them they were a guest.

import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Profile } from './Profile'
import { setSessionToken, clearAuthToken } from '../lib/authToken'

const ACCOUNT = {
  id: 'u-9',
  email: 'dana@acme.test',
  name: 'Dana Okafor',
  avatar_url: null,
  org_id: 'acme',
  role: 'viewer',
  providers: ['github'],
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(me: () => Response) {
  const handler = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return me()
    // Supabase is not configured in this environment, which is the whole point.
    if (url.endsWith('/frontend-config')) return json({})
    return json({ detail: `no route for ${url}` }, 404)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

function renderProfile() {
  return render(<Profile onBack={() => {}} onLogout={() => {}} />)
}

describe('Profile identity', () => {
  beforeEach(() => {
    clearAuthToken()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    clearAuthToken()
  })

  it('shows the backend account for a user Supabase knows nothing about', async () => {
    setSessionToken('token-abc')
    installFetch(() => json(ACCOUNT))

    renderProfile()

    expect(await screen.findByText('Dana Okafor')).toBeInTheDocument()
    expect(screen.getAllByText('dana@acme.test').length).toBeGreaterThan(0)
    expect(screen.getByText('github')).toBeInTheDocument()
    expect(screen.getByText('acme')).toBeInTheDocument()
    // The old placeholders are claims about the account, and they were wrong.
    expect(screen.queryByText('Not available')).not.toBeInTheDocument()
    expect(screen.queryByText('Unknown')).not.toBeInTheDocument()
  })

  it('reports the role the server assigned, not "Owner" for everyone', async () => {
    setSessionToken('token-abc')
    installFetch(() => json(ACCOUNT))

    renderProfile()

    expect(await screen.findByText('viewer')).toBeInTheDocument()
    expect(screen.queryByText('Owner')).not.toBeInTheDocument()
  })

  it('does not call a signed-in user a guest', async () => {
    setSessionToken('token-abc')
    installFetch(() => json(ACCOUNT))

    renderProfile()
    await screen.findByText('Dana Okafor')

    expect(screen.queryByText('Guest Mode')).not.toBeInTheDocument()
  })

  it('still calls an unauthenticated visitor a guest', async () => {
    installFetch(() => json({ detail: 'Not authenticated' }, 401))

    renderProfile()

    expect(await screen.findByText('Guest Mode')).toBeInTheDocument()
    // And claims nothing about a role it has no way of knowing.
    expect(screen.queryByText('Role')).not.toBeInTheDocument()
  })

  it('says the account could not be read rather than guessing at a role', async () => {
    setSessionToken('token-abc')
    installFetch(() => json({ detail: 'Not authenticated' }, 401))

    renderProfile()

    expect(await screen.findByRole('alert')).toHaveTextContent('Not authenticated')
    expect(screen.queryByText('Role')).not.toBeInTheDocument()
  })
})
