// "Send reset link" sent no request at all — it printed "Password reset link
// sent to your email!" and switched back to the login form. A locked-out user
// waited for mail that was never requested, and could repeat that forever
// without ever regaining access.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Login } from './Login'
import { clearAuthToken } from '../lib/authToken'

const resetPassword = vi.fn()
let supabaseConfigured = true

vi.mock('../hooks/useSupabaseAuth', () => ({
  useSupabaseAuth: () => ({
    user: null,
    session: null,
    loading: false,
    error: null,
    signInWithGitHub: vi.fn(),
    signInWithGoogle: vi.fn(),
    signInWithEmail: vi.fn(),
    signUp: vi.fn(),
    resetPassword,
    signOut: vi.fn(),
    isConfigured: supabaseConfigured,
  }),
}))

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function installFetch(providers: unknown[] = []) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/auth/providers')) return json(providers)
      return json({}, 404)
    })
  )
}

/** Open the forgot-password form and submit `email`. */
async function requestReset(email = 'dana@acme.test') {
  render(<Login onBack={() => {}} onSuccess={() => {}} />)
  fireEvent.click(await screen.findByText('Forgot password?'))
  fireEvent.change(screen.getByPlaceholderText('you@company.com'), { target: { value: email } })
  fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
}

describe('Password reset', () => {
  beforeEach(() => {
    supabaseConfigured = true
    resetPassword.mockReset()
    clearAuthToken()
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('actually asks for the mail before saying it was sent', async () => {
    installFetch()
    resetPassword.mockResolvedValue(undefined)

    await requestReset()

    await waitFor(() => expect(resetPassword).toHaveBeenCalledWith('dana@acme.test'))
    expect(await screen.findByText('Password reset link sent to your email!')).toBeInTheDocument()
  })

  it('does not claim the mail was sent when the request fails', async () => {
    installFetch()
    resetPassword.mockRejectedValue(new Error('For security purposes, wait 60 seconds'))

    await requestReset()

    expect(
      await screen.findByText('For security purposes, wait 60 seconds')
    ).toBeInTheDocument()
    expect(screen.queryByText('Password reset link sent to your email!')).not.toBeInTheDocument()
  })

  it('says there is no password to reset when sign-in is by OAuth', async () => {
    supabaseConfigured = false
    installFetch([{ id: 'github', name: 'GitHub', icon: 'github' }])

    await requestReset()

    // Named, so the user knows which button to press instead.
    expect(await screen.findByText(/no password to reset.*GitHub/i)).toBeInTheDocument()
    expect(screen.queryByText('Password reset link sent to your email!')).not.toBeInTheDocument()
    expect(resetPassword).not.toHaveBeenCalled()
  })

  it('says reset is unavailable when nothing can sign the user in', async () => {
    supabaseConfigured = false
    installFetch([])

    await requestReset()

    expect(await screen.findByText(/Password reset is not available/i)).toBeInTheDocument()
    expect(screen.queryByText('Password reset link sent to your email!')).not.toBeInTheDocument()
  })
})
