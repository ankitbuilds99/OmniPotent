import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Sparkles, Loader2, CheckCircle, XCircle } from 'lucide-react'
import { initSupabase, checkSupabaseConfigured } from '../lib/supabase'

interface AuthCallbackProps {
  onSuccess: () => void
  onError: () => void
}

export function AuthCallback({ onSuccess, onError }: AuthCallbackProps) {
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('Completing sign in...')

  useEffect(() => {
    const handleCallback = async () => {
      const configured = await checkSupabaseConfigured()
      if (!configured) {
        setStatus('error')
        setMessage('Supabase is not configured')
        setTimeout(onError, 2000)
        return
      }

      try {
        const supabase = await initSupabase()
        if (!supabase) {
          setStatus('error')
          setMessage('Failed to initialize Supabase')
          setTimeout(onError, 2000)
          return
        }

        const { data, error } = await supabase.auth.getSession()

        if (error) {
          setStatus('error')
          setMessage(error.message)
          setTimeout(onError, 2000)
          return
        }

        if (data.session) {
          setStatus('success')
          setMessage('Welcome! Redirecting to console...')
          setTimeout(onSuccess, 1500)
        } else {
          setStatus('error')
          setMessage('Authentication failed. Please try again.')
          setTimeout(onError, 2000)
        }
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
