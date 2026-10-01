import { useRef, useState, useEffect, useCallback } from 'react'
import { motion, useInView, AnimatePresence } from 'framer-motion'
import { CheckCircle, Play, Clock, Zap, RotateCcw, FlaskConical, ShieldCheck, CloudCog, Triangle } from 'lucide-react'
import { IntegrationLogo } from '../IntegrationLogo'

const PIPELINE_STAGES = [
  {
    id: 'github',
    icon: 'github',
    name: 'GitHub',
    description: 'Code push triggers pipeline',
    color: '#FFFFFF',
    duration: '0.2s',
    logs: ['Webhook received from repository', 'Branch: main', 'Commit: feat: add payment flow', 'Author: @developer']
  },
  {
    id: 'docker',
    icon: 'docker',
    name: 'Docker Build',
    description: 'Building container image',
    color: '#2496ED',
    duration: '1m 24s',
    logs: ['FROM node:20-alpine', 'COPY package*.json ./', 'RUN npm ci --production', 'Successfully built sha256:a1b2c3d4']
  },
  {
    id: 'testing',
    icon: 'jest',
    name: 'Testing',
    description: 'Running test suite',
    color: '#99425B',
    duration: '45s',
    logs: ['Test Suites: 24 passed, 24 total', 'Tests: 187 passed, 187 total', 'Coverage: 89.2%', 'Snapshots: 12 passed']
  },
  {
    id: 'security',
    icon: 'snyk',
    name: 'Security Scan',
    description: 'Vulnerability assessment',
    color: '#4C4A73',
    duration: '32s',
    logs: ['Scanning 847 dependencies...', 'No critical vulnerabilities found', 'No high severity issues', 'Security score: A+']
  },
  {
    id: 'terraform',
    icon: 'terraform',
    name: 'Terraform',
    description: 'Infrastructure planning',
    color: '#844FBA',
    duration: '18s',
    logs: ['Initializing backend...', 'Plan: 2 to add, 1 to change, 0 to destroy', 'Applying changes...', 'Apply complete!']
  },
  {
    id: 'provision',
    icon: 'amazonwebservices',
    name: 'AWS Provision',
    description: 'Infrastructure deployment',
    color: '#FF9900',
    duration: '2m 15s',
    logs: ['Creating ALB target group...', 'Updating ECS service...', 'Configuring auto-scaling...', 'Infrastructure ready']
  },
  {
    id: 'kubernetes',
    icon: 'kubernetes',
    name: 'Kubernetes',
    description: 'Deploying to cluster',
    color: '#326CE5',
    duration: '1m 08s',
    logs: ['deployment.apps/api configured', 'Waiting for rollout...', 'Pods ready: 3/3', 'Service endpoints updated']
  },
  {
    id: 'production',
    icon: 'vercel',
    name: 'Production',
    description: 'Live in production',
    color: '#00DC82',
    duration: '0.8s',
    logs: ['Traffic routing: 100%', 'Health checks: passing', 'Metrics collection: active', '✨ Deployment successful!']
  }
]

const STAGE_BRANDS: Record<string, string> = {
  github: 'github',
  docker: 'docker',
  terraform: 'terraform',
  provision: 'aws',
  kubernetes: 'kubernetes',
}

function StageIcon({ stage, size }: { stage: typeof PIPELINE_STAGES[0]; size: number }) {
  const brand = STAGE_BRANDS[stage.id]
  if (brand) return <IntegrationLogo name={brand} size={size} />

  const FallbackIcon = {
    testing: FlaskConical,
    security: ShieldCheck,
    production: Triangle,
  }[stage.id] ?? CloudCog

  return <FallbackIcon size={Math.round(size * 0.76)} strokeWidth={1.8} aria-hidden="true" />
}

function FlowingParticles({ isActive, color }: { isActive: boolean; color: string }) {
  if (!isActive) return null

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {[...Array(3)].map((_, i) => (
        <motion.div
          key={i}
          className="absolute w-2 h-2 rounded-full"
          style={{
            backgroundColor: color,
            boxShadow: `0 0 10px ${color}, 0 0 20px ${color}`,
            top: '50%',
            left: 0
          }}
          animate={{
            x: ['0%', '100%'],
            opacity: [0, 1, 1, 0]
          }}
          transition={{
            duration: 1.5,
            delay: i * 0.5,
            repeat: Infinity,
            ease: 'linear'
          }}
        />
      ))}
    </div>
  )
}

function PipelineStage({
  stage,
  index,
  activeIndex,
  isHovered,
  onHover,
  onLeave,
  onClick
}: {
  stage: typeof PIPELINE_STAGES[0]
  index: number
  activeIndex: number
  isHovered: boolean
  onHover: () => void
  onLeave: () => void
  onClick: () => void
}) {
  const isActive = index <= activeIndex
  const isCurrent = index === activeIndex
  const isCompleted = index < activeIndex

  return (
    <div className="relative flex flex-col items-center">
      {/* Connector line */}
      {index > 0 && (
        <div className="absolute right-1/2 top-10 w-full h-1 -translate-y-1/2 z-0">
          <div className="absolute inset-0 bg-white/5 rounded-full" />
          <motion.div
            className="absolute inset-y-0 left-0 rounded-full"
            style={{
              background: `linear-gradient(90deg, ${PIPELINE_STAGES[index - 1].color}, ${stage.color})`
            }}
            initial={{ width: '0%' }}
            animate={{ width: isActive ? '100%' : '0%' }}
            transition={{ duration: 0.6, delay: 0.2 }}
          />
          <FlowingParticles isActive={isCurrent} color={stage.color} />
        </div>
      )}

      {/* Stage card */}
      <motion.button
        type="button"
        className={`
          omni-landing__pipeline-stage relative z-10 w-20 h-20 rounded-2xl flex items-center justify-center cursor-pointer
          transition-all duration-300 border-2
          ${isActive
            ? 'bg-white/5 backdrop-blur-sm'
            : 'bg-white/[0.02] border-white/5'
          }
          ${isCurrent ? 'border-white/30' : isCompleted ? 'border-transparent' : 'border-white/5'}
        `}
        style={{
          background: isActive
            ? `linear-gradient(135deg, ${stage.color}15, ${stage.color}05)`
            : undefined,
          borderColor: isCurrent ? stage.color : undefined,
          boxShadow: isCurrent
            ? `0 0 40px ${stage.color}30, inset 0 0 20px ${stage.color}10`
            : isHovered
            ? `0 8px 32px rgba(0,0,0,0.3)`
            : undefined
        }}
        whileHover={{ scale: 1.08, y: -8 }}
        animate={isCurrent ? {
          boxShadow: [
            `0 0 20px ${stage.color}20`,
            `0 0 40px ${stage.color}40`,
            `0 0 20px ${stage.color}20`
          ]
        } : {}}
        transition={isCurrent ? { duration: 2, repeat: Infinity } : { duration: 0.2 }}
        onMouseEnter={onHover}
        onMouseLeave={onLeave}
        onClick={onClick}
        aria-label={`Show pipeline stage ${index + 1}: ${stage.name}`}
        aria-pressed={isCurrent}
      >
        <span
          className="flex items-center justify-center w-9 h-9 transition-all duration-300"
          style={{ opacity: isActive ? 1 : 0.3, filter: isActive ? 'none' : 'grayscale(100%)' }}
        >
          <StageIcon stage={stage} size={36} />
        </span>

        <motion.div
          className="absolute -top-2 -right-2"
          initial={{ scale: 0 }}
          animate={{ scale: isActive ? 1 : 0 }}
          transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        >
          {isCurrent ? (
            <div
              className="w-6 h-6 rounded-full flex items-center justify-center"
              style={{ backgroundColor: stage.color }}
            >
              <Play className="w-3 h-3 text-white fill-white" />
            </div>
          ) : isCompleted ? (
            <div className="w-6 h-6 rounded-full bg-green-500 flex items-center justify-center">
              <CheckCircle className="w-4 h-4 text-white" />
            </div>
          ) : null}
        </motion.div>

        {isCurrent && (
          <motion.div
            className="absolute inset-0 rounded-2xl border-2"
            style={{ borderColor: stage.color }}
            animate={{
              scale: [1, 1.2, 1],
              opacity: [0.5, 0, 0.5]
            }}
            transition={{ duration: 2, repeat: Infinity }}
          />
        )}
      </motion.button>

      <motion.span
        className={`mt-4 text-sm font-semibold transition-colors duration-300 ${
          isActive ? 'text-white' : 'text-white/30'
        }`}
        animate={{ color: isCurrent ? stage.color : undefined }}
      >
        {stage.name}
      </motion.span>

      <span className="text-xs text-white/40 text-center max-w-[100px] mt-1">
        {stage.description}
      </span>

      {isActive && (
        <motion.div
          className="flex items-center gap-1 mt-2 text-xs"
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <Clock className="w-3 h-3" style={{ color: stage.color }} />
          <span style={{ color: stage.color }}>{stage.duration}</span>
        </motion.div>
      )}
    </div>
  )
}

function LogPanel({ stage, isCompleted }: { stage: typeof PIPELINE_STAGES[0]; isCompleted: boolean }) {
  const [visibleLogs, setVisibleLogs] = useState<number>(0)

  useEffect(() => {
    setVisibleLogs(0)
    const timer = setInterval(() => {
      setVisibleLogs((prev) => {
        if (prev >= stage.logs.length) {
          clearInterval(timer)
          return prev
        }
        return prev + 1
      })
    }, 300)

    return () => clearInterval(timer)
  }, [stage])

  return (
    <motion.div
      className="mt-12 max-w-3xl mx-auto"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3 }}
    >
      <div className="rounded-xl overflow-hidden border border-white/10 bg-[#0d1117] shadow-2xl">
        <div className="flex items-center gap-3 px-4 py-3 bg-[#161b22] border-b border-white/10">
          <div className="flex gap-2">
            <div className="w-3 h-3 rounded-full bg-[#ff5f57]" />
            <div className="w-3 h-3 rounded-full bg-[#febc2e]" />
            <div className="w-3 h-3 rounded-full bg-[#28c840]" />
          </div>
          <div className="flex-1 flex items-center justify-center gap-2">
            <StageIcon stage={stage} size={18} />
            <span className="text-white/60 text-sm font-mono">
              {stage.name.toLowerCase().replace(' ', '_')}.log
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="px-2 py-0.5 rounded text-xs font-medium"
              style={{
                backgroundColor: `${stage.color}20`,
                color: stage.color
              }}
            >
              {isCompleted ? 'COMPLETED' : 'RUNNING'}
            </span>
          </div>
        </div>

        <div className="p-4 font-mono text-sm min-h-[160px] max-h-[200px] overflow-y-auto">
          <AnimatePresence mode="popLayout">
            {stage.logs.slice(0, visibleLogs).map((log, i) => (
              <motion.div
                key={`${stage.id}-${i}`}
                className="flex items-start gap-3 py-1.5"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.2 }}
              >
                <span className="text-white/30 select-none w-6 text-right">{i + 1}</span>
                <span className="text-green-400">$</span>
                <span className="text-white/80 flex-1">{log}</span>
                <motion.span
                  initial={{ opacity: 0, scale: 0 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.1 }}
                >
                  <CheckCircle className="w-4 h-4 text-green-500" />
                </motion.span>
              </motion.div>
            ))}
          </AnimatePresence>

          {visibleLogs < stage.logs.length && (
            <div className="flex items-center gap-3 py-1.5">
              <span className="text-white/30 select-none w-6 text-right">{visibleLogs + 1}</span>
              <span className="text-green-400">$</span>
              <motion.span
                className="w-2 h-5 bg-white/70"
                animate={{ opacity: [1, 0] }}
                transition={{ duration: 0.8, repeat: Infinity }}
              />
            </div>
          )}

          {visibleLogs >= stage.logs.length && (
            <motion.div
              className="flex items-center gap-2 mt-4 pt-4 border-t border-white/10"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
            >
              <Zap className="w-4 h-4" style={{ color: stage.color }} />
              <span style={{ color: stage.color }}>Stage completed in {stage.duration}</span>
            </motion.div>
          )}
        </div>
      </div>
    </motion.div>
  )
}

function PipelineProgress({ activeIndex, total, onRestart, isComplete }: {
  activeIndex: number;
  total: number;
  onRestart: () => void;
  isComplete: boolean;
}) {
  const progress = Math.max(0, ((activeIndex + 1) / total) * 100)

  return (
    <div className="max-w-3xl mx-auto mb-12">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-white/60">Pipeline Progress</span>
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-white">
            {Math.min(activeIndex + 1, total)} of {total} stages
          </span>
          {isComplete && (
            <button
              onClick={onRestart}
              className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3 h-3" />
              Replay
            </button>
          )}
        </div>
      </div>
      <div className="h-2 bg-white/5 rounded-full overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-gradient-to-r from-cyan-500 via-purple-500 to-green-500"
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.5 }}
        />
      </div>
    </div>
  )
}

export function Pipeline() {
  const containerRef = useRef<HTMLDivElement>(null)
  const isInView = useInView(containerRef, { once: false, amount: 0.3 })
  const [activeIndex, setActiveIndex] = useState(-1)
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const startPipeline = useCallback(() => {
    setActiveIndex(-1)
    setIsRunning(true)
  }, [])

  const restartPipeline = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
    }
    setActiveIndex(-1)
    setIsRunning(true)
  }, [])

  // Auto-start when in view
  useEffect(() => {
    if (isInView && activeIndex === -1 && !isRunning) {
      startPipeline()
    }
  }, [isInView, activeIndex, isRunning, startPipeline])

  // Progress through stages
  useEffect(() => {
    if (!isRunning) return

    if (activeIndex < PIPELINE_STAGES.length - 1) {
      timerRef.current = setTimeout(() => {
        setActiveIndex((prev) => prev + 1)
      }, activeIndex === -1 ? 500 : 1500)
    } else {
      // Pipeline complete - auto restart after delay
      timerRef.current = setTimeout(() => {
        restartPipeline()
      }, 5000)
    }

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
  }, [isRunning, activeIndex, restartPipeline])

  const handleStageClick = useCallback((index: number) => {
    setActiveIndex(index)
  }, [])

  const displayedStage = hoveredIndex !== null
    ? PIPELINE_STAGES[hoveredIndex]
    : PIPELINE_STAGES[Math.max(0, activeIndex)]

  const isDisplayedCompleted = hoveredIndex !== null
    ? hoveredIndex < activeIndex
    : activeIndex === PIPELINE_STAGES.length - 1

  const isComplete = activeIndex === PIPELINE_STAGES.length - 1

  return (
    <section ref={containerRef} className="relative py-32 overflow-hidden omni-landing__section omni-landing__pipeline">
      <div className="absolute inset-0 bg-gradient-to-b from-[#030508] via-background to-[#030508]" />
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `
            linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)
          `,
          backgroundSize: '60px 60px'
        }}
      />

      <div
        className="absolute top-1/2 left-1/4 w-96 h-96 -translate-x-1/2 -translate-y-1/2 opacity-20 blur-3xl pointer-events-none"
        style={{ background: 'radial-gradient(circle, #00E7FF 0%, transparent 70%)' }}
      />
      <div
        className="absolute top-1/2 right-1/4 w-96 h-96 translate-x-1/2 -translate-y-1/2 opacity-20 blur-3xl pointer-events-none"
        style={{ background: 'radial-gradient(circle, #7C3AED 0%, transparent 70%)' }}
      />

      <div className="container-wide relative z-10">
        <div className="text-center mb-16">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            Interactive Pipeline
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Watch code flow to <span className="gradient-text">production</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            {/* Was "Real-time visualization … with live logs". The stages and their
                log lines are a fixed script advanced by a timer — there is no fetch
                in this file and nothing for it to be live against. */}
            The stages a change moves through, and what the agent does at each one
          </motion.p>
        </div>

        <PipelineProgress
          activeIndex={activeIndex}
          total={PIPELINE_STAGES.length}
          onRestart={restartPipeline}
          isComplete={isComplete}
        />

        <div className="overflow-x-auto pb-4 -mx-4 px-4">
          <div className="grid grid-cols-4 lg:grid-cols-8 gap-6 min-w-[900px]">
            {PIPELINE_STAGES.map((stage, index) => (
              <PipelineStage
                key={stage.id}
                stage={stage}
                index={index}
                activeIndex={activeIndex}
                isHovered={hoveredIndex === index}
                onHover={() => setHoveredIndex(index)}
                onLeave={() => setHoveredIndex(null)}
                onClick={() => handleStageClick(index)}
              />
            ))}
          </div>
        </div>

        <AnimatePresence mode="wait">
          <LogPanel
            key={displayedStage.id}
            stage={displayedStage}
            isCompleted={isDisplayedCompleted}
          />
        </AnimatePresence>
      </div>
    </section>
  )
}
