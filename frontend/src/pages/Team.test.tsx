// The Team page used to call GET /api/team/members — a route that does not exist
// — and on the 404 synthesise a single fake "owner" from the Supabase user.
// Invite/remove/role-change issued zero network calls and only mutated React
// state, so every mutation vanished on reload. These tests pin the page to the
// real backend surface (app/teams/router.py).

import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

import { Team } from './Team'

interface Call {
  url: string
  method: string
  body: unknown
}

let calls: Call[] = []

const TEAM = {
  id: 'team-1',
  name: 'Acme',
  slug: 'acme',
  owner_id: 'u-owner',
  member_count: 2,
  role: 'owner',
}

const MEMBERS = [
  {
    id: 'm-1',
    user_id: 'u-owner',
    email: 'owner@acme.test',
    name: 'Owner',
    role: 'owner',
    joined_at: '2026-01-01T00:00:00',
    last_active: null,
    avatar_url: null,
  },
  {
    id: 'm-2',
    user_id: 'u-bob',
    email: 'bob@acme.test',
    name: 'Bob',
    role: 'member',
    joined_at: '2026-01-02T00:00:00',
    last_active: null,
    avatar_url: null,
  },
]

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Route table standing in for the backend. Anything unrouted 404s loudly so a
 *  stray call to a non-existent path shows up as a test failure, not silence. */
function installFetch(overrides: Record<string, () => Response> = {}) {
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    calls.push({
      url,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    })
    const key = `${method} ${url}`
    if (overrides[key]) return overrides[key]()
    if (key === 'GET /frontend-config') return json({})
    if (key === 'GET /auth/me') return json({ email: 'owner@acme.test' })
    if (key === 'GET /api/teams') return json([TEAM])
    if (key === 'GET /api/teams/team-1/members') return json(MEMBERS)
    if (key === 'GET /api/teams/team-1/invitations') return json([])
    return json({ detail: `no route for ${key}` }, 404)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

describe('Team page', () => {
  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('loads members from the real /api/teams surface', async () => {
    installFetch()
    render(<Team onBack={() => {}} />)

    expect(await screen.findByText('bob@acme.test')).toBeInTheDocument()

    const urls = calls.map(c => `${c.method} ${c.url}`)
    expect(urls).toContain('GET /api/teams')
    expect(urls).toContain('GET /api/teams/team-1/members')
    // The route that never existed must be gone.
    expect(urls.some(u => u.includes('/api/team/members'))).toBe(false)
  })

  it('never invents a member when the user has no team', async () => {
    installFetch({ 'GET /api/teams': () => json([]) })
    render(<Team onBack={() => {}} />)

    expect(await screen.findByText(/do not belong to a team yet/i)).toBeInTheDocument()
    expect(screen.getByText('Members (0)')).toBeInTheDocument()
    // No fabricated member row: nothing that looks like a member email is shown.
    expect(screen.queryByText(/@/)).not.toBeInTheDocument()
  })

  it('POSTs a real invitation instead of faking one client-side', async () => {
    installFetch({
      'POST /api/teams/team-1/invitations': () =>
        json({
          id: 'inv-1',
          email: 'new@acme.test',
          role: 'member',
          status: 'pending',
          created_at: '2026-01-03T00:00:00',
          expires_at: '2026-01-10T00:00:00',
        }),
    })
    render(<Team onBack={() => {}} />)
    await screen.findByText('bob@acme.test')

    fireEvent.click(screen.getByRole('button', { name: /invite member/i }))
    fireEvent.change(screen.getByPlaceholderText('colleague@company.com'), {
      target: { value: 'new@acme.test' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send invitation/i }))

    await waitFor(() => {
      const post = calls.find(c => c.method === 'POST' && c.url.endsWith('/invitations'))
      expect(post).toBeDefined()
      expect(post!.url).toBe('/api/teams/team-1/invitations')
      expect(post!.body).toEqual({ email: 'new@acme.test', role: 'member' })
    })
    expect(await screen.findByText('new@acme.test')).toBeInTheDocument()
  })

  it('surfaces the backend reason when an invitation is rejected', async () => {
    installFetch({
      'POST /api/teams/team-1/invitations': () =>
        json({ detail: 'Cannot create invitation (user may already be a member)' }, 400),
    })
    render(<Team onBack={() => {}} />)
    await screen.findByText('bob@acme.test')

    fireEvent.click(screen.getByRole('button', { name: /invite member/i }))
    fireEvent.change(screen.getByPlaceholderText('colleague@company.com'), {
      target: { value: 'dupe@acme.test' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send invitation/i }))

    expect(
      await screen.findByText('Cannot create invitation (user may already be a member)')
    ).toBeInTheDocument()
    // The failed invite must not appear in the pending list.
    expect(screen.queryByText('dupe@acme.test')).not.toBeInTheDocument()
  })

  it('DELETEs the member by user_id when removing', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    installFetch({
      'DELETE /api/teams/team-1/members/u-bob': () => json({ message: 'Member removed' }),
    })
    render(<Team onBack={() => {}} />)
    await screen.findByText('bob@acme.test')

    fireEvent.click(screen.getByTitle('Remove member'))

    await waitFor(() => {
      expect(calls).toContainEqual({
        url: '/api/teams/team-1/members/u-bob',
        method: 'DELETE',
        body: undefined,
      })
    })
    await waitFor(() => expect(screen.queryByText('bob@acme.test')).not.toBeInTheDocument())
  })

  it('keeps the member listed when the remove call fails', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    installFetch({
      'DELETE /api/teams/team-1/members/u-bob': () =>
        json({ detail: 'Cannot remove member (owner cannot be removed)' }, 400),
    })
    render(<Team onBack={() => {}} />)
    await screen.findByText('bob@acme.test')

    fireEvent.click(screen.getByTitle('Remove member'))

    expect(
      await screen.findByText('Cannot remove member (owner cannot be removed)')
    ).toBeInTheDocument()
    expect(screen.getByText('bob@acme.test')).toBeInTheDocument()
  })

  it('PATCHes the role when it is changed', async () => {
    installFetch({
      'PATCH /api/teams/team-1/members/u-bob': () => json({ ...MEMBERS[1], role: 'admin' }),
    })
    render(<Team onBack={() => {}} />)
    await screen.findByText('bob@acme.test')

    fireEvent.change(screen.getByLabelText('Role for bob@acme.test'), {
      target: { value: 'admin' },
    })

    await waitFor(() => {
      const patch = calls.find(c => c.method === 'PATCH')
      expect(patch).toBeDefined()
      expect(patch!.url).toBe('/api/teams/team-1/members/u-bob')
      expect(patch!.body).toEqual({ role: 'admin' })
    })
  })
})

// The mailed link is the primary way to redeem an invitation, but this
// deployment only sends mail when EmailJS is configured. Without somewhere to
// accept from inside the app, an invitee whose mail never arrived could not
// join at all, and the invitation expired seven days later.
describe('Invitations addressed to me', () => {
  const PENDING = [
    {
      invitation: {
        id: 'inv-42',
        email: 'owner@acme.test',
        role: 'member',
        status: 'pending',
        created_at: '2026-01-03T00:00:00',
        expires_at: '2026-01-10T00:00:00',
      },
      team: { id: 'team-9', name: 'Platform' },
    },
  ]

  beforeEach(() => {
    calls = []
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('offers to accept an invitation the signed-in user has been sent', async () => {
    installFetch({
      'GET /api/teams/invitations/pending': () => json(PENDING),
      'POST /api/teams/invitations/inv-42/accept': () =>
        json({ message: 'Invitation accepted', team: { id: 'team-9', name: 'Platform' } }),
    })
    render(<Team onBack={() => {}} />)

    expect(await screen.findByText('Invitations for you (1)')).toBeInTheDocument()
    expect(screen.getByText('Platform')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /accept/i }))

    expect(await screen.findByText('You have joined Platform')).toBeInTheDocument()
    const accept = calls.find(c => c.method === 'POST' && c.url.endsWith('/accept'))
    expect(accept!.url).toBe('/api/teams/invitations/inv-42/accept')
  })

  it('reports why an invitation was refused instead of pretending it was joined', async () => {
    installFetch({
      'GET /api/teams/invitations/pending': () => json(PENDING),
      'POST /api/teams/invitations/inv-42/accept': () =>
        json({ detail: 'Cannot accept invitation (status: expired)' }, 400),
    })
    render(<Team onBack={() => {}} />)
    await screen.findByText('Invitations for you (1)')

    fireEvent.click(screen.getByRole('button', { name: /accept/i }))

    expect(
      await screen.findByText('Cannot accept invitation (status: expired)')
    ).toBeInTheDocument()
    expect(screen.queryByText(/You have joined/)).not.toBeInTheDocument()
  })

  it('shows no panel when nothing is waiting for the user', async () => {
    installFetch({ 'GET /api/teams/invitations/pending': () => json([]) })
    render(<Team onBack={() => {}} />)
    await screen.findByText('bob@acme.test')

    expect(screen.queryByText(/Invitations for you/)).not.toBeInTheDocument()
  })
})
