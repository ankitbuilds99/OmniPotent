// "Send reset link" sent no request at all — it printed "Password reset link
// sent to your email!" and switched back to the login form. A locked-out user
// waited for mail that was never requested, and could repeat that forever
// without ever regaining access.

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Login } from './Login'
import { clearAuthToken } from '../lib/authToken'

const resetPassword = vi.fn()
const signInWithEmail = vi.fn()
const signInWithGitHub = vi.fn()
let firebaseConfigured = true

vi.mock('../hooks/useFirebaseAuth', () => ({
  useFirebaseAuth: () => ({
    user: null,
    session: null,
    loading: false,
    error: null,
    signInWithGitHub,
    signInWithGoogle: vi.fn(),
    signInWithEmail,
    signUp: vi.fn(),
    resetPassword,
    signOut: vi.fn(),
    isConfigured: firebaseConfigured,
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

/** Fill in the login form and submit it. */
async function signIn(onSuccess: () => void, password = 'correct horse') {
  render(<Login onBack={() => {}} onSuccess={onSuccess} />)
  fireEvent.change(await screen.findByPlaceholderText('you@company.com'), {
    target: { value: 'dana@acme.test' },
  })
  fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: password } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('Sign in', () => {
  beforeEach(() => {
    firebaseConfigured = true
    signInWithEmail.mockReset()
    signInWithGitHub.mockReset()
    clearAuthToken()
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('does not let a rejected password through', async () => {
    // The page treated a resolved promise as success while the hook reported
    // failure only by setting `error`, so a wrong password still navigated on
    // — to a console holding no credential, where every request came back 401.
    installFetch()
    signInWithEmail.mockRejectedValue(new Error('Invalid login credentials'))
    const onSuccess = vi.fn()

    await signIn(onSuccess, 'wrong')

    expect(await screen.findByText('Invalid login credentials')).toBeInTheDocument()
    expect(onSuccess).not.toHaveBeenCalled()
  })

  it('lets a successful sign-in through', async () => {
    installFetch()
    signInWithEmail.mockResolvedValue(undefined)
    const onSuccess = vi.fn()

    await signIn(onSuccess)

    await waitFor(() => expect(onSuccess).toHaveBeenCalled())
  })

  it('says so when the OAuth redirect cannot be started', async () => {
    installFetch()
    signInWithGitHub.mockRejectedValue(new Error('Provider not enabled'))

    render(<Login onBack={() => {}} onSuccess={() => {}} />)
    fireEvent.click(await screen.findByText('Continue with GitHub'))

    // Without this the button went quiet: no redirect, and no explanation.
    expect(await screen.findByText('Provider not enabled')).toBeInTheDocument()
  })
})

describe('Social login buttons', () => {
  beforeEach(() => {
    firebaseConfigured = true
    clearAuthToken()
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('offers the Firebase providers when Firebase is configured', async () => {
    installFetch()

    render(<Login onBack={() => {}} onSuccess={() => {}} />)

    expect(await screen.findByText('Continue with GitHub')).toBeInTheDocument()
    expect(screen.getByText('Continue with Google')).toBeInTheDocument()
  })

  it('still offers a provider the backend can complete', async () => {
    // The backend OAuth route remains available when Firebase is not used.
    firebaseConfigured = false
    installFetch([{ id: 'google', name: 'Google', icon: 'google' }])

    render(<Login onBack={() => {}} onSuccess={() => {}} />)

    expect(await screen.findByText('Continue with Google')).toBeInTheDocument()
  })

})

describe('Password reset', () => {
  beforeEach(() => {
    firebaseConfigured = true
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
    firebaseConfigured = false
    installFetch([{ id: 'github', name: 'GitHub', icon: 'github' }])

    await requestReset()

    // Named, so the user knows which button to press instead.
    expect(await screen.findByText(/no password to reset.*GitHub/i)).toBeInTheDocument()
    expect(screen.queryByText('Password reset link sent to your email!')).not.toBeInTheDocument()
    expect(resetPassword).not.toHaveBeenCalled()
  })

  it('says reset is unavailable when nothing can sign the user in', async () => {
    firebaseConfigured = false
    installFetch([])

    await requestReset()

    expect(await screen.findByText(/Password reset is not available/i)).toBeInTheDocument()
    expect(screen.queryByText('Password reset link sent to your email!')).not.toBeInTheDocument()
  })
})
