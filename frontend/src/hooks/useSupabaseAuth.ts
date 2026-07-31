/**
 * React hook for Supabase authentication.
 */

import { useEffect, useState, useCallback } from 'react'
import { initSupabase, checkSupabaseConfigured } from '../lib/supabase'

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
            setSession(data.session)
            setUser(data.session?.user ?? null)
            setLoading(false)
          }
        })

        const { data: { subscription } } = supabase.auth.onAuthStateChange(
          (_event: string, sess: Session | null) => {
            if (mounted) {
              setSession(sess)
              setUser(sess?.user ?? null)
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
  }, [])

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
