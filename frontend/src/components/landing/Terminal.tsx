import { useEffect, useState, useRef, useCallback } from 'react'
import { motion, useInView } from 'framer-motion'
import { Check, RotateCcw } from 'lucide-react'

// A fixed script, typed out on a timer. Nothing here runs, connects or reports —
// which is fine for a product illustration, and was not what the section claimed:
// it was headed "Live Terminal" over "…in real-time". Same fabrication the
// Metrics and Dashboard sections were carrying, one section along.
const TERMINAL_LINES = [
  { type: 'command', text: '$ deploy production', delay: 0 },
  { type: 'progress', text: '✓ Building Docker image...', delay: 500, status: 'success' },
  { type: 'progress', text: '✓ Running Tests...', delay: 1200, status: 'success' },
  { type: 'progress', text: '✓ Creating Terraform Plan...', delay: 1900, status: 'success' },
  { type: 'progress', text: '✓ Provisioning AWS...', delay: 2800, status: 'success' },
  { type: 'progress', text: '✓ Deploying Kubernetes...', delay: 3700, status: 'success' },
  { type: 'progress', text: '✓ Configuring Monitoring...', delay: 4400, status: 'success' },
  { type: 'result', text: '✓ Production Live', delay: 5200, status: 'success' },
]

function TypingText({ text, onComplete }: { text: string; onComplete?: () => void }) {
  const [displayText, setDisplayText] = useState('')
  const [isComplete, setIsComplete] = useState(false)

  useEffect(() => {
    setDisplayText('')
    setIsComplete(false)
    let index = 0
    const interval = setInterval(() => {
      if (index < text.length) {
        setDisplayText(text.slice(0, index + 1))
        index++
      } else {
        clearInterval(interval)
        setIsComplete(true)
        onComplete?.()
      }
    }, 30)

    return () => clearInterval(interval)
  }, [text, onComplete])

  return (
    <span>
      {displayText}
      {!isComplete && <span className="terminal-cursor" />}
    </span>
  )
}

export function Terminal() {
  const [visibleLines, setVisibleLines] = useState<number[]>([])
  const [runCount, setRunCount] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const isInView = useInView(containerRef, { once: false, amount: 0.5 })
  const hasStarted = useRef(false)
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([])

  const clearTimers = useCallback(() => {
    timersRef.current.forEach(t => clearTimeout(t))
    timersRef.current = []
  }, [])

  const startAnimation = useCallback(() => {
    clearTimers()
    setVisibleLines([])

    TERMINAL_LINES.forEach((line, index) => {
      const timer = setTimeout(() => {
        setVisibleLines((prev) => [...prev, index])
      }, line.delay)
      timersRef.current.push(timer)
    })

    // Auto-restart after completion
    const restartTimer = setTimeout(() => {
      setRunCount(c => c + 1)
    }, 8000)
    timersRef.current.push(restartTimer)
  }, [clearTimers])

  const handleRestart = useCallback(() => {
    setRunCount(c => c + 1)
  }, [])

  // Start on first view
  useEffect(() => {
    if (isInView && !hasStarted.current) {
      hasStarted.current = true
      startAnimation()
    }
  }, [isInView, startAnimation])

  // Restart when runCount changes
  useEffect(() => {
    if (runCount > 0) {
      startAnimation()
    }
  }, [runCount, startAnimation])

  // Cleanup
  useEffect(() => {
    return () => clearTimers()
  }, [clearTimers])

  const isComplete = visibleLines.length === TERMINAL_LINES.length

  return (
    <section className="relative py-32 overflow-hidden omni-landing__section omni-landing__terminal">
      <div className="absolute inset-0 omni-landing__terminal-canvas" />

      <div className="container-wide relative z-10">
        <motion.div
          ref={containerRef}
          className="max-w-4xl mx-auto"
          initial={{ opacity: 0, y: 60 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.8 }}
        >
          <div className="text-center mb-12">
            <motion.span
              className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5 }}
            >
              Deploy walkthrough
            </motion.span>
            <motion.h2
              className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: 0.1 }}
            >
              One command to <span className="gradient-text">production</span>
            </motion.h2>
            <motion.p
              className="text-white/60 text-lg max-w-2xl mx-auto"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: 0.2 }}
            >
              The deploy sequence the agent drives, start to finish
            </motion.p>
          </div>

          <div className="terminal shadow-2xl shadow-primary/10">
            <div className="terminal-header">
              <div className="terminal-dot red" />
              <div className="terminal-dot yellow" />
              <div className="terminal-dot green" />
              <span className="ml-4 text-white/40 text-sm font-mono flex-1">omnipotent ~ production</span>
              {isComplete && (
                <button
                  onClick={handleRestart}
                  className="flex items-center gap-1 text-xs text-primary/60 hover:text-primary transition-colors cursor-pointer mr-2"
                  title="Replay"
                >
                  <RotateCcw className="w-3 h-3" />
                  Replay
                </button>
              )}
            </div>

            <div className="terminal-body min-h-[300px]">
              {TERMINAL_LINES.map((line, index) => (
                <motion.div
                  key={`${runCount}-${index}`}
                  className="terminal-line"
                  initial={{ opacity: 0, x: -20 }}
                  animate={visibleLines.includes(index) ? { opacity: 1, x: 0 } : { opacity: 0, x: -20 }}
                  transition={{ duration: 0.3 }}
                >
                  {line.type === 'command' ? (
                    <>
                      <span className="terminal-prompt">$</span>
                      <span className="text-white font-semibold">
                        {visibleLines.includes(index) && (
                          <TypingText key={runCount} text="deploy production" />
                        )}
                      </span>
                    </>
                  ) : (
                    <span
                      className={`flex items-center gap-2 ${
                        line.status === 'success' ? 'text-success' : 'terminal-text'
                      }`}
                    >
                      {line.type === 'result' ? (
                        <motion.span
                          className="flex items-center gap-2 text-lg font-semibold"
                          initial={{ scale: 0.8 }}
                          animate={{ scale: 1 }}
                          transition={{ type: 'spring', stiffness: 300 }}
                        >
                          <Check className="w-5 h-5" />
                          Production Live
                        </motion.span>
                      ) : (
                        line.text
                      )}
                    </span>
                  )}
                </motion.div>
              ))}

              {isComplete && (
                <motion.div
                  className="terminal-line mt-4"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.5 }}
                >
                  <span className="terminal-prompt">$</span>
                  <span className="terminal-cursor" />
                </motion.div>
              )}
            </div>
          </div>

          <div className="absolute -left-20 top-1/2 w-40 h-40 bg-primary/10 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute -right-20 top-1/3 w-60 h-60 bg-secondary/10 rounded-full blur-3xl pointer-events-none" />
        </motion.div>
      </div>
    </section>
  )
}
