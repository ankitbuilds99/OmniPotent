import { useRef, useState, useEffect } from 'react'
import { motion, useInView } from 'framer-motion'
import { TrendingUp, Clock, Rocket, Container, Globe } from 'lucide-react'

import { useCatalog, type Catalog } from '../../hooks/useCatalog'

interface Metric {
  icon: typeof TrendingUp
  value: number
  suffix: string
  label: string
  color: string
  description: string
}

/** Claims about outcomes, which no endpoint can settle. Both are framed as
 *  targets and the section says "private beta" underneath, so they stay. */
const OUTCOME_METRICS: Metric[] = [
  {
    icon: TrendingUp,
    value: 70,
    suffix: '%',
    label: 'MTTR Reduction',
    color: '#3CF281',
    description: 'Target improvement in pilot tests'
  },
  {
    icon: Clock,
    value: 15,
    suffix: 'min',
    label: 'Avg Resolution',
    color: '#00E7FF',
    description: 'Down from 4+ hours manual RCA'
  }
]

/** The five providers `_LLM_KEY_ENV` (app/tenancy/auth.py) accepts a key for.
 *  Not derivable: GET /config reports the provider in use, not the set that
 *  could be used. Verified against that dict rather than left to memory. */
const PROVIDER_METRIC: Metric = {
  icon: Globe,
  value: 5,
  suffix: '',
  label: 'LLM Providers',
  color: '#FF9900',
  description: 'Claude, GPT-4, Gemini, Groq, DeepSeek'
}

// The counted connector/tool cards used to live here as a local hook; the hero
// stat row needed the same fix, so `useCatalog` moved to hooks/useCatalog.ts
// (history and rationale documented there).

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
  metric: Metric
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

/*
 * What used to sit here: `LiveGraph`, three of them, under a "Live Performance"
 * heading with a pulsing green dot captioned "Real-time" and the labels Request
 * Rate, CPU Usage and Memory.
 *
 * None of it was live and none of it was performance. Each graph seeded itself
 * with `Array(20).fill(50)` and then walked that line every 500ms by
 * `(Math.random() - 0.5) * 20`, clamped to 20–80 so it always looked plausibly
 * healthy. Three independent random walks, presented to a visitor on the public
 * landing page as this system's real-time request rate, CPU and memory. Whose
 * infrastructure it was meant to describe was never answerable, because the
 * answer was nobody's.
 *
 * This is the same thing GlobalMap was fixed for, and the fix is the same:
 * nothing here was measurable, so nothing here is drawn. What replaces it is the
 * connector catalog from GET /config — a real list of what this build ships,
 * which is what the panel was gesturing at anyway.
 */
function ConnectorStrip({ catalog }: { catalog: Catalog }) {
  return (
    <motion.div
      className="floating-card p-6"
      initial={{ opacity: 0, y: 40 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: 0.3 }}
    >
      <div className="flex items-center justify-between mb-4">
        <span className="font-display font-semibold text-white">Connectors in this build</span>
        <span className="text-sm text-white/50">
          {catalog.tools} tools across {catalog.servers} MCP servers
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {catalog.labels.map((label) => (
          <span
            key={label}
            className="text-sm px-3 py-1.5 rounded-lg bg-white/5 text-white/70 border border-white/10"
          >
            {label}
          </span>
        ))}
      </div>
    </motion.div>
  )
}

export function Metrics() {
  const catalog = useCatalog()

  // The two counted cards sit between the outcome targets and the provider
  // count, where the literals used to be. Until /config answers — or if it never
  // does — the row is three cards wide rather than five holding a guess.
  const metrics: Metric[] = [
    ...OUTCOME_METRICS,
    ...(catalog
      ? [
          {
            icon: Rocket,
            value: catalog.tools,
            suffix: '',
            label: 'AI Tools',
            color: '#7C3AED',
            description: 'Integrated observability actions'
          },
          {
            icon: Container,
            value: catalog.servers,
            suffix: '',
            label: 'MCP Servers',
            color: '#0DB7ED',
            description: catalog.labels.slice(0, 4).join(', ')
          }
        ]
      : []),
    PROVIDER_METRIC
  ]

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
            Built for <span className="gradient-text">serious incident response</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Currently in private beta with select engineering teams
          </motion.p>
        </div>

        {/* Metrics grid */}
        <div
          className={`grid grid-cols-1 sm:grid-cols-2 gap-6 mb-12 ${
            metrics.length === 5 ? 'lg:grid-cols-5' : 'lg:grid-cols-3'
          }`}
        >
          {metrics.map((metric, i) => (
            <MetricCard key={metric.label} metric={metric} index={i} />
          ))}
        </div>

        {catalog && <ConnectorStrip catalog={catalog} />}
      </div>
    </section>
  )
}
