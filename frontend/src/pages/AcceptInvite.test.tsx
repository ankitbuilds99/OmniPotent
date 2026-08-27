// Team.tsx mails every invitee a link to /invite/{id}, but the SPA had no route
// for that path and nothing in the frontend ever called the accept endpoint. The
// invitee landed on the marketing page, and the invitation sat pending until it
// expired seven days later.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AcceptInvite, PENDING_INVITATION_KEY } from './AcceptInvite'
import { setSessionToken, clearAuthToken } from '../lib/authToken'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(accept: () => Response) {
  const handler = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.includes('/accept') && (init?.method ?? 'GET').toUpperCase() === 'POST') {
      return accept()
    }
    return json({ detail: `no route for ${url}` }, 404)
  })
  vi.stubGlobal('fetch', handler)
  return handler
}

function renderAt(id = 'inv-7') {
  return render(
    <MemoryRouter initialEntries={[`/invite/${id}`]}>
      <Routes>
        <Route path="/invite/:invitationId" element={<AcceptInvite />} />
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('Accepting a team invitation', () => {
  beforeEach(() => {
    localStorage.clear()
    clearAuthToken()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    clearAuthToken()
    localStorage.clear()
  })

  it('redeems the invitation against the real endpoint', async () => {
    setSessionToken('token-abc')
    const fetchMock = installFetch(() =>
      json({ message: 'Invitation accepted', team: { id: 'team-1', name: 'Acme' } })
    )

    renderAt()

    expect(await screen.findByText('You have joined Acme')).toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([input]) => String(input).includes('/accept'))
    expect(String(call![0])).toBe('/api/teams/invitations/inv-7/accept')
    expect((call![1] as RequestInit).method).toBe('POST')
  })

  it('sends an unauthenticated invitee to sign in and remembers where to return', async () => {
    const fetchMock = installFetch(() => json({}))

    renderAt('inv-9')

    expect(await screen.findByText('Sign in to join')).toBeInTheDocument()
    // Nothing was attempted without a credential — the endpoint would only 401.
    expect(fetchMock).not.toHaveBeenCalled()
    expect(localStorage.getItem(PENDING_INVITATION_KEY)).toBe('inv-9')

    fireEvent.click(screen.getByText('Sign in'))
    expect(await screen.findByText('Login page')).toBeInTheDocument()
  })

  it("passes on the server's reason when the invitation was issued to someone else", async () => {
    setSessionToken('token-abc')
    installFetch(() =>
      json({ detail: 'This invitation was issued to a different email address.' }, 403)
    )

    renderAt()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This invitation was issued to a different email address.'
    )
    expect(screen.getByText('This invitation could not be accepted')).toBeInTheDocument()
  })

  it('retries on demand after a failure', async () => {
    setSessionToken('token-abc')
    let attempt = 0
    installFetch(() => {
      attempt += 1
      return attempt === 1
        ? json({ detail: 'Invitation not found' }, 404)
        : json({ message: 'Invitation accepted', team: { id: 'team-1', name: 'Acme' } })
    })

    renderAt()
    await screen.findByText('This invitation could not be accepted')

    fireEvent.click(screen.getByText('Try again'))

    await waitFor(() => expect(screen.getByText('You have joined Acme')).toBeInTheDocument())
  })
})
