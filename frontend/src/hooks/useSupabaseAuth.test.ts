// `loading` gates every field and button on the login page, and the bootstrap
// that clears it had no error handling. On the deployed instance the browser
// blocked the Supabase host (it was missing from the CSP), so restoring a stored
// session rejected with "Failed to fetch", the .then() chain never ran, and the
// login form stayed disabled behind a spinner — on that load and on every reload
// after it, because the session that could not be restored was never dropped.
//
// The other half: a failed sign-in reported failure only by setting `error`, so
// a caller awaiting the promise saw success and sent the user on with no
// credential stored.

import { renderHook, waitFor, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const checkSupabaseConfigured = vi.fn()
const initSupabase = vi.fn()

vi.mock('../lib/supabase', () => ({
  checkSupabaseConfigured: () => checkSupabaseConfigured(),
  initSupabase: () => initSupabase(),
}))

import { useSupabaseAuth } from './useSupabaseAuth'
import { getAuthToken, setSessionToken, clearAuthToken } from '../lib/authToken'

const SESSION = { access_token: 'jwt-abc', user: { id: 'u1', email: 'dana@acme.test' } }

/** A stub client whose auth calls are individually controllable. */
function stubClient(overrides: Record<string, unknown> = {}) {
  return {
    auth: {
      getSession: vi.fn(async () => ({ data: { session: null } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signInWithPassword: vi.fn(async () => ({ data: { session: SESSION }, error: null })),
      signUp: vi.fn(async () => ({ data: { session: null }, error: null })),
      signInWithOAuth: vi.fn(async () => ({ data: {}, error: null })),
      signOut: vi.fn(async () => ({ error: null })),
      ...overrides,
    },
  }
}

beforeEach(() => {
  clearAuthToken()
  checkSupabaseConfigured.mockReset().mockResolvedValue(true)
  initSupabase.mockReset()
})

afterEach(() => {
  clearAuthToken()
  vi.restoreAllMocks()
})

describe('the auth bootstrap always finishes', () => {
  it('clears loading when Supabase is not configured', async () => {
    checkSupabaseConfigured.mockResolvedValue(false)

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.isConfigured).toBe(false)
  })

  it('clears loading when the config check itself fails', async () => {
    checkSupabaseConfigured.mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.isConfigured).toBe(false)
  })

  it('clears loading and explains itself when the client never loads', async () => {
    initSupabase.mockRejectedValue(new Error('chunk load failed'))

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error?.message).toMatch(/auth client did not load/i)
  })

  it('clears loading, and the unusable session, when restoring it rejects', async () => {
    // This is the deployed failure: the refresh request is blocked, so
    // getSession() rejects rather than returning a null session.
    const client = stubClient({
      getSession: vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    })
    initSupabase.mockResolvedValue(client)
    setSessionToken('stale-jwt', 'supabase')

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.user).toBeNull()
    expect(result.current.error?.message).toMatch(/sign in again/i)
    // Dropped locally so the next load starts from a clean, usable form
    // instead of failing on the same stored session again.
    expect(getAuthToken()).toBe('')
    expect(client.auth.signOut).toHaveBeenCalledWith({ scope: 'local' })
  })

  it('drops a mirrored token once Supabase reports no session', async () => {
    // The refresh reaches Supabase and is refused, so getSession() resolves
    // with null rather than rejecting. The token it mirrored here earlier is
    // dead all the same, and a login page that finds one redirects on it.
    initSupabase.mockResolvedValue(stubClient())
    setSessionToken('stale-jwt', 'supabase')

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(getAuthToken()).toBe('')
  })

  it('leaves a token the backend OAuth callback stored alone', async () => {
    // Supabase is configured but nobody signed in through it. That is no reason
    // to sign out a session the other login flow established.
    initSupabase.mockResolvedValue(stubClient())
    setSessionToken('backend-oauth-jwt', 'oauth')

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(getAuthToken()).toBe('backend-oauth-jwt')
  })

  it('adopts a restored session into the shared token store', async () => {
    initSupabase.mockResolvedValue(
      stubClient({ getSession: vi.fn(async () => ({ data: { session: SESSION } })) })
    )

    const { result } = renderHook(() => useSupabaseAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.user?.email).toBe('dana@acme.test')
    expect(getAuthToken()).toBe('jwt-abc')
  })
})

describe('a sign-in that did not sign anyone in rejects', () => {
  async function ready(client: unknown) {
    initSupabase.mockResolvedValue(client)
    const { result } = renderHook(() => useSupabaseAuth())
    await waitFor(() => expect(result.current.loading).toBe(false))
    return result
  }

  /** Run `call` to completion and hand back whatever it rejected with.
   *  Caught inside act() so the rejection is never left unhandled. */
  async function rejection(call: () => Promise<unknown>): Promise<Error> {
    let caught: unknown = null
    await act(async () => {
      caught = await call().then(() => null, (err) => err)
    })
    expect(caught, 'expected the call to reject').toBeInstanceOf(Error)
    return caught as Error
  }

  it('rejects when the credentials are refused', async () => {
    const result = await ready(
      stubClient({
        signInWithPassword: vi.fn(async () => ({
          data: { session: null },
          error: { message: 'Invalid login credentials' },
        })),
      })
    )

    const err = await rejection(() => result.current.signInWithEmail('dana@acme.test', 'wrong'))

    expect(err.message).toBe('Invalid login credentials')
    expect(getAuthToken()).toBe('')
  })

  it('rejects when the request cannot be made at all', async () => {
    const result = await ready(
      stubClient({
        signInWithPassword: vi.fn(async () => {
          throw new TypeError('Failed to fetch')
        }),
      })
    )

    const err = await rejection(() => result.current.signInWithEmail('dana@acme.test', 'pw'))

    expect(err.message).toBe('Failed to fetch')
    expect(result.current.loading).toBe(false)
  })

  it('rejects when no session comes back despite no error', async () => {
    const result = await ready(
      stubClient({
        signInWithPassword: vi.fn(async () => ({ data: { session: null }, error: null })),
      })
    )

    const err = await rejection(() => result.current.signInWithEmail('dana@acme.test', 'pw'))

    expect(err.message).toMatch(/did not return a session/i)
  })

  it('stores the token before resolving on success', async () => {
    const result = await ready(stubClient())

    await act(() => result.current.signInWithEmail('dana@acme.test', 'pw'))

    // The caller navigates the moment this resolves, and the API clients read
    // the token from here — not from React state.
    expect(getAuthToken()).toBe('jwt-abc')
  })

  it('rejects when the OAuth redirect cannot be started', async () => {
    const result = await ready(
      stubClient({
        signInWithOAuth: vi.fn(async () => ({
          data: {},
          error: { message: 'Provider not enabled' },
        })),
      })
    )

    const err = await rejection(() => result.current.signInWithGitHub())

    expect(err.message).toBe('Provider not enabled')
  })
})
