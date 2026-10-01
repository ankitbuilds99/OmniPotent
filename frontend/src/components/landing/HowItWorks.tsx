import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  MessageSquare,
  Search,
  FileText,
  CheckCircle,
  XCircle,
  GitPullRequest,
  Play,
  Pause,
  RotateCcw,
  ChevronRight,
  Sparkles,
  AlertTriangle,
  Database,
  Server,
  GitCommit
} from 'lucide-react'

const STEPS = [
  {
    id: 'ask',
    title: 'Ask a Question',
    description: 'Type your question in natural language',
    icon: MessageSquare,
    color: '#00E7FF',
    demo: {
      type: 'input',
      content: 'Why is the checkout API throwing 500 errors?'
    }
  },
  {
    id: 'investigate',
    title: 'AI Investigates',
    description: 'Copilot searches logs, metrics, traces, and code',
    icon: Search,
    color: '#7C3AED',
    demo: {
      type: 'trace',
      steps: [
        { icon: Database, text: 'Searching Datadog logs...', status: 'done' },
        { icon: AlertTriangle, text: 'Found 847 errors in last 30min', status: 'done' },
        { icon: Server, text: 'Checking Kubernetes pods...', status: 'done' },
        { icon: GitCommit, text: 'Analyzing recent deploys...', status: 'done' },
        { icon: FileText, text: 'Reading checkout.js diff...', status: 'done' },
      ]
    }
  },
  {
    id: 'analyze',
    title: 'Root Cause Found',
    description: 'AI identifies the exact issue with evidence',
    icon: FileText,
    color: '#00FFB3',
    demo: {
      type: 'rca',
      content: {
        issue: 'Null pointer in discount calculation',
        commit: 'a1b2c3d by @developer',
        file: 'checkout.js:42',
        confidence: 94
      }
    }
  },
  {
    id: 'approve',
    title: 'You Approve',
    description: 'AI pauses before any write action',
    icon: CheckCircle,
    color: '#FF9900',
    demo: {
      type: 'approval',
      action: 'Create Pull Request',
      details: {
        title: 'fix: handle null coupon code',
        branch: 'fix/checkout-null-coupon'
      }
    }
  },
  {
    id: 'done',
    title: 'Fixed!',
    description: 'PR created, incident resolved',
    icon: GitPullRequest,
    color: '#3CF281',
    demo: {
      type: 'success',
      content: {
        pr: '#1234',
        title: 'fix: handle null coupon code in checkout',
        status: 'Ready for review'
      }
    }
  }
]

function StepIndicator({
  steps,
  currentStep,
  onStepClick
}: {
  steps: typeof STEPS
  currentStep: number
  onStepClick: (index: number) => void
}) {
  return (
    <div className="flex items-center justify-center gap-2 mb-12">
      {steps.map((step, index) => {
        const Icon = step.icon
        const isActive = index === currentStep
        const isPast = index < currentStep

        return (
          <button
            key={step.id}
            type="button"
            onClick={() => onStepClick(index)}
            aria-label={`Show step ${index + 1}: ${step.title}`}
            aria-current={isActive ? 'step' : undefined}
            className="flex items-center gap-2 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 focus-visible:ring-offset-2 focus-visible:ring-offset-black group"
          >
            <motion.div
              className={`
                w-10 h-10 rounded-xl flex items-center justify-center transition-all duration-300
                ${isActive
                  ? 'scale-110'
                  : 'hover:scale-105'
                }
              `}
              style={{
                backgroundColor: isActive || isPast ? `${step.color}20` : 'rgba(255,255,255,0.05)',
                borderWidth: 2,
                borderColor: isActive ? step.color : isPast ? `${step.color}50` : 'rgba(255,255,255,0.1)'
              }}
              animate={isActive ? {
                boxShadow: [`0 0 20px ${step.color}30`, `0 0 30px ${step.color}50`, `0 0 20px ${step.color}30`]
              } : {}}
              transition={{ duration: 2, repeat: Infinity }}
            >
              <Icon
                className="w-5 h-5"
                style={{ color: isActive || isPast ? step.color : 'rgba(255,255,255,0.4)' }}
              />
            </motion.div>

            {index < steps.length - 1 && (
              <div className="w-8 h-0.5 bg-white/10 mx-1">
                <motion.div
                  className="h-full"
                  style={{ backgroundColor: step.color }}
                  initial={{ width: '0%' }}
                  animate={{ width: isPast ? '100%' : '0%' }}
                  transition={{ duration: 0.5 }}
                />
              </div>
            )}
          </button>
        )
      })}
    </div>
  )
}

function TypeWriter({ text, onComplete }: { text: string; onComplete?: () => void }) {
  const [displayText, setDisplayText] = useState('')
  const [cursorVisible, setCursorVisible] = useState(true)

  useEffect(() => {
    setDisplayText('')
    let index = 0
    const interval = setInterval(() => {
      if (index < text.length) {
        setDisplayText(text.slice(0, index + 1))
        index++
      } else {
        clearInterval(interval)
        onComplete?.()
      }
    }, 50)

    const cursorInterval = setInterval(() => {
      setCursorVisible(v => !v)
    }, 500)

    return () => {
      clearInterval(interval)
      clearInterval(cursorInterval)
    }
  }, [text])

  return (
    <span>
      {displayText}
      <span className={`${cursorVisible ? 'opacity-100' : 'opacity-0'} text-primary`}>|</span>
    </span>
  )
}

function DemoContent({ step, isActive }: { step: typeof STEPS[0]; isActive: boolean }) {
  const [traceIndex, setTraceIndex] = useState(0)
  const [showApproval, setShowApproval] = useState(false)
  const [approved, setApproved] = useState<boolean | null>(null)

  useEffect(() => {
    if (!isActive) {
      setTraceIndex(0)
      setShowApproval(false)
      setApproved(null)
      return
    }

    if (step.demo.type === 'trace') {
      const steps = step.demo.steps || []
      let index = 0
      const interval = setInterval(() => {
        if (index < steps.length) {
          setTraceIndex(index + 1)
          index++
        } else {
          clearInterval(interval)
        }
      }, 600)
      return () => clearInterval(interval)
    }

    if (step.demo.type === 'approval') {
      const timer = setTimeout(() => setShowApproval(true), 500)
      return () => clearTimeout(timer)
    }
  }, [isActive, step])

  const demo = step.demo

  switch (demo.type) {
    case 'input':
      return (
        <div className="bg-[#0d1117] rounded-xl border border-white/10 p-4">
          <div className="flex items-center gap-3 mb-3">
            <div className="flex gap-1.5">
              <div className="w-3 h-3 rounded-full bg-red-500/80" />
              <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
              <div className="w-3 h-3 rounded-full bg-green-500/80" />
            </div>
            <span className="text-white/40 text-sm">OmniPotent</span>
          </div>
          <div className="flex items-center gap-3 bg-white/5 rounded-lg px-4 py-3 border border-white/10">
            <Sparkles className="w-5 h-5 text-primary" />
            <span className="text-white flex-1">
              {isActive ? <TypeWriter text={demo.content as string} /> : (demo.content as string)}
            </span>
          </div>
        </div>
      )

    case 'trace': {
      const steps = demo.steps || []
      return (
        <div className="bg-[#0d1117] rounded-xl border border-white/10 p-4 font-mono text-sm">
          <div className="flex items-center gap-2 mb-4 text-primary">
            <Search className="w-4 h-4 animate-pulse" />
            <span>Investigating...</span>
          </div>
          <div className="space-y-2">
            {steps.slice(0, traceIndex).map((s, i) => {
              const Icon = s.icon
              return (
                <motion.div
                  key={i}
                  className="flex items-center gap-3 text-white/70"
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                >
                  <Icon className="w-4 h-4 text-white/40" />
                  <span>{s.text}</span>
                  <CheckCircle className="w-4 h-4 text-green-400 ml-auto" />
                </motion.div>
              )
            })}
            {traceIndex < steps.length && (
              <div className="flex items-center gap-3 text-white/40">
                <motion.div
                  className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                />
                <span>{steps[traceIndex]?.text}</span>
              </div>
            )}
          </div>
        </div>
      )
    }

    case 'rca': {
      const rca = demo.content as { issue: string; commit: string; file: string; confidence: number }
      return (
        <motion.div
          className="bg-[#0d1117] rounded-xl border border-green-500/30 p-5"
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
        >
          <div className="flex items-center gap-2 text-green-400 font-semibold mb-4">
            <CheckCircle className="w-5 h-5" />
            Root Cause Identified
          </div>
          <div className="space-y-3 text-sm">
            <div>
              <span className="text-white/40">Issue:</span>
              <span className="text-white ml-2">{rca.issue}</span>
            </div>
            <div>
              <span className="text-white/40">Bad Commit:</span>
              <span className="text-orange-400 ml-2 font-mono">{rca.commit}</span>
            </div>
            <div>
              <span className="text-white/40">Location:</span>
              <span className="text-cyan-400 ml-2 font-mono">{rca.file}</span>
            </div>
            <div className="flex items-center gap-2 pt-2">
              <span className="text-white/40">Confidence:</span>
              <div className="flex-1 h-2 bg-white/10 rounded-full overflow-hidden">
                <motion.div
                  className="h-full bg-green-500 rounded-full"
                  initial={{ width: 0 }}
                  animate={{ width: `${rca.confidence}%` }}
                  transition={{ duration: 1, delay: 0.3 }}
                />
              </div>
              <span className="text-green-400 font-semibold">{rca.confidence}%</span>
            </div>
          </div>
        </motion.div>
      )
    }

    case 'approval': {
      const details = demo.details as { title: string; branch: string }
      return (
        <AnimatePresence>
          {showApproval && (
            <motion.div
              className="bg-[#0d1117] rounded-xl border border-yellow-500/30 p-5"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <div className="flex items-center gap-2 text-yellow-400 font-semibold mb-4">
                <AlertTriangle className="w-5 h-5" />
                Approval Required
              </div>
              <div className="bg-white/5 rounded-lg p-4 mb-4">
                <div className="text-white font-medium mb-2">{demo.action}</div>
                <div className="text-sm space-y-1 text-white/60 font-mono">
                  <div>title: "{details.title}"</div>
                  <div>branch: "{details.branch}"</div>
                </div>
              </div>
              {approved === null ? (
                <div className="flex gap-3">
                  <motion.button
                    className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg bg-green-500/20 text-green-400 border border-green-500/30 hover:bg-green-500/30 transition-colors"
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={() => setApproved(true)}
                  >
                    <CheckCircle className="w-4 h-4" />
                    Approve
                  </motion.button>
                  <motion.button
                    className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 transition-colors"
                    whileHover={{ scale: 1.02 }}
                    whileTap={{ scale: 0.98 }}
                    onClick={() => setApproved(false)}
                  >
                    <XCircle className="w-4 h-4" />
                    Reject
                  </motion.button>
                </div>
              ) : (
                <motion.div
                  className={`flex items-center justify-center gap-2 py-2.5 rounded-lg ${
                    approved ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                  }`}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                >
                  {approved ? (
                    <>
                      <CheckCircle className="w-4 h-4" />
                      Approved! Creating PR...
                    </>
                  ) : (
                    <>
                      <XCircle className="w-4 h-4" />
                      Rejected. No action taken.
                    </>
                  )}
                </motion.div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      )
    }

    case 'success': {
      const pr = demo.content as { pr: string; title: string; status: string }
      return (
        <motion.div
          className="bg-[#0d1117] rounded-xl border border-green-500/30 p-5"
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
        >
          <div className="flex items-center justify-center gap-3 mb-4">
            <motion.div
              className="w-16 h-16 rounded-full bg-green-500/20 flex items-center justify-center"
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ type: 'spring', stiffness: 200 }}
            >
              <GitPullRequest className="w-8 h-8 text-green-400" />
            </motion.div>
          </div>
          <div className="text-center">
            <div className="text-green-400 font-semibold text-lg mb-1">PR {pr.pr} Created!</div>
            <div className="text-white/70 text-sm mb-3">{pr.title}</div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-green-500/20 text-green-400 text-sm">
              <CheckCircle className="w-3 h-3" />
              {pr.status}
            </div>
          </div>
        </motion.div>
      )
    }

    default:
      return null
  }
}

export function HowItWorks({ onLaunch }: { onLaunch?: () => void }) {
  const [currentStep, setCurrentStep] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [hasStarted, setHasStarted] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasStarted) {
          setHasStarted(true)
          setIsPlaying(true)
        }
      },
      { threshold: 0.3 }
    )

    if (containerRef.current) {
      observer.observe(containerRef.current)
    }

    return () => observer.disconnect()
  }, [hasStarted])

  useEffect(() => {
    if (isPlaying) {
      intervalRef.current = setInterval(() => {
        setCurrentStep(prev => {
          if (prev >= STEPS.length - 1) {
            setIsPlaying(false)
            return prev
          }
          return prev + 1
        })
      }, 3000)
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
      }
    }
  }, [isPlaying])

  const handlePlayPause = () => {
    if (currentStep >= STEPS.length - 1) {
      setCurrentStep(0)
      setIsPlaying(true)
    } else {
      setIsPlaying(!isPlaying)
    }
  }

  const handleReset = () => {
    setCurrentStep(0)
    setIsPlaying(true)
  }

  const handleStepClick = (index: number) => {
    setCurrentStep(index)
    setIsPlaying(false)
  }

  const step = STEPS[currentStep]

  return (
    <section ref={containerRef} className="relative py-32 overflow-hidden omni-landing__section omni-landing__how-it-works">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#030508] to-background" />
      <div className="container-wide relative z-10">
        {/* Header */}
        <div className="text-center mb-12">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            How It Works
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
          >
            From question to <span className="gradient-text">resolution</span>
          </motion.h2>
          <motion.p
            className="text-white/75 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.2 }}
          >
            Watch how OmniPotent investigates and resolves incidents
          </motion.p>
        </div>

        {/* Step indicator */}
        <StepIndicator
          steps={STEPS}
          currentStep={currentStep}
          onStepClick={handleStepClick}
        />

        {/* Main content */}
        <div className="max-w-4xl mx-auto">
          <div className="grid md:grid-cols-2 gap-8 items-center">
            {/* Step info */}
            <motion.div
              key={step.id}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3 }}
            >
              <div
                className="w-16 h-16 rounded-2xl flex items-center justify-center mb-6"
                style={{ backgroundColor: `${step.color}20` }}
              >
                <step.icon className="w-8 h-8" style={{ color: step.color }} />
              </div>
              <div className="text-sm font-medium mb-2" style={{ color: step.color }}>
                Step {currentStep + 1} of {STEPS.length}
              </div>
              <h3 className="text-2xl md:text-3xl font-bold text-white mb-3">
                {step.title}
              </h3>
              <p className="text-white/80 text-lg mb-6">
                {step.description}
              </p>

              {/* Progress bar */}
              <div className="flex items-center gap-4">
                <div className="flex-1 h-1 bg-white/10 rounded-full overflow-hidden">
                  <motion.div
                    className="h-full rounded-full"
                    style={{ backgroundColor: step.color }}
                    initial={{ width: '0%' }}
                    animate={{ width: isPlaying ? '100%' : '0%' }}
                    transition={{ duration: 3, ease: 'linear' }}
                    key={`${currentStep}-${isPlaying}`}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={handlePlayPause}
                    aria-label={isPlaying ? 'Pause walkthrough' : 'Play walkthrough'}
                    title={isPlaying ? 'Pause walkthrough' : 'Play walkthrough'}
                    className="p-2 rounded-lg bg-black/45 border border-white/20 hover:bg-white/15 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80"
                  >
                    {isPlaying ? (
                      <Pause className="w-4 h-4 text-white/70" />
                    ) : (
                      <Play className="w-4 h-4 text-white/70" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={handleReset}
                    aria-label="Restart walkthrough"
                    title="Restart walkthrough"
                    className="p-2 rounded-lg bg-black/45 border border-white/20 hover:bg-white/15 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80"
                  >
                    <RotateCcw className="w-4 h-4 text-white/70" />
                  </button>
                </div>
              </div>
            </motion.div>

            {/* Demo visualization */}
            <motion.div
              key={`demo-${step.id}`}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.3 }}
            >
              <DemoContent step={step} isActive={true} />
            </motion.div>
          </div>
        </div>

        {/* CTA */}
        <motion.div
          className="text-center mt-16"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
        >
          <button
            onClick={onLaunch}
            className="inline-flex items-center gap-2 px-6 py-3 bg-white text-black font-semibold rounded-xl hover:bg-white/90 transition-all cursor-pointer"
          >
            Try It Yourself
            <ChevronRight className="w-4 h-4" />
          </button>
        </motion.div>
      </div>
    </section>
  )
}
