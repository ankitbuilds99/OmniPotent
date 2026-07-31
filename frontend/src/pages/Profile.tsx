/**
 * User Profile Page
 * Shows user info, connected accounts, and settings
 */

import { useState } from 'react'
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
  Clock
} from 'lucide-react'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'

interface ProfileProps {
  onBack: () => void
  onLogout: () => void
}

export function Profile({ onBack, onLogout }: ProfileProps) {
  const { user, session, signOut, isConfigured } = useSupabaseAuth()
  const [loggingOut, setLoggingOut] = useState(false)

  const handleLogout = async () => {
    setLoggingOut(true)
    await signOut()
    onLogout()
  }

  // Get user metadata
  const email = user?.email || 'Not available'
  const nameFromMeta = user?.user_metadata?.full_name || user?.user_metadata?.name
  const name = typeof nameFromMeta === 'string' ? nameFromMeta : email.split('@')[0]
  const avatarFromMeta = user?.user_metadata?.avatar_url || user?.user_metadata?.picture
  const avatarUrl = typeof avatarFromMeta === 'string' ? avatarFromMeta : undefined
  const providerFromMeta = user?.app_metadata?.provider
  const provider = typeof providerFromMeta === 'string' ? providerFromMeta : 'email'
  const createdAt = user?.created_at ? new Date(user.created_at).toLocaleDateString() : 'Unknown'

  // Get provider icon
  const ProviderIcon = provider === 'github' ? Github : provider === 'google' ? Chrome : Mail

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
                  alt={name}
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
                  {name.charAt(0).toUpperCase()}
                </div>
              )}
              <div>
                <h1
                  className="text-2xl font-bold"
                  style={{ color: 'var(--text)' }}
                >
                  {name}
                </h1>
                <p style={{ color: 'var(--text-2)' }}>{email}</p>
              </div>
            </div>

            {/* Info Grid */}
            <div className="grid gap-4">
              {/* Email */}
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

              {/* Auth Provider */}
              <div
                className="flex items-center gap-3 p-3 rounded-lg"
                style={{ backgroundColor: 'var(--bg)' }}
              >
                <ProviderIcon size={20} style={{ color: 'var(--accent)' }} />
                <div>
                  <p className="text-sm" style={{ color: 'var(--text-2)' }}>Signed in with</p>
                  <p style={{ color: 'var(--text)', textTransform: 'capitalize' }}>{provider}</p>
                </div>
              </div>

              {/* Member Since */}
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

              {/* Role */}
              <div
                className="flex items-center gap-3 p-3 rounded-lg"
                style={{ backgroundColor: 'var(--bg)' }}
              >
                <Shield size={20} style={{ color: 'var(--accent)' }} />
                <div>
                  <p className="text-sm" style={{ color: 'var(--text-2)' }}>Role</p>
                  <p style={{ color: 'var(--text)' }}>Owner</p>
                </div>
              </div>
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

          {/* Not Configured State */}
          {!isConfigured && (
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
                You're using DevOps Copilot without authentication.
                Sign in to save your investigations and access more features.
              </p>
            </div>
          )}
        </motion.div>
      </main>
    </div>
  )
}
