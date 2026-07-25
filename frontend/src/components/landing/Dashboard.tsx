import { useRef, useState, useEffect } from 'react'
import { motion, useInView, AnimatePresence } from 'framer-motion'
import {
  Activity,
  Cpu,
  HardDrive,
  Network,
  Container,
  Server,
  FileText,
  TrendingUp,
  TrendingDown
} from 'lucide-react'

// Animated number counter
function AnimatedCounter({
  value,
  duration = 2,
  suffix = ''
}: {
  value: number
  duration?: number
  suffix?: string
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
        setCount(Math.floor(current))
      }
    }, stepDuration)

    return () => clearInterval(timer)
  }, [value, duration, isInView])

  return <span ref={ref}>{count.toLocaleString()}{suffix}</span>
}

// Mini sparkline chart
function Sparkline({
  data,
  color,
  height = 40
}: {
  data: number[]
  color: string
  height?: number
}) {
  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1

  const points = data
    .map((value, i) => {
      const x = (i / (data.length - 1)) * 100
      const y = ((max - value) / range) * height
      return `${x},${y}`
    })
    .join(' ')

  return (
    <svg className="w-full" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={`spark-${color}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.4" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon
        points={`0,${height} ${points} 100,${height}`}
        fill={`url(#spark-${color})`}
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

// Dashboard metric card
function MetricCard({
  icon: Icon,
  title,
  value,
  suffix = '',
  trend,
  trendValue,
  data,
  color,
  delay = 0
}: {
  icon: React.ComponentType<{ className?: string; style?: React.CSSProperties }>
  title: string
  value: number
  suffix?: string
  trend?: 'up' | 'down'
  trendValue?: string
  data?: number[]
  color: string
  delay?: number
}) {
  return (
    <motion.div
      className="floating-card p-5"
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay }}
    >
      <div className="flex items-start justify-between mb-3">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center"
          style={{ backgroundColor: `${color}20` }}
        >
          <Icon className="w-5 h-5" style={{ color }} />
        </div>
        {trend && (
          <div
            className={`flex items-center gap-1 text-sm ${
              trend === 'up' ? 'text-success' : 'text-danger'
            }`}
          >
            {trend === 'up' ? (
              <TrendingUp className="w-4 h-4" />
            ) : (
              <TrendingDown className="w-4 h-4" />
            )}
            {trendValue}
          </div>
        )}
      </div>

      <div className="text-white/60 text-sm mb-1">{title}</div>
      <div className="font-display text-2xl font-bold text-white mb-3">
        <AnimatedCounter value={value} suffix={suffix} />
      </div>

      {data && (
        <div className="h-10">
          <Sparkline data={data} color={color} />
        </div>
      )}
    </motion.div>
  )
}

// Progress bar with animation
function ProgressBar({
  value,
  color,
  label
}: {
  value: number
  color: string
  label: string
}) {
  return (
    <div className="flex items-center gap-3">
      <span className="text-white/60 text-sm w-24">{label}</span>
      <div className="flex-1 h-2 bg-white/10 rounded-full overflow-hidden">
        <motion.div
          className="h-full rounded-full"
          style={{ backgroundColor: color }}
          initial={{ width: 0 }}
          whileInView={{ width: `${value}%` }}
          viewport={{ once: true }}
          transition={{ duration: 1, ease: 'easeOut' }}
        />
      </div>
      <span className="text-white/80 text-sm w-12 text-right">{value}%</span>
    </div>
  )
}

// Live log stream
function LogStream() {
  const [logs, setLogs] = useState<Array<{ id: number; level: string; message: string; time: string }>>([])
  const logId = useRef(0)

  const LOG_MESSAGES = [
    { level: 'info', message: 'Pod checkout-svc-7f8d9b scaled to 3 replicas' },
    { level: 'success', message: 'Health check passed: api-gateway' },
    { level: 'info', message: 'Ingress rule updated: /api/v2/*' },
    { level: 'warning', message: 'High memory usage: payments-svc (78%)' },
    { level: 'info', message: 'ConfigMap reloaded: nginx-config' },
    { level: 'success', message: 'Deployment rollout complete: inventory-svc' },
    { level: 'info', message: 'Certificate renewed: *.example.com' },
    { level: 'error', message: 'Connection timeout: redis-cluster' },
    { level: 'success', message: 'Auto-scaling triggered: frontend-pod' },
    { level: 'info', message: 'Metrics exported to Prometheus' }
  ]

  useEffect(() => {
    const interval = setInterval(() => {
      const randomLog = LOG_MESSAGES[Math.floor(Math.random() * LOG_MESSAGES.length)]
      const now = new Date()
      const time = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`

      setLogs((prev) => [
        { id: logId.current++, ...randomLog, time },
        ...prev.slice(0, 4)
      ])
    }, 2000)

    return () => clearInterval(interval)
  }, [])

  const levelColors: Record<string, string> = {
    info: 'text-primary',
    success: 'text-success',
    warning: 'text-warning',
    error: 'text-danger'
  }

  return (
    <motion.div
      className="floating-card p-5 h-full"
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: 0.3 }}
    >
      <div className="flex items-center gap-2 mb-4">
        <FileText className="w-5 h-5 text-primary" />
        <span className="font-display font-semibold text-white">Live Logs</span>
        <span className="ml-auto flex items-center gap-1">
          <span className="w-2 h-2 bg-success rounded-full animate-pulse" />
          <span className="text-white/50 text-xs">streaming</span>
        </span>
      </div>

      <div className="space-y-2 font-mono text-xs">
        <AnimatePresence mode="popLayout">
          {logs.map((log) => (
            <motion.div
              key={log.id}
              className="flex items-start gap-2 py-1"
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 20 }}
              layout
            >
              <span className="text-white/30">{log.time}</span>
              <span className={`uppercase ${levelColors[log.level]}`}>
                [{log.level}]
              </span>
              <span className="text-white/70 truncate">{log.message}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}

export function Dashboard() {
  const containerRef = useRef<HTMLDivElement>(null)

  // Sample data for sparklines
  const cpuData = [45, 52, 48, 61, 55, 70, 65, 58, 62, 68, 72, 65]
  const memoryData = [62, 58, 65, 70, 68, 75, 72, 78, 74, 80, 76, 72]
  const networkData = [120, 145, 160, 155, 180, 175, 190, 185, 200, 195, 210, 205]
  const requestData = [850, 920, 880, 950, 1020, 980, 1050, 1100, 1080, 1150, 1120, 1200]

  return (
    <section ref={containerRef} className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background-secondary to-background" />

      {/* Grid overlay */}
      <div
        className="absolute inset-0 opacity-10"
        style={{
          backgroundImage: `
            linear-gradient(rgba(0, 231, 255, 0.05) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 231, 255, 0.05) 1px, transparent 1px)
          `,
          backgroundSize: '30px 30px'
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
            Operations Center
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Real-time <span className="gradient-text">monitoring</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Live dashboard with metrics, logs, and infrastructure health
          </motion.p>
        </div>

        {/* Dashboard grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-6">
          <MetricCard
            icon={Cpu}
            title="CPU Usage"
            value={68}
            suffix="%"
            trend="up"
            trendValue="+5%"
            data={cpuData}
            color="#00E7FF"
            delay={0}
          />
          <MetricCard
            icon={HardDrive}
            title="Memory"
            value={72}
            suffix="%"
            trend="up"
            trendValue="+3%"
            data={memoryData}
            color="#7C3AED"
            delay={0.1}
          />
          <MetricCard
            icon={Network}
            title="Network I/O"
            value={205}
            suffix=" MB/s"
            trend="up"
            trendValue="+12%"
            data={networkData}
            color="#00FFB3"
            delay={0.2}
          />
          <MetricCard
            icon={Activity}
            title="Requests/sec"
            value={1200}
            suffix=""
            trend="up"
            trendValue="+8%"
            data={requestData}
            color="#FF9900"
            delay={0.3}
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Infrastructure status */}
          <motion.div
            className="floating-card p-5"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            <div className="flex items-center gap-2 mb-4">
              <Server className="w-5 h-5 text-primary" />
              <span className="font-display font-semibold text-white">Infrastructure Health</span>
            </div>

            <div className="space-y-4">
              <ProgressBar value={99} color="#3CF281" label="API Gateway" />
              <ProgressBar value={95} color="#3CF281" label="Checkout" />
              <ProgressBar value={100} color="#3CF281" label="Inventory" />
              <ProgressBar value={87} color="#FFC247" label="Payments" />
            </div>
          </motion.div>

          {/* Container status */}
          <motion.div
            className="floating-card p-5"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            <div className="flex items-center gap-2 mb-4">
              <Container className="w-5 h-5 text-primary" />
              <span className="font-display font-semibold text-white">Containers</span>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="text-center p-3 rounded-lg bg-success/10">
                <div className="font-display text-3xl font-bold text-success">
                  <AnimatedCounter value={24} />
                </div>
                <div className="text-white/50 text-sm">Running</div>
              </div>
              <div className="text-center p-3 rounded-lg bg-warning/10">
                <div className="font-display text-3xl font-bold text-warning">
                  <AnimatedCounter value={2} />
                </div>
                <div className="text-white/50 text-sm">Pending</div>
              </div>
              <div className="text-center p-3 rounded-lg bg-primary/10">
                <div className="font-display text-3xl font-bold text-primary">
                  <AnimatedCounter value={8} />
                </div>
                <div className="text-white/50 text-sm">Pods</div>
              </div>
              <div className="text-center p-3 rounded-lg bg-secondary/10">
                <div className="font-display text-3xl font-bold text-secondary">
                  <AnimatedCounter value={3} />
                </div>
                <div className="text-white/50 text-sm">Nodes</div>
              </div>
            </div>
          </motion.div>

          {/* Live logs */}
          <LogStream />
        </div>
      </div>
    </section>
  )
}
