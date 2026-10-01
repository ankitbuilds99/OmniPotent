import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Sparkles, Loader2, CheckCircle, XCircle } from 'lucide-react'
import { setSessionToken } from '../lib/authToken'

/** Pull the session token the backend OAuth callback handed us and store it.
 *
 *  Two shapes: `#token=` (current — fragments stay out of server logs) and
 *  `?token=` (older links still in flight). Either way the token is stripped
 *  from the address bar afterwards so it isn't left in history or shared by a
 *  copy-pasted URL. Returns true when a token was consumed. */
function consumeTokenFromUrl(): boolean {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const query = new URLSearchParams(window.location.search)
  const token = hash.get('token') || query.get('token')
  if (!token) return false

  setSessionToken(token)
  window.history.replaceState({}, '', window.location.pathname)
  return true
}

interface AuthCallbackProps {
  onSuccess: () => void
  onError: () => void
}

export function AuthCallback({ onSuccess, onError }: AuthCallbackProps) {
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('Completing sign in...')

  useEffect(() => {
    const handleCallback = async () => {
      // Firebase provider sign-in uses a popup and completes in the login page.
      // This callback is retained only for the backend OAuth fallback, which
      // redirects here with a session token attached.
      if (consumeTokenFromUrl()) {
        setStatus('success')
        setMessage('Welcome! Redirecting to console...')
        setTimeout(onSuccess, 1000)
        return
      }

      try {
        setStatus('error')
        setMessage('Authentication callback did not include a session token.')
        setTimeout(onError, 2000)
      } catch (err) {
        setStatus('error')
        setMessage(err instanceof Error ? err.message : 'An error occurred')
        setTimeout(onError, 2000)
      }
    }

    handleCallback()
  }, [onSuccess, onError])

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ backgroundColor: 'var(--bg)' }}
    >
      <motion.div
        className="text-center"
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
      >
        {/* Logo */}
        <motion.div
          className="w-16 h-16 rounded-xl flex items-center justify-center mx-auto mb-6"
          style={{
            background: `linear-gradient(135deg, var(--accent), var(--accent-2))`
          }}
          animate={status === 'loading' ? { rotate: 360 } : {}}
          transition={{ duration: 2, repeat: status === 'loading' ? Infinity : 0, ease: 'linear' }}
        >
          <Sparkles className="w-8 h-8" style={{ color: 'var(--bg)' }} />
        </motion.div>

        {/* Status icon */}
        <div className="mb-4">
          {status === 'loading' && (
            <Loader2
              className="w-8 h-8 mx-auto animate-spin"
              style={{ color: 'var(--accent)' }}
            />
          )}
          {status === 'success' && (
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: 'spring', duration: 0.5 }}
            >
              <CheckCircle className="w-8 h-8 mx-auto" style={{ color: 'var(--ok)' }} />
            </motion.div>
          )}
          {status === 'error' && (
            <motion.div
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: 'spring', duration: 0.5 }}
            >
              <XCircle className="w-8 h-8 mx-auto" style={{ color: 'var(--bad)' }} />
            </motion.div>
          )}
        </div>

        {/* Message */}
        <p
          className="text-lg font-medium"
          style={{ color: 'var(--text)' }}
        >
          {message}
        </p>
      </motion.div>
    </div>
  )
}
