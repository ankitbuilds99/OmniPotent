/**
 * React hook for Supabase authentication.
 */

import { useEffect, useState, useCallback } from 'react'
import { initSupabase, checkSupabaseConfigured } from '../lib/supabase'
import { clearAuthToken, clearSessionTokenFrom, setSessionToken } from '../lib/authToken'

// The client is loaded by dynamic import, so there is no static type for it.
type AnyClient = any

interface User {
  id: string
  email?: string
  user_metadata?: Record<string, unknown>
  app_metadata?: Record<string, unknown>
  created_at?: string
}

interface Session {
  user: User
  access_token: string
}

interface AuthError {
  message: string
}

interface UseSupabaseAuthReturn {
  user: User | null
  session: Session | null
  loading: boolean
  error: AuthError | null
  signInWithGitHub: () => Promise<void>
  signInWithGoogle: () => Promise<void>
  signInWithEmail: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<void>
  /** Rejects if the mail was not requested, so no caller can claim it was sent. */
  resetPassword: (email: string) => Promise<void>
  signOut: () => Promise<void>
  isConfigured: boolean
}

export function useSupabaseAuth(): UseSupabaseAuthReturn {
  const [user, setUser] = useState<User | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<AuthError | null>(null)
  const [client, setClient] = useState<AnyClient>(null)
  const [isConfigured, setIsConfigured] = useState(false)

  // Adopt a Supabase session into both React state and the shared token store.
  // The backend accepts the access token as a bearer credential, but the API
  // clients read from authToken — without mirroring it there, a signed-in user
  // still sends no credential and every request comes back 401.
  const adoptSession = useCallback((sess: Session | null) => {
    setSession(sess)
    setUser(sess?.user ?? null)
    if (sess?.access_token) {
      setSessionToken(sess.access_token, 'firebase')
    } else {
      // Supabase having no session means the token it mirrored here is dead.
      // Leaving it behind is worse than having none: the login page sees a
      // credential, redirects on it, and every request from the console then
      // comes back 401 with no route back to the form.
      clearSessionTokenFrom('firebase')
    }
  }, [])

  useEffect(() => {
    let mounted = true
    let unsubscribe: (() => void) | null = null

    // `loading` disables every field and button on the login page, so a step of
    // this bootstrap that rejects does not degrade the page — it bricks it, with
    // no message outside the developer console. That is exactly what happened
    // when the browser blocked the Supabase host: getSession() rejected while
    // refreshing a stored token, the old .then() chain never ran, and the form
    // stayed disabled behind a spinner on every reload. Nothing below may throw
    // past the finally that clears the flag.
    const bootstrap = async () => {
      let configured = false
      try {
        configured = await checkSupabaseConfigured()
      } catch {
        configured = false
      }
      if (!mounted) return
      setIsConfigured(configured)
      if (!configured) return

      let supabase: AnyClient = null
      try {
        supabase = await initSupabase()
      } catch {
        supabase = null
      }
      if (!mounted) return
      if (!supabase) {
        setError({ message: 'Sign-in is unavailable — the auth client did not load.' })
        return
      }
      setClient(supabase)

      try {
        const { data } = await supabase.auth.getSession()
        if (!mounted) return
        adoptSession(data.session)
      } catch {
        if (!mounted) return
        // A stored session we cannot refresh is worse than no session: it fails
        // again on every load, and each failure locks the form the user needs in
        // order to recover. Drop it locally — scope 'local' touches storage
        // only, so it still works when the network is what failed.
        adoptSession(null)
        try {
          await supabase.auth.signOut({ scope: 'local' })
        } catch {
          // Best effort. The user can still sign in from a clean form.
        }
        if (!mounted) return
        setError({ message: 'Your saved session could not be restored. Please sign in again.' })
      }

      if (!mounted) return
      const { data: { subscription } } = supabase.auth.onAuthStateChange(
        (_event: string, sess: Session | null) => {
          if (mounted) {
            adoptSession(sess)
            setLoading(false)
          }
        }
      )
      unsubscribe = () => subscription.unsubscribe()
    }

    bootstrap().finally(() => {
      if (mounted) setLoading(false)
    })

    return () => {
      mounted = false
      unsubscribe?.()
    }
  }, [adoptSession])

  // Every entry point below rejects when the user was not signed in.
  //
  // They used to report failure only by setting `error`, and the login page read
  // a resolved promise as success: a wrong password, an unreachable auth host or
  // a client that never loaded all sent the visitor on to the console with no
  // credential stored, where every request then came back 401. Reporting through
  // the return value is the only signal a caller can act on.
  const fail = useCallback((err: unknown, fallback: string): never => {
    // Supabase reports a rejected credential as a plain { message } object as
    // often as an Error, and String()-ing one of those puts "[object Object]"
    // in front of the user.
    const reported =
      err instanceof Error
        ? err.message
        : typeof (err as { message?: unknown })?.message === 'string'
          ? (err as { message: string }).message
          : ''
    const message = reported || fallback
    setError({ message })
    throw new Error(message)
  }, [])

  const signInWithOAuth = useCallback(async (provider: 'github' | 'google') => {
    if (!client) {
      fail(null, 'Sign-in is unavailable — the auth client did not load.')
    }
    setError(null)
    try {
      const { error: err } = await client.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
        },
      })
      if (err) fail(err, `Could not start ${provider} sign-in.`)
    } catch (err) {
      fail(err, `Could not start ${provider} sign-in.`)
    }
  }, [client, fail])

  const signInWithGitHub = useCallback(() => signInWithOAuth('github'), [signInWithOAuth])
  const signInWithGoogle = useCallback(() => signInWithOAuth('google'), [signInWithOAuth])

  const signInWithEmail = useCallback(async (email: string, password: string) => {
    if (!client) {
      fail(null, 'Sign-in is unavailable — the auth client did not load.')
    }
    setError(null)
    setLoading(true)
    try {
      const { data, error: err } = await client.auth.signInWithPassword({ email, password })
      if (err) fail(err, 'Sign-in failed.')
      // Adopt the session here rather than waiting for onAuthStateChange: the
      // caller navigates as soon as this resolves, and the API clients read the
      // token from the shared store, not from React state.
      if (!data?.session) fail(null, 'Sign-in did not return a session.')
      adoptSession(data.session)
    } catch (err) {
      fail(err, 'Sign-in failed.')
    } finally {
      setLoading(false)
    }
  }, [client, adoptSession, fail])

  const signUp = useCallback(async (email: string, password: string) => {
    if (!client) {
      fail(null, 'Sign-up is unavailable — the auth client did not load.')
    }
    setError(null)
    setLoading(true)
    try {
      const { data, error: err } = await client.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
        },
      })
      if (err) fail(err, 'Sign-up failed.')
      // A session comes back only when the project does not require email
      // confirmation. When it does, there is nothing to adopt and the caller
      // tells the user to check their mail.
      if (data?.session) adoptSession(data.session)
    } catch (err) {
      fail(err, 'Sign-up failed.')
    } finally {
      setLoading(false)
    }
  }, [client, adoptSession, fail])

  // Throws rather than reporting through `error`, because the caller has to know
  // whether to tell the user a link is on its way. The login page used to print
  // "Password reset link sent to your email!" without sending anything at all.
  const resetPassword = useCallback(async (email: string) => {
    if (!client) {
      throw new Error('Password reset is unavailable — the auth client did not load.')
    }
    setLoading(true)
    try {
      const { error: err } = await client.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/callback`,
      })
      if (err) throw new Error(err.message)
    } finally {
      setLoading(false)
    }
  }, [client])

  const signOut = useCallback(async () => {
    // Clear the shared token first, and unconditionally: Supabase may not be the
    // thing that authenticated this session (backend OAuth and self-serve signup
    // write to the same store), and a stale credential would keep the app logged
    // in after the user asked to leave.
    clearAuthToken()
    if (!client) return
    setError(null)
    const { error: err } = await client.auth.signOut()
    if (err) setError(err)
  }, [client])

  return {
    user,
    session,
    loading,
    error,
    signInWithGitHub,
    signInWithGoogle,
    signInWithEmail,
    signUp,
    resetPassword,
    signOut,
    isConfigured,
  }
}
