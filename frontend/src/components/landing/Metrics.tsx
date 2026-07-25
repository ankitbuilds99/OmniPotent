import { useRef, useState, useEffect } from 'react'
import { motion, useInView } from 'framer-motion'
import { TrendingUp, Clock, Rocket, Container, Globe } from 'lucide-react'

const METRICS = [
  {
    icon: TrendingUp,
    value: 99.99,
    suffix: '%',
    label: 'Deployment Success',
    color: '#3CF281',
    description: 'Industry-leading reliability'
  },
  {
    icon: Clock,
    value: 18,
    suffix: 'ms',
    label: 'Average Response',
    color: '#00E7FF',
    description: 'Lightning fast operations'
  },
  {
    icon: Rocket,
    value: 1532,
    suffix: '+',
    label: 'Deployments',
    color: '#7C3AED',
    description: 'Successful deployments this month'
  },
  {
    icon: Container,
    value: 500,
    suffix: 'K+',
    label: 'Containers',
    color: '#0DB7ED',
    description: 'Containers orchestrated'
  },
  {
    icon: Globe,
    value: 12,
    suffix: '+',
    label: 'Cloud Providers',
    color: '#FF9900',
    description: 'Multi-cloud support'
  }
]

function AnimatedNumber({
  value,
  suffix,
  duration = 2
}: {
  value: number
  suffix: string
  duration?: number
}) {
  const [count, setCount] = useState(0)
  const ref = useRef<HTMLSpanElement>(null)
  const isInView = useInView(ref, { once: true })

  useEffect(() => {
    if (!isInView) return

    const steps = 60
    const increment = value / steps
    const stepDuration = (duration * 1000) / steps
    let current = 0

    const timer = setInterval(() => {
      current += increment
      if (current >= value) {
        setCount(value)
        clearInterval(timer)
      } else {
        setCount(Math.floor(current * 100) / 100)
      }
    }, stepDuration)

    return () => clearInterval(timer)
  }, [value, duration, isInView])

  const displayValue = Number.isInteger(value) ? Math.floor(count) : count.toFixed(2)

  return (
    <span ref={ref}>
      {displayValue}
      <span className="text-white/60">{suffix}</span>
    </span>
  )
}

function MetricCard({
  metric,
  index
}: {
  metric: typeof METRICS[0]
  index: number
}) {
  const Icon = metric.icon

  return (
    <motion.div
      className="relative group"
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
    >
      {/* Background glow on hover */}
      <div
        className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 blur-xl"
        style={{ backgroundColor: `${metric.color}20` }}
      />

      <div className="floating-card p-8 text-center relative">
        {/* Icon */}
        <motion.div
          className="w-16 h-16 mx-auto rounded-2xl flex items-center justify-center mb-6"
          style={{ backgroundColor: `${metric.color}15` }}
          whileHover={{ scale: 1.1, rotate: 5 }}
          transition={{ type: 'spring', stiffness: 400 }}
        >
          <Icon className="w-8 h-8" style={{ color: metric.color }} />
        </motion.div>

        {/* Value */}
        <div className="font-display text-4xl md:text-5xl font-bold text-white mb-2">
          <AnimatedNumber value={metric.value} suffix={metric.suffix} />
        </div>

        {/* Label */}
        <div className="font-display font-semibold text-white mb-2">
          {metric.label}
        </div>

        {/* Description */}
        <div className="text-sm text-white/50">
          {metric.description}
        </div>

        {/* Animated border on hover */}
        <motion.div
          className="absolute inset-0 rounded-2xl pointer-events-none"
          initial={{ opacity: 0 }}
          whileHover={{ opacity: 1 }}
          style={{
            background: `linear-gradient(135deg, ${metric.color}30, transparent)`,
            border: `1px solid ${metric.color}30`
          }}
        />
      </div>
    </motion.div>
  )
}

// Live graph component
function LiveGraph({ color }: { color: string }) {
  const [data, setData] = useState<number[]>(Array(20).fill(50))

  useEffect(() => {
    const interval = setInterval(() => {
      setData((prev) => {
        const newData = [...prev.slice(1)]
        const lastValue = prev[prev.length - 1]
        const change = (Math.random() - 0.5) * 20
        const newValue = Math.max(20, Math.min(80, lastValue + change))
        newData.push(newValue)
        return newData
      })
    }, 500)

    return () => clearInterval(interval)
  }, [])

  const points = data
    .map((v, i) => `${(i / (data.length - 1)) * 100},${100 - v}`)
    .join(' ')

  return (
    <svg className="w-full h-12" viewBox="0 0 100 100" preserveAspectRatio="none">
      <defs>
        <linearGradient id={`graph-${color}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon
        points={`0,100 ${points} 100,100`}
        fill={`url(#graph-${color})`}
      />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

export function Metrics() {
  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background to-background-secondary" />

      {/* Decorative elements */}
      <div className="absolute top-0 left-1/4 w-96 h-96 bg-primary/5 rounded-full blur-3xl" />
      <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-secondary/5 rounded-full blur-3xl" />

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
            By the Numbers
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Trusted by <span className="gradient-text">engineering teams</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Real metrics from production deployments
          </motion.p>
        </div>

        {/* Metrics grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-6 mb-12">
          {METRICS.map((metric, i) => (
            <MetricCard key={metric.label} metric={metric} index={i} />
          ))}
        </div>

        {/* Live graphs */}
        <motion.div
          className="floating-card p-6"
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }}
        >
          <div className="flex items-center justify-between mb-4">
            <span className="font-display font-semibold text-white">Live Performance</span>
            <span className="flex items-center gap-2 text-sm text-success">
              <span className="w-2 h-2 bg-success rounded-full animate-pulse" />
              Real-time
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <div className="text-sm text-white/50 mb-2">Request Rate</div>
              <LiveGraph color="#00E7FF" />
            </div>
            <div>
              <div className="text-sm text-white/50 mb-2">CPU Usage</div>
              <LiveGraph color="#7C3AED" />
            </div>
            <div>
              <div className="text-sm text-white/50 mb-2">Memory</div>
              <LiveGraph color="#00FFB3" />
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
