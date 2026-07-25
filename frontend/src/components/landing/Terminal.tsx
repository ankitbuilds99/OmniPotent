import { useEffect, useState, useRef } from 'react'
import { motion, useInView } from 'framer-motion'
import { Check } from 'lucide-react'

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
  const containerRef = useRef<HTMLDivElement>(null)
  const isInView = useInView(containerRef, { once: true, amount: 0.5 })
  const hasStarted = useRef(false)

  useEffect(() => {
    if (isInView && !hasStarted.current) {
      hasStarted.current = true
      TERMINAL_LINES.forEach((line, index) => {
        setTimeout(() => {
          setVisibleLines((prev) => [...prev, index])
        }, line.delay)
      })
    }
  }, [isInView])

  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-b from-background via-background-secondary to-background" />

      <div className="container-wide relative z-10">
        <motion.div
          ref={containerRef}
          className="max-w-4xl mx-auto"
          initial={{ opacity: 0, y: 60 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.3 }}
          transition={{ duration: 0.8 }}
        >
          {/* Section header */}
          <div className="text-center mb-12">
            <motion.span
              className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5 }}
            >
              Live Terminal
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
              Watch AI orchestrate your entire deployment pipeline in real-time
            </motion.p>
          </div>

          {/* Terminal window */}
          <div className="terminal shadow-2xl shadow-primary/10">
            {/* Terminal header */}
            <div className="terminal-header">
              <div className="terminal-dot red" />
              <div className="terminal-dot yellow" />
              <div className="terminal-dot green" />
              <span className="ml-4 text-white/40 text-sm font-mono">devops-copilot ~ production</span>
            </div>

            {/* Terminal body */}
            <div className="terminal-body min-h-[300px]">
              {TERMINAL_LINES.map((line, index) => (
                <motion.div
                  key={index}
                  className="terminal-line"
                  initial={{ opacity: 0, x: -20 }}
                  animate={visibleLines.includes(index) ? { opacity: 1, x: 0 } : {}}
                  transition={{ duration: 0.3 }}
                >
                  {line.type === 'command' ? (
                    <>
                      <span className="terminal-prompt">$</span>
                      <span className="text-white font-semibold">
                        {visibleLines.includes(index) && (
                          <TypingText text="deploy production" />
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

              {/* Blinking cursor at end */}
              {visibleLines.length === TERMINAL_LINES.length && (
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

          {/* Decorative elements */}
          <div className="absolute -left-20 top-1/2 w-40 h-40 bg-primary/10 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute -right-20 top-1/3 w-60 h-60 bg-secondary/10 rounded-full blur-3xl pointer-events-none" />
        </motion.div>
      </div>
    </section>
  )
}
