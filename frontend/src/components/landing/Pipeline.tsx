import { useRef, useState, useEffect } from 'react'
import { motion, useInView, AnimatePresence } from 'framer-motion'
import {
  GitBranch,
  Container,
  TestTube,
  Shield,
  FileCode,
  Server,
  Ship,
  Rocket,
  CheckCircle
} from 'lucide-react'

const PIPELINE_STAGES = [
  {
    id: 'github',
    icon: GitBranch,
    name: 'GitHub',
    description: 'Code push triggers pipeline',
    color: '#FFFFFF',
    logs: ['Webhook received', 'Branch: main', 'Commit: a1b2c3d']
  },
  {
    id: 'docker',
    icon: Container,
    name: 'Docker Build',
    description: 'Building container image',
    color: '#0DB7ED',
    logs: ['Pulling base image', 'Installing dependencies', 'Building layers', 'Image: sha256:abc123']
  },
  {
    id: 'testing',
    icon: TestTube,
    name: 'Testing',
    description: 'Running test suite',
    color: '#00FFB3',
    logs: ['Unit tests: 127 passed', 'Integration tests: 43 passed', 'Coverage: 87%']
  },
  {
    id: 'security',
    icon: Shield,
    name: 'Security Scan',
    description: 'Vulnerability assessment',
    color: '#FF4D6D',
    logs: ['Scanning dependencies', 'CVE check complete', '0 critical vulnerabilities']
  },
  {
    id: 'terraform',
    icon: FileCode,
    name: 'Terraform',
    description: 'Infrastructure planning',
    color: '#7B42BC',
    logs: ['Init complete', 'Plan: 3 to add, 0 to change', 'Apply approved']
  },
  {
    id: 'provision',
    icon: Server,
    name: 'Provision',
    description: 'Infrastructure deployment',
    color: '#FF9900',
    logs: ['Creating EKS cluster', 'Configuring load balancer', 'Setting up RDS']
  },
  {
    id: 'kubernetes',
    icon: Ship,
    name: 'Kubernetes',
    description: 'Deploying to cluster',
    color: '#326CE5',
    logs: ['Rolling deployment started', 'Pods scaling: 3/3', 'Health checks passing']
  },
  {
    id: 'production',
    icon: Rocket,
    name: 'Production',
    description: 'Live in production',
    color: '#3CF281',
    logs: ['Traffic routing complete', 'Monitoring active', 'Deployment successful']
  }
]

function PipelineStage({
  stage,
  index,
  activeIndex,
  onHover
}: {
  stage: typeof PIPELINE_STAGES[0]
  index: number
  activeIndex: number
  onHover: (index: number) => void
}) {
  const isActive = index <= activeIndex
  const isCurrent = index === activeIndex
  const Icon = stage.icon

  return (
    <motion.div
      className="relative flex flex-col items-center"
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
      onMouseEnter={() => onHover(index)}
    >
      {/* Connector line */}
      {index > 0 && (
        <div className="absolute -left-1/2 top-8 w-full h-0.5">
          <motion.div
            className="h-full bg-gradient-to-r from-transparent via-primary to-transparent"
            initial={{ scaleX: 0 }}
            animate={{ scaleX: isActive ? 1 : 0 }}
            transition={{ duration: 0.5, delay: 0.2 }}
          />
          {/* Animated particle along line */}
          {isCurrent && (
            <motion.div
              className="absolute top-1/2 -translate-y-1/2 w-2 h-2 bg-primary rounded-full shadow-glow-primary"
              animate={{ x: ['0%', '100%'] }}
              transition={{ duration: 1, repeat: Infinity }}
            />
          )}
        </div>
      )}

      {/* Stage node */}
      <motion.div
        className={`
          relative w-16 h-16 rounded-2xl flex items-center justify-center cursor-pointer
          transition-all duration-300
          ${isActive
            ? 'bg-gradient-to-br shadow-lg'
            : 'bg-glass border border-glass-border'
          }
        `}
        style={{
          backgroundImage: isActive
            ? `linear-gradient(135deg, ${stage.color}30, ${stage.color}10)`
            : undefined,
          boxShadow: isCurrent ? `0 0 30px ${stage.color}40` : undefined
        }}
        whileHover={{ scale: 1.1, y: -5 }}
        animate={isCurrent ? { scale: [1, 1.05, 1] } : {}}
        transition={isCurrent ? { duration: 1, repeat: Infinity } : {}}
      >
        <Icon
          className="w-8 h-8"
          style={{ color: isActive ? stage.color : 'rgba(255,255,255,0.4)' }}
        />

        {/* Status indicator */}
        {isActive && (
          <motion.div
            className="absolute -top-1 -right-1"
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
          >
            {isCurrent ? (
              <div className="w-4 h-4 bg-primary rounded-full animate-pulse" />
            ) : (
              <CheckCircle className="w-4 h-4 text-success" />
            )}
          </motion.div>
        )}
      </motion.div>

      {/* Stage name */}
      <span
        className={`mt-3 text-sm font-medium transition-colors duration-300 ${
          isActive ? 'text-white' : 'text-white/40'
        }`}
      >
        {stage.name}
      </span>

      {/* Stage description */}
      <span className="text-xs text-white/40 text-center max-w-[100px]">
        {stage.description}
      </span>
    </motion.div>
  )
}

function LogPanel({ stage }: { stage: typeof PIPELINE_STAGES[0] }) {
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
    }, 400)

    return () => clearInterval(timer)
  }, [stage])

  return (
    <motion.div
      className="terminal mt-8 max-w-2xl mx-auto"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
    >
      <div className="terminal-header">
        <div className="terminal-dot red" />
        <div className="terminal-dot yellow" />
        <div className="terminal-dot green" />
        <span className="ml-4 text-white/40 text-sm font-mono">
          {stage.name.toLowerCase()}.log
        </span>
      </div>
      <div className="terminal-body min-h-[120px]">
        <AnimatePresence mode="popLayout">
          {stage.logs.slice(0, visibleLogs).map((log, i) => (
            <motion.div
              key={`${stage.id}-${i}`}
              className="terminal-line"
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
            >
              <span className="text-success mr-2">✓</span>
              <span className="text-white/70">{log}</span>
            </motion.div>
          ))}
        </AnimatePresence>
        {visibleLogs < stage.logs.length && (
          <div className="terminal-line">
            <span className="terminal-cursor" />
          </div>
        )}
      </div>
    </motion.div>
  )
}

export function Pipeline() {
  const containerRef = useRef<HTMLDivElement>(null)
  const isInView = useInView(containerRef, { once: true, amount: 0.3 })
  const [activeIndex, setActiveIndex] = useState(-1)
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null)

  useEffect(() => {
    if (isInView) {
      const interval = setInterval(() => {
        setActiveIndex((prev) => {
          if (prev >= PIPELINE_STAGES.length - 1) {
            clearInterval(interval)
            return prev
          }
          return prev + 1
        })
      }, 1500)

      return () => clearInterval(interval)
    }
  }, [isInView])

  const displayedStage = hoveredIndex !== null
    ? PIPELINE_STAGES[hoveredIndex]
    : PIPELINE_STAGES[Math.max(0, activeIndex)]

  return (
    <section ref={containerRef} className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background-secondary via-background to-background-secondary" />

      {/* Grid background */}
      <div
        className="absolute inset-0 opacity-20"
        style={{
          backgroundImage: `
            linear-gradient(rgba(0, 231, 255, 0.05) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 231, 255, 0.05) 1px, transparent 1px)
          `,
          backgroundSize: '40px 40px'
        }}
      />

      <div className="container-wide relative z-10">
        {/* Section header */}
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
            Real-time visualization of your deployment pipeline with live logs
          </motion.p>
        </div>

        {/* Pipeline visualization */}
        <div className="overflow-x-auto pb-4">
          <div className="grid grid-cols-4 lg:grid-cols-8 gap-4 min-w-[800px] px-4">
            {PIPELINE_STAGES.map((stage, index) => (
              <PipelineStage
                key={stage.id}
                stage={stage}
                index={index}
                activeIndex={activeIndex}
                onHover={setHoveredIndex}
              />
            ))}
          </div>
        </div>

        {/* Log panel */}
        <AnimatePresence mode="wait">
          <LogPanel key={displayedStage.id} stage={displayedStage} />
        </AnimatePresence>
      </div>
    </section>
  )
}
