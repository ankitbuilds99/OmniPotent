/** React authentication hook backed by Firebase Authentication. */

import { useCallback, useEffect, useState } from 'react'
import {
  browserLocalPersistence,
  createUserWithEmailAndPassword,
  GithubAuthProvider,
  GoogleAuthProvider,
  onIdTokenChanged,
  sendPasswordResetEmail,
  setPersistence,
  signInWithEmailAndPassword,
  getRedirectResult,
  signInWithRedirect,
  signOut as firebaseSignOut,
  type User as FirebaseUser,
} from 'firebase/auth'
import { FirebaseError } from 'firebase/app'
import { firebaseAuth, isFirebaseConfigured } from '../lib/firebase'
import { clearAuthToken, clearSessionTokenFrom, setSessionToken } from '../lib/authToken'

export interface User {
  id: string
  email?: string
  user_metadata?: Record<string, unknown>
  app_metadata?: Record<string, unknown>
  created_at?: string
}

export interface Session {
  user: User
  access_token: string
}

interface AuthError {
  message: string
}

interface UseFirebaseAuthReturn {
  user: User | null
  session: Session | null
  loading: boolean
  error: AuthError | null
  signInWithGitHub: () => Promise<void>
  signInWithGoogle: () => Promise<void>
  signInWithEmail: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<void>
  resetPassword: (email: string) => Promise<void>
  signOut: () => Promise<void>
  isConfigured: boolean
}

function toUser(firebaseUser: FirebaseUser): User {
  return {
    id: firebaseUser.uid,
    email: firebaseUser.email ?? undefined,
    user_metadata: {
      name: firebaseUser.displayName ?? undefined,
      avatar_url: firebaseUser.photoURL ?? undefined,
    },
    app_metadata: { provider: firebaseUser.providerData[0]?.providerId ?? 'password' },
    created_at: firebaseUser.metadata.creationTime,
  }
}

function describeError(error: unknown, fallback: string): string {
  if (error instanceof FirebaseError) {
    const messages: Record<string, string> = {
      'auth/invalid-credential': 'The email or password is incorrect.',
      'auth/invalid-email': 'Enter a valid email address.',
      'auth/email-already-in-use': 'An account already exists for this email.',
      'auth/weak-password': 'Password must be at least 6 characters.',
      'auth/popup-closed-by-user': 'The sign-in window was closed before it finished.',
      'auth/popup-blocked': 'Your browser blocked the sign-in window. Allow popups and try again.',
      'auth/too-many-requests': 'Too many attempts. Please wait and try again.',
    }
    return messages[error.code] ?? (error.message || fallback)
  }
  return error instanceof Error ? error.message : fallback
}

export function useFirebaseAuth(): UseFirebaseAuthReturn {
  const [user, setUser] = useState<User | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<AuthError | null>(null)

  const adoptUser = useCallback(async (firebaseUser: FirebaseUser | null) => {
    if (!firebaseUser) {
      setUser(null)
      setSession(null)
      clearSessionTokenFrom('firebase')
      return null
    }

    const accessToken = await firebaseUser.getIdToken()
    const nextUser = toUser(firebaseUser)
    const nextSession = { user: nextUser, access_token: accessToken }
    setUser(nextUser)
    setSession(nextSession)
    setSessionToken(accessToken, 'firebase')
    return nextSession
  }, [])

  useEffect(() => {
    if (!isFirebaseConfigured || !firebaseAuth) {
      setLoading(false)
      return
    }

    let mounted = true
    const unsubscribe = onIdTokenChanged(firebaseAuth, (firebaseUser) => {
      void adoptUser(firebaseUser)
        .catch((authError) => {
          if (mounted) setError({ message: describeError(authError, 'Could not restore your session.') })
          clearSessionTokenFrom('firebase')
        })
        .finally(() => {
          if (mounted) setLoading(false)
        })
    })

    void setPersistence(firebaseAuth, browserLocalPersistence).catch((authError) => {
      if (mounted) setError({ message: describeError(authError, 'Could not persist your session.') })
    })

    // Complete a social login that returned through Firebase's full-page
    // redirect flow. Redirects avoid browser popup blockers and popup-close
    // races on localhost.
    void getRedirectResult(firebaseAuth)
      .then((result) => result && adoptUser(result.user))
      .catch((authError) => {
        if (mounted) setError({ message: describeError(authError, 'Could not complete sign-in.') })
      })

    return () => {
      mounted = false
      unsubscribe()
    }
  }, [adoptUser])

  const runAuth = useCallback(async (operation: () => Promise<FirebaseUser>) => {
    if (!firebaseAuth || !isFirebaseConfigured) {
      const message = 'Firebase Authentication is not configured for this deployment.'
      setError({ message })
      throw new Error(message)
    }

    setError(null)
    setLoading(true)
    try {
      await adoptUser(await operation())
    } catch (authError) {
      const message = describeError(authError, 'Authentication failed.')
      setError({ message })
      throw new Error(message)
    } finally {
      setLoading(false)
    }
  }, [adoptUser])

  const signInWithGitHub = useCallback(() => {
    return runAuth(async () => {
      if (!firebaseAuth) throw new Error('Firebase Authentication is unavailable.')
      await signInWithRedirect(firebaseAuth, new GithubAuthProvider())
      // The browser navigates away; this keeps the promise type honest for
      // the shared auth runner while the redirect completes in the next load.
      return new Promise<FirebaseUser>(() => {})
    })
  }, [runAuth])

  const signInWithGoogle = useCallback(() => {
    return runAuth(async () => {
      if (!firebaseAuth) throw new Error('Firebase Authentication is unavailable.')
      await signInWithRedirect(firebaseAuth, new GoogleAuthProvider())
      return new Promise<FirebaseUser>(() => {})
    })
  }, [runAuth])

  const signInWithEmail = useCallback((email: string, password: string) => {
    return runAuth(async () => {
      if (!firebaseAuth) throw new Error('Firebase Authentication is unavailable.')
      return (await signInWithEmailAndPassword(firebaseAuth, email, password)).user
    })
  }, [runAuth])

  const signUp = useCallback((email: string, password: string) => {
    return runAuth(async () => {
      if (!firebaseAuth) throw new Error('Firebase Authentication is unavailable.')
      return (await createUserWithEmailAndPassword(firebaseAuth, email, password)).user
    })
  }, [runAuth])

  const resetPassword = useCallback(async (email: string) => {
    if (!firebaseAuth || !isFirebaseConfigured) {
      throw new Error('Password reset is not configured for this deployment.')
    }
    setLoading(true)
    try {
      await sendPasswordResetEmail(firebaseAuth, email)
    } catch (authError) {
      const message = describeError(authError, 'Could not send the password reset email.')
      setError({ message })
      throw new Error(message)
    } finally {
      setLoading(false)
    }
  }, [])

  const signOut = useCallback(async () => {
    clearAuthToken()
    if (!firebaseAuth) return
    try {
      await firebaseSignOut(firebaseAuth)
      setUser(null)
      setSession(null)
    } catch (authError) {
      const message = describeError(authError, 'Could not sign out.')
      setError({ message })
      throw new Error(message)
    }
  }, [])

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
    isConfigured: isFirebaseConfigured && firebaseAuth !== null,
  }
}
