/**
 * React hook for Supabase authentication.
 */

import { useEffect, useState, useCallback } from 'react'
import { initSupabase, checkSupabaseConfigured } from '../lib/supabase'
import { clearAuthToken, setSessionToken } from '../lib/authToken'

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
  signOut: () => Promise<void>
  isConfigured: boolean
}

export function useSupabaseAuth(): UseSupabaseAuthReturn {
  const [user, setUser] = useState<User | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<AuthError | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- dynamic Supabase client
  const [client, setClient] = useState<any>(null)
  const [isConfigured, setIsConfigured] = useState(false)

  // Adopt a Supabase session into both React state and the shared token store.
  // The backend accepts the access token as a bearer credential, but the API
  // clients read from authToken — without mirroring it there, a signed-in user
  // still sends no credential and every request comes back 401.
  const adoptSession = useCallback((sess: Session | null) => {
    setSession(sess)
    setUser(sess?.user ?? null)
    if (sess?.access_token) setSessionToken(sess.access_token)
  }, [])

  useEffect(() => {
    let mounted = true
    let unsubscribe: (() => void) | null = null

    // Check config asynchronously (may need to fetch from API)
    checkSupabaseConfigured().then(configured => {
      if (!mounted) return
      setIsConfigured(configured)

      if (!configured) {
        setLoading(false)
        return
      }

      initSupabase().then(supabase => {
        if (!mounted || !supabase) {
          setLoading(false)
          return
        }

        setClient(supabase)

        supabase.auth.getSession().then(({ data }: { data: { session: Session | null } }) => {
          if (mounted) {
            adoptSession(data.session)
            setLoading(false)
          }
        })

        const { data: { subscription } } = supabase.auth.onAuthStateChange(
          (_event: string, sess: Session | null) => {
            if (mounted) {
              adoptSession(sess)
              setLoading(false)
            }
          }
        )

        unsubscribe = () => subscription.unsubscribe()
      })
    })

    return () => {
      mounted = false
      unsubscribe?.()
    }
  }, [adoptSession])

  const signInWithGitHub = useCallback(async () => {
    if (!client) return
    setError(null)
    const { error: err } = await client.auth.signInWithOAuth({
      provider: 'github',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    })
    if (err) setError(err)
  }, [client])

  const signInWithGoogle = useCallback(async () => {
    if (!client) return
    setError(null)
    const { error: err } = await client.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    })
    if (err) setError(err)
  }, [client])

  const signInWithEmail = useCallback(async (email: string, password: string) => {
    if (!client) return
    setError(null)
    setLoading(true)
    const { error: err } = await client.auth.signInWithPassword({ email, password })
    setLoading(false)
    if (err) setError(err)
  }, [client])

  const signUp = useCallback(async (email: string, password: string) => {
    if (!client) return
    setError(null)
    setLoading(true)
    const { error: err } = await client.auth.signUp({
      email,
      password,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    })
    setLoading(false)
    if (err) setError(err)
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
    signOut,
    isConfigured,
  }
}
