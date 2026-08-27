/**
 * User Profile Page
 * Shows user info, connected accounts, and settings
 */

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  User,
  Mail,
  Shield,
  LogOut,
  ChevronLeft,
  Github,
  Chrome,
  Key,
  Clock,
  AlertTriangle
} from 'lucide-react'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'
import { apiFetch, describeFailure } from '../lib/apiClient'
import { getAuthToken } from '../lib/authToken'

/** GET /auth/me. The backend knows who is signed in whether or not Supabase does. */
interface AccountInfo {
  id: string
  email: string
  name: string
  avatar_url: string | null
  org_id: string | null
  role: string
  providers: string[]
}

interface ProfileProps {
  onBack: () => void
  onLogout: () => void
}

function providerIcon(provider: string) {
  if (provider === 'github') return Github
  if (provider === 'google') return Chrome
  return Mail
}

export function Profile({ onBack, onLogout }: ProfileProps) {
  const { user, session, signOut } = useSupabaseAuth()
  const [loggingOut, setLoggingOut] = useState(false)

  // This page used to read identity from Supabase alone. On a deployment that
  // signs users in through the backend's own OAuth — Supabase unconfigured —
  // that left a genuinely authenticated user looking at "Not available",
  // "Unknown", an avatar reading "N" and a panel telling them they were a guest.
  const [account, setAccount] = useState<AccountInfo | null>(null)
  const [accountError, setAccountError] = useState<string | null>(null)
  const [loadingAccount, setLoadingAccount] = useState(true)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      // No credential to ask with; Supabase state is the only identity there is.
      if (!getAuthToken()) {
        if (!cancelled) setLoadingAccount(false)
        return
      }
      try {
        const res = await apiFetch('/auth/me')
        if (!res.ok) throw new Error(await describeFailure(res))
        const data = (await res.json()) as AccountInfo
        if (!cancelled) {
          setAccount(data)
          setAccountError(null)
        }
      } catch (err) {
        // A 401 here is meaningful — the token this app is sending is not one
        // the server recognises — so it is reported rather than swallowed.
        if (!cancelled) setAccountError(err instanceof Error ? err.message : String(err))
      } finally {
        if (!cancelled) setLoadingAccount(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
    // Re-runs once Supabase mirrors its access token into the shared store.
  }, [session])

  const handleLogout = async () => {
    setLoggingOut(true)
    await signOut()
    onLogout()
  }

  // The backend's answer wins; Supabase metadata fills in what it does not carry.
  const nameFromMeta = user?.user_metadata?.full_name || user?.user_metadata?.name
  const avatarFromMeta = user?.user_metadata?.avatar_url || user?.user_metadata?.picture
  const providerFromMeta = user?.app_metadata?.provider

  const email = account?.email || user?.email || null
  const name =
    account?.name ||
    (typeof nameFromMeta === 'string' ? nameFromMeta : null) ||
    email?.split('@')[0] ||
    null
  const avatarUrl =
    account?.avatar_url || (typeof avatarFromMeta === 'string' ? avatarFromMeta : undefined)
  const providers =
    account?.providers && account.providers.length > 0
      ? account.providers
      : typeof providerFromMeta === 'string'
        ? [providerFromMeta]
        : []
  // Every user used to be told "Owner" — a confident claim about their
  // permissions that the server contradicts the first time an action 403s.
  const role = account?.role ?? null
  const createdAt = user?.created_at ? new Date(user.created_at).toLocaleDateString() : null

  const signedIn = account !== null || user !== null

  if (loadingAccount && !signedIn) {
    return (
      <div className="min-h-screen" style={{ backgroundColor: 'var(--bg)' }}>
        <header className="nav">
          <button
            className="nav__btn"
            onClick={onBack}
            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
          >
            <ChevronLeft size={18} />
            <span>Back to Console</span>
          </button>
          <div className="nav__right">
            <span className="nav__tag">Profile</span>
          </div>
        </header>
        <main className="max-w-2xl mx-auto px-4 py-8" style={{ color: 'var(--text-2)' }}>
          Loading your profile…
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--bg)' }}>
      {/* Header */}
      <header className="nav">
        <button
          className="nav__btn"
          onClick={onBack}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <ChevronLeft size={18} />
          <span>Back to Console</span>
        </button>
        <div className="nav__right">
          <span className="nav__tag">Profile</span>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-2xl mx-auto px-4 py-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          {accountError && (
            <div
              role="alert"
              className="rounded-xl p-4 mb-6 flex items-start gap-3 text-sm"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--warn, #f59e0b)',
                color: 'var(--text-2)'
              }}
            >
              <AlertTriangle size={18} style={{ color: 'var(--warn, #f59e0b)', flexShrink: 0 }} />
              <span>
                Could not read your account from the server: {accountError}. Anything it alone
                knows — your role in particular — is not shown below.
              </span>
            </div>
          )}

          {/* Profile Card */}
          <div
            className="rounded-xl p-6 mb-6"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)'
            }}
          >
            {/* Avatar & Name */}
            <div className="flex items-center gap-4 mb-6">
              {avatarUrl ? (
                <img
                  src={avatarUrl}
                  alt={name ?? 'Profile'}
                  className="w-20 h-20 rounded-full"
                  style={{ border: '3px solid var(--accent)' }}
                />
              ) : (
                <div
                  className="w-20 h-20 rounded-full flex items-center justify-center text-2xl font-bold"
                  style={{
                    backgroundColor: 'var(--accent)',
                    color: 'var(--bg)'
                  }}
                >
                  {(name ?? '?').charAt(0).toUpperCase()}
                </div>
              )}
              <div>
                <h1
                  className="text-2xl font-bold"
                  style={{ color: 'var(--text)' }}
                >
                  {name ?? 'Not signed in'}
                </h1>
                {email && <p style={{ color: 'var(--text-2)' }}>{email}</p>}
              </div>
            </div>

            {/* Info Grid. Rows are omitted rather than filled with placeholders:
                "Unknown" reads as a fact about the account, not about the page. */}
            <div className="grid gap-4">
              {email && (
                <div
                  className="flex items-center gap-3 p-3 rounded-lg"
                  style={{ backgroundColor: 'var(--bg)' }}
                >
                  <Mail size={20} style={{ color: 'var(--accent)' }} />
                  <div>
                    <p className="text-sm" style={{ color: 'var(--text-2)' }}>Email</p>
                    <p style={{ color: 'var(--text)' }}>{email}</p>
                  </div>
                </div>
              )}

              {providers.map((provider) => {
                const ProviderIcon = providerIcon(provider)
                return (
                  <div
                    key={provider}
                    className="flex items-center gap-3 p-3 rounded-lg"
                    style={{ backgroundColor: 'var(--bg)' }}
                  >
                    <ProviderIcon size={20} style={{ color: 'var(--accent)' }} />
                    <div>
                      <p className="text-sm" style={{ color: 'var(--text-2)' }}>Signed in with</p>
                      <p style={{ color: 'var(--text)', textTransform: 'capitalize' }}>{provider}</p>
                    </div>
                  </div>
                )
              })}

              {createdAt && (
                <div
                  className="flex items-center gap-3 p-3 rounded-lg"
                  style={{ backgroundColor: 'var(--bg)' }}
                >
                  <Clock size={20} style={{ color: 'var(--accent)' }} />
                  <div>
                    <p className="text-sm" style={{ color: 'var(--text-2)' }}>Member since</p>
                    <p style={{ color: 'var(--text)' }}>{createdAt}</p>
                  </div>
                </div>
              )}

              {role && (
                <div
                  className="flex items-center gap-3 p-3 rounded-lg"
                  style={{ backgroundColor: 'var(--bg)' }}
                >
                  <Shield size={20} style={{ color: 'var(--accent)' }} />
                  <div>
                    <p className="text-sm" style={{ color: 'var(--text-2)' }}>Role</p>
                    <p style={{ color: 'var(--text)', textTransform: 'capitalize' }}>{role}</p>
                  </div>
                </div>
              )}

              {account?.org_id && (
                <div
                  className="flex items-center gap-3 p-3 rounded-lg"
                  style={{ backgroundColor: 'var(--bg)' }}
                >
                  <User size={20} style={{ color: 'var(--accent)' }} />
                  <div>
                    <p className="text-sm" style={{ color: 'var(--text-2)' }}>Organization</p>
                    <p style={{ color: 'var(--text)' }}>{account.org_id}</p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Session Info */}
          {session && (
            <div
              className="rounded-xl p-6 mb-6"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--border)'
              }}
            >
              <h2
                className="text-lg font-semibold mb-4 flex items-center gap-2"
                style={{ color: 'var(--text)' }}
              >
                <Key size={20} style={{ color: 'var(--accent)' }} />
                Session
              </h2>
              <div
                className="p-3 rounded-lg text-sm font-mono"
                style={{
                  backgroundColor: 'var(--bg)',
                  color: 'var(--text-2)'
                }}
              >
                <p>Token: {session.access_token.slice(0, 20)}...{session.access_token.slice(-10)}</p>
              </div>
            </div>
          )}

          {/* Danger Zone */}
          <div
            className="rounded-xl p-6"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--bad)'
            }}
          >
            <h2
              className="text-lg font-semibold mb-4"
              style={{ color: 'var(--bad)' }}
            >
              Danger Zone
            </h2>
            <p
              className="mb-4 text-sm"
              style={{ color: 'var(--text-2)' }}
            >
              Signing out will end your current session. You'll need to sign in again to access the console.
            </p>
            <button
              onClick={handleLogout}
              disabled={loggingOut}
              className="px-4 py-2 rounded-lg font-medium flex items-center gap-2 transition-all"
              style={{
                backgroundColor: 'var(--bad)',
                color: 'white',
                opacity: loggingOut ? 0.7 : 1
              }}
            >
              <LogOut size={18} />
              {loggingOut ? 'Signing out...' : 'Sign Out'}
            </button>
          </div>

          {/* Guest state — keyed to whether anyone is actually signed in, not to
              whether Supabase happens to be the thing that would sign them in. */}
          {!signedIn && (
            <div
              className="rounded-xl p-6 mt-6 text-center"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--border)'
              }}
            >
              <User size={48} className="mx-auto mb-4" style={{ color: 'var(--text-2)' }} />
              <h2
                className="text-lg font-semibold mb-2"
                style={{ color: 'var(--text)' }}
              >
                Guest Mode
              </h2>
              <p style={{ color: 'var(--text-2)' }}>
                You're using OmniPotent without authentication.
                Sign in to save your investigations and access more features.
              </p>
            </div>
          )}
        </motion.div>
      </main>
    </div>
  )
}
