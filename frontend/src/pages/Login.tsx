import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Sparkles,
  Mail,
  Lock,
  Eye,
  EyeOff,
  Github,
  ArrowLeft,
  Loader2,
  AlertCircle,
  CheckCircle,
  User
} from 'lucide-react'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'
import { fetchSupabaseProviders } from '../lib/supabase'
import { getAuthToken, setSessionToken } from '../lib/authToken'

type AuthMode = 'login' | 'signup' | 'forgot'

interface LoginProps {
  onBack: () => void
  onSuccess: () => void
}

interface AuthProvider {
  id: string
  name: string
  icon: string
}

const API_BASE = import.meta.env.VITE_API_URL || ''

export function Login({ onBack, onSuccess }: LoginProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [localLoading, setLocalLoading] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [backendProviders, setBackendProviders] = useState<AuthProvider[]>([])
  const [checkingProviders, setCheckingProviders] = useState(true)
  // null = not known yet, or the probe failed. See fetchSupabaseProviders.
  const [supabaseProviders, setSupabaseProviders] = useState<Set<string> | null>(null)

  const {
    signInWithGitHub,
    signInWithGoogle,
    signInWithEmail,
    signUp,
    resetPassword,
    loading: authLoading,
    error: authError,
    isConfigured: supabaseConfigured,
    user
  } = useSupabaseAuth()

  const loading = localLoading || authLoading
  const error = localError || (authError?.message ?? null)

  // Check for backend OAuth providers
  useEffect(() => {
    const checkProviders = async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/providers`)
        if (res.ok) {
          const providers = await res.json()
          setBackendProviders(providers)
        }
      } catch {
        // Backend auth not available
      } finally {
        setCheckingProviders(false)
      }
    }
    checkProviders()
  }, [])

  // Which social logins the Supabase project will actually accept.
  useEffect(() => {
    if (!supabaseConfigured) return
    let mounted = true
    fetchSupabaseProviders()
      .then((names) => {
        if (mounted) setSupabaseProviders(names)
      })
      .catch(() => {
        // Unknown, which leaves every button on offer.
      })
    return () => {
      mounted = false
    }
  }, [supabaseConfigured])

  // Check for token in URL (from OAuth callback)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const token = params.get('token')
    if (token) {
      setSessionToken(token)
      window.history.replaceState({}, '', window.location.pathname)
      onSuccess()
    }
  }, [onSuccess])

  // Leaving this page is a side effect, so it belongs in an effect — the old
  // `if (user) { onSuccess(); return null }` updated the router from inside this
  // component's render.
  //
  // It also waits for the auth bootstrap to finish. A stored token is not proof
  // of a usable session: until the hook has tried to restore it, redirecting on
  // one can land the user in a console that cannot authenticate, with no way
  // back to the form.
  useEffect(() => {
    if (authLoading) return
    if (user || getAuthToken()) onSuccess()
  }, [authLoading, user, onSuccess])

  const handleBackendOAuth = (provider: string) => {
    const redirectUri = encodeURIComponent(window.location.origin + '/auth/callback')
    window.location.href = `${API_BASE}/auth/${provider}/login?redirect_uri=${redirectUri}`
  }

  // Offer a provider only where something can actually complete it. Supabase
  // being configured says nothing about which social logins the project has
  // switched on, and a button for one that is off just returns "Unsupported
  // provider". While the answer is unknown, show it — a slow probe should not
  // take away a working button.
  const viaSupabase = (id: string) =>
    supabaseConfigured && (supabaseProviders === null || supabaseProviders.has(id))

  const handleOAuthLogin = async (provider: 'github' | 'google') => {
    setLocalError(null)

    // Try Supabase first, but only where the project accepts this provider —
    // otherwise fall through to a backend that might.
    if (viaSupabase(provider)) {
      try {
        await (provider === 'github' ? signInWithGitHub() : signInWithGoogle())
      } catch (err) {
        // Without this the button just went quiet: the redirect never happened
        // and the page gave no sign that anything had gone wrong.
        setLocalError(err instanceof Error ? err.message : `Could not start ${provider} sign-in.`)
      }
      return
    }

    // Fall back to backend OAuth
    const backendProvider = backendProviders.find(p => p.id === provider)
    if (backendProvider) {
      handleBackendOAuth(provider)
      return
    }

    setLocalError(`${provider} login is not configured`)
  }

  const handleEmailAuth = async (e: React.FormEvent) => {
    e.preventDefault()
    setLocalError(null)
    setSuccess(null)

    if (!email.trim()) {
      setLocalError('Please enter your email')
      return
    }

    if (mode !== 'forgot' && !password) {
      setLocalError('Please enter your password')
      return
    }

    if (mode === 'signup' && password.length < 6) {
      setLocalError('Password must be at least 6 characters')
      return
    }

    setLocalLoading(true)

    try {
      if (supabaseConfigured) {
        // Use Supabase auth. Each call below throws when the user was not signed
        // in, which is what keeps a rejected password from reaching onSuccess().
        if (mode === 'login') {
          await signInWithEmail(email, password)
          onSuccess()
        } else if (mode === 'signup') {
          await signUp(email, password)
          // A project with email confirmation switched off signs the account in
          // immediately; telling that user to go and check their mail would
          // strand them on the login form.
          if (getAuthToken()) {
            onSuccess()
          } else {
            setSuccess('Check your email to confirm your account!')
            setMode('login')
          }
        } else if (mode === 'forgot') {
          // This used to print "link sent" without sending anything, so a
          // locked-out user waited for mail that was never requested — and
          // could repeat that forever without regaining access.
          await resetPassword(email)
          setSuccess('Password reset link sent to your email!')
          setMode('login')
        }
      } else {
        // For demo/dev mode without auth, just proceed
        if (mode === 'login' || mode === 'signup') {
          // Store a simple session
          localStorage.setItem('demo_user', JSON.stringify({ email, name: name || email }))
          onSuccess()
        } else if (mode === 'forgot') {
          // No password store and no reset endpoint on this deployment, so say
          // what the user can actually do instead of implying mail is coming.
          setLocalError(
            backendProviders.length > 0
              ? `There is no password to reset — this deployment signs you in with ${backendProviders
                  .map((p) => p.name)
                  .join(' or ')}.`
              : 'Password reset is not available on this deployment. Use "Try Demo" to continue without an account.'
          )
        }
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Authentication failed')
    } finally {
      setLocalLoading(false)
    }
  }

  const handleDemoLogin = () => {
    localStorage.setItem('demo_user', JSON.stringify({ email: 'demo@omnipotent.com', name: 'Demo User' }))
    onSuccess()
  }

  const hasGitHub = viaSupabase('github') || backendProviders.some(p => p.id === 'github')
  const hasGoogle = viaSupabase('google') || backendProviders.some(p => p.id === 'google')

  if (checkingProviders) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: 'var(--bg)' }}>
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--accent)' }} />
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 relative overflow-hidden" style={{ backgroundColor: 'var(--bg)' }}>
      {/* Background effects */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className="absolute top-1/4 left-1/4 w-96 h-96 rounded-full blur-3xl opacity-20"
          style={{ backgroundColor: 'var(--accent)' }}
        />
        <div
          className="absolute bottom-1/4 right-1/4 w-96 h-96 rounded-full blur-3xl opacity-20"
          style={{ backgroundColor: 'var(--accent-2)' }}
        />
      </div>

      {/* Back button */}
      <motion.button
        className="absolute top-6 left-6 flex items-center gap-2 px-4 py-2 rounded-lg text-white/70 hover:text-white transition-colors"
        style={{ backgroundColor: 'var(--panel)' }}
        onClick={onBack}
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
      >
        <ArrowLeft className="w-4 h-4" />
        Back
      </motion.button>

      {/* Login card */}
      <motion.div
        className="w-full max-w-md relative z-10"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
      >
        <div
          className="rounded-2xl p-8 border backdrop-blur-xl"
          style={{
            backgroundColor: 'var(--panel)',
            borderColor: 'var(--border)'
          }}
        >
          {/* Logo */}
          <div className="flex flex-col items-center mb-8">
            <motion.div
              className="w-14 h-14 rounded-xl flex items-center justify-center mb-4"
              style={{
                background: `linear-gradient(135deg, var(--accent), var(--accent-2))`
              }}
              whileHover={{ rotate: 180, scale: 1.1 }}
              transition={{ duration: 0.5 }}
            >
              <Sparkles className="w-7 h-7" style={{ color: 'var(--bg)' }} />
            </motion.div>
            <h1 className="text-2xl font-bold text-white">
              {mode === 'login' && 'Welcome back'}
              {mode === 'signup' && 'Create account'}
              {mode === 'forgot' && 'Reset password'}
            </h1>
            <p className="text-sm mt-2" style={{ color: 'var(--text-dim)' }}>
              {mode === 'login' && 'Sign in to OmniPotent'}
              {mode === 'signup' && 'Start your free trial'}
              {mode === 'forgot' && 'We\'ll send you a reset link'}
            </p>
          </div>

          {/* OAuth buttons - always show if available */}
          {(hasGitHub || hasGoogle) && (
            <>
              <div className="space-y-3 mb-6">
                {hasGitHub && (
                  <motion.button
                    className="w-full flex items-center justify-center gap-3 py-3 px-4 rounded-xl border font-medium transition-all"
                    style={{
                      backgroundColor: 'var(--shell)',
                      borderColor: 'var(--border)',
                      color: 'var(--text)'
                    }}
                    onClick={() => handleOAuthLogin('github')}
                    disabled={loading}
                    whileHover={{ scale: 1.01 }}
                    whileTap={{ scale: 0.99 }}
                  >
                    <Github className="w-5 h-5" />
                    Continue with GitHub
                  </motion.button>
                )}

                {hasGoogle && (
                  <motion.button
                    className="w-full flex items-center justify-center gap-3 py-3 px-4 rounded-xl border font-medium transition-all"
                    style={{
                      backgroundColor: 'var(--shell)',
                      borderColor: 'var(--border)',
                      color: 'var(--text)'
                    }}
                    onClick={() => handleOAuthLogin('google')}
                    disabled={loading}
                    whileHover={{ scale: 1.01 }}
                    whileTap={{ scale: 0.99 }}
                  >
                    <svg className="w-5 h-5" viewBox="0 0 24 24">
                      <path
                        fill="currentColor"
                        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                      />
                      <path
                        fill="currentColor"
                        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                      />
                      <path
                        fill="currentColor"
                        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                      />
                      <path
                        fill="currentColor"
                        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                      />
                    </svg>
                    Continue with Google
                  </motion.button>
                )}
              </div>

              {/* Divider */}
              <div className="flex items-center gap-4 mb-6">
                <div className="flex-1 h-px" style={{ backgroundColor: 'var(--border)' }} />
                <span className="text-sm" style={{ color: 'var(--text-dim)' }}>or</span>
                <div className="flex-1 h-px" style={{ backgroundColor: 'var(--border)' }} />
              </div>
            </>
          )}

          {/* Email form */}
          <form onSubmit={handleEmailAuth} className="space-y-4">
            {mode === 'signup' && (
              <div>
                <label className="block text-sm font-medium mb-2" style={{ color: 'var(--text-dim)' }}>
                  Name
                </label>
                <div className="relative">
                  <User
                    className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5"
                    style={{ color: 'var(--text-dim)' }}
                  />
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full pl-11 pr-4 py-3 rounded-xl border outline-none transition-all focus:ring-2"
                    style={{
                      backgroundColor: 'var(--shell)',
                      borderColor: 'var(--border)',
                      color: 'var(--text)'
                    }}
                    placeholder="Your name"
                    disabled={loading}
                  />
                </div>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium mb-2" style={{ color: 'var(--text-dim)' }}>
                Email
              </label>
              <div className="relative">
                <Mail
                  className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5"
                  style={{ color: 'var(--text-dim)' }}
                />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full pl-11 pr-4 py-3 rounded-xl border outline-none transition-all focus:ring-2"
                  style={{
                    backgroundColor: 'var(--shell)',
                    borderColor: 'var(--border)',
                    color: 'var(--text)'
                  }}
                  placeholder="you@company.com"
                  disabled={loading}
                />
              </div>
            </div>

            <AnimatePresence mode="wait">
              {mode !== 'forgot' && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                >
                  <label className="block text-sm font-medium mb-2" style={{ color: 'var(--text-dim)' }}>
                    Password
                  </label>
                  <div className="relative">
                    <Lock
                      className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5"
                      style={{ color: 'var(--text-dim)' }}
                    />
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full pl-11 pr-12 py-3 rounded-xl border outline-none transition-all focus:ring-2"
                      style={{
                        backgroundColor: 'var(--shell)',
                        borderColor: 'var(--border)',
                        color: 'var(--text)'
                      }}
                      placeholder="••••••••"
                      disabled={loading}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 p-1"
                      style={{ color: 'var(--text-dim)' }}
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Error message */}
            <AnimatePresence>
              {error && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="flex items-center gap-2 p-3 rounded-lg text-sm"
                  style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#ef4444' }}
                >
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  {error}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Success message */}
            <AnimatePresence>
              {success && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -10 }}
                  className="flex items-center gap-2 p-3 rounded-lg text-sm"
                  style={{ backgroundColor: 'rgba(34, 197, 94, 0.1)', color: '#22c55e' }}
                >
                  <CheckCircle className="w-4 h-4 flex-shrink-0" />
                  {success}
                </motion.div>
              )}
            </AnimatePresence>

            {/* Forgot password link */}
            {mode === 'login' && (
              <button
                type="button"
                onClick={() => { setMode('forgot'); setLocalError(null); setSuccess(null); }}
                className="text-sm hover:underline"
                style={{ color: 'var(--accent)' }}
              >
                Forgot password?
              </button>
            )}

            {/* Submit button */}
            <motion.button
              type="submit"
              className="w-full py-3 rounded-xl font-semibold flex items-center justify-center gap-2 transition-all"
              style={{
                background: `linear-gradient(135deg, var(--accent), var(--accent-2))`,
                color: 'var(--bg)'
              }}
              disabled={loading}
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.99 }}
            >
              {/* Keep the label while busy. A bare spinner cannot say whether it
                  is checking a saved session, signing in, or stuck. */}
              {loading && <Loader2 className="w-5 h-5 animate-spin" />}
              {mode === 'login' && (loading ? 'Signing in…' : 'Sign in')}
              {mode === 'signup' && (loading ? 'Creating account…' : 'Create account')}
              {mode === 'forgot' && (loading ? 'Sending…' : 'Send reset link')}
            </motion.button>
          </form>

          {/* Switch mode */}
          <div className="mt-6 text-center text-sm" style={{ color: 'var(--text-dim)' }}>
            {mode === 'login' ? (
              <>
                Don't have an account?{' '}
                <button
                  onClick={() => { setMode('signup'); setLocalError(null); setSuccess(null); }}
                  className="font-medium hover:underline"
                  style={{ color: 'var(--accent)' }}
                >
                  Sign up
                </button>
              </>
            ) : (
              <>
                Already have an account?{' '}
                <button
                  onClick={() => { setMode('login'); setLocalError(null); setSuccess(null); }}
                  className="font-medium hover:underline"
                  style={{ color: 'var(--accent)' }}
                >
                  Sign in
                </button>
              </>
            )}
          </div>

          {/* Demo mode - always available for solo users / quick access */}
          <div className="mt-4 pt-4 border-t" style={{ borderColor: 'var(--border)' }}>
            <p className="text-center text-xs mb-3" style={{ color: 'var(--text-dim)' }}>
              No account needed - try it instantly
            </p>
            <motion.button
              onClick={handleDemoLogin}
              className="w-full py-3 rounded-xl font-medium transition-all flex items-center justify-center gap-2"
              style={{
                backgroundColor: 'var(--shell)',
                border: '1px solid var(--border)',
                color: 'var(--text)'
              }}
              whileHover={{ scale: 1.01, borderColor: 'var(--accent)' }}
              whileTap={{ scale: 0.99 }}
            >
              <Sparkles className="w-4 h-4" style={{ color: 'var(--accent)' }} />
              Try Demo (No Login Required)
            </motion.button>
            <p className="text-center text-xs mt-2" style={{ color: 'var(--text-dim)' }}>
              Full access to all features with sample data
            </p>
          </div>

          {/* A consent sentence used to sit here, linking "Terms of Service"
              and "Privacy Policy" to href="#". Neither document exists, so the
              sentence asked users to agree to nothing and the links went
              nowhere. Removed until the documents are real. */}
        </div>
      </motion.div>
    </div>
  )
}
