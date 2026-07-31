import { useState } from 'react'
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
  CheckCircle
} from 'lucide-react'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'

type AuthMode = 'login' | 'signup' | 'forgot'

interface LoginProps {
  onBack: () => void
  onSuccess: () => void
}

export function Login({ onBack, onSuccess }: LoginProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [localLoading, setLocalLoading] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const {
    signInWithGitHub,
    signInWithGoogle,
    signInWithEmail,
    signUp,
    loading: authLoading,
    error: authError,
    isConfigured,
    user
  } = useSupabaseAuth()

  const loading = localLoading || authLoading
  const error = localError || (authError?.message ?? null)

  // Redirect if user is already logged in
  if (user) {
    onSuccess()
    return null
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
      if (mode === 'login') {
        await signInWithEmail(email, password)
        onSuccess()
      } else if (mode === 'signup') {
        await signUp(email, password)
        setSuccess('Check your email to confirm your account!')
        setMode('login')
      } else if (mode === 'forgot') {
        setSuccess('Password reset link sent to your email!')
        setMode('login')
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Authentication failed')
    } finally {
      setLocalLoading(false)
    }
  }

  const handleOAuthLogin = async (provider: 'github' | 'google') => {
    setLocalError(null)
    if (provider === 'github') {
      await signInWithGitHub()
    } else {
      await signInWithGoogle()
    }
  }

  // Demo mode - skip login when Supabase isn't configured
  const handleDemoLogin = () => {
    onSuccess()
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
              {mode === 'login' && 'Sign in to DevOps Copilot'}
              {mode === 'signup' && 'Start your free trial'}
              {mode === 'forgot' && 'We\'ll send you a reset link'}
            </p>
          </div>

          {/* Demo mode notice */}
          {!isConfigured && (
            <div
              className="mb-6 p-4 rounded-lg border text-sm"
              style={{
                backgroundColor: 'var(--shell)',
                borderColor: 'var(--accent)',
                color: 'var(--text-dim)'
              }}
            >
              <p className="flex items-center gap-2 mb-2">
                <AlertCircle className="w-4 h-4" style={{ color: 'var(--accent)' }} />
                <span className="font-medium text-white">Demo Mode</span>
              </p>
              <p>Supabase auth is not configured. Click below to continue without authentication.</p>
              <button
                onClick={handleDemoLogin}
                className="mt-3 w-full py-2 rounded-lg font-medium transition-all"
                style={{
                  backgroundColor: 'var(--accent)',
                  color: 'var(--bg)'
                }}
              >
                Continue to Console
              </button>
            </div>
          )}

          {/* OAuth buttons */}
          {isConfigured && (
            <>
              <div className="space-y-3 mb-6">
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
              </div>

              {/* Divider */}
              <div className="flex items-center gap-4 mb-6">
                <div className="flex-1 h-px" style={{ backgroundColor: 'var(--border)' }} />
                <span className="text-sm" style={{ color: 'var(--text-dim)' }}>or</span>
                <div className="flex-1 h-px" style={{ backgroundColor: 'var(--border)' }} />
              </div>

              {/* Email form */}
              <form onSubmit={handleEmailAuth} className="space-y-4">
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
                      className="w-full pl-11 pr-4 py-3 rounded-xl border outline-none transition-all"
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
                          className="w-full pl-11 pr-12 py-3 rounded-xl border outline-none transition-all"
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
                  {loading ? (
                    <Loader2 className="w-5 h-5 animate-spin" />
                  ) : (
                    <>
                      {mode === 'login' && 'Sign in'}
                      {mode === 'signup' && 'Create account'}
                      {mode === 'forgot' && 'Send reset link'}
                    </>
                  )}
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
            </>
          )}

          {/* Terms */}
          <p className="mt-6 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
            By continuing, you agree to our{' '}
            <a href="#" className="hover:underline" style={{ color: 'var(--accent)' }}>Terms of Service</a>
            {' '}and{' '}
            <a href="#" className="hover:underline" style={{ color: 'var(--accent)' }}>Privacy Policy</a>
          </p>
        </div>
      </motion.div>
    </div>
  )
}
