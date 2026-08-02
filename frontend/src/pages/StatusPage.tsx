/**
 * Public Status Page
 * Shows service health status and recent incidents
 */

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  CheckCircle,
  AlertTriangle,
  XCircle,
  Clock,
  RefreshCw,
  Activity,
  Server,
  Database,
  Cloud,
  Zap
} from 'lucide-react'

interface ServiceStatus {
  name: string
  status: 'operational' | 'degraded' | 'outage' | 'maintenance'
  latency?: number
  uptime?: number
  lastChecked?: string
}

interface Incident {
  id: string
  title: string
  status: 'investigating' | 'identified' | 'monitoring' | 'resolved'
  severity: 'minor' | 'major' | 'critical'
  createdAt: string
  updatedAt: string
  updates: {
    message: string
    timestamp: string
  }[]
}

interface StatusPageProps {
  onBack?: () => void
}

export function StatusPage({ onBack }: StatusPageProps) {
  const [services, setServices] = useState<ServiceStatus[]>([])
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date())

  useEffect(() => {
    loadStatus()
    const interval = setInterval(loadStatus, 60000) // Refresh every minute
    return () => clearInterval(interval)
  }, [])

  const loadStatus = async () => {
    setLoading(true)
    const startTime = Date.now()
    try {
      // Try to fetch real status from API
      const res = await fetch('/healthz')
      const apiLatency = Date.now() - startTime

      if (res.ok) {
        const health = await res.json()
        // Map health to services with real latency
        setServices([
          {
            name: 'API Server',
            status: health.status === 'ok' ? 'operational' : 'degraded',
            latency: apiLatency,
            uptime: health.uptime_percent,
            lastChecked: new Date().toISOString()
          },
          {
            name: 'WebSocket',
            status: health.websocket?.enabled ? 'operational' : 'maintenance',
            lastChecked: new Date().toISOString()
          },
          {
            name: 'LLM Provider',
            status: health.llm?.configured ? 'operational' : 'maintenance',
            latency: health.llm?.latency_ms,
            lastChecked: new Date().toISOString()
          },
          {
            name: 'Database',
            status: health.database?.connected ? 'operational' : 'outage',
            uptime: health.database?.uptime_percent,
            lastChecked: new Date().toISOString()
          },
          {
            name: 'Authentication',
            status: health.auth?.configured ? 'operational' : 'maintenance',
            lastChecked: new Date().toISOString()
          }
        ])
      }

      // Fetch real incidents from API
      try {
        const incidentsRes = await fetch('/api/incidents?limit=5')
        if (incidentsRes.ok) {
          const incidentsData = await incidentsRes.json()
          const mappedIncidents: Incident[] = incidentsData
            .filter((inc: Record<string, unknown>) => inc.status === 'resolved' || inc.severity)
            .slice(0, 5)
            .map((inc: Record<string, unknown>) => ({
              id: inc.id,
              title: inc.title || 'Service Incident',
              status: inc.status === 'resolved' ? 'resolved' : 'investigating',
              severity: inc.severity === 'critical' ? 'critical' : inc.severity === 'high' ? 'major' : 'minor',
              createdAt: inc.triggered_at || new Date().toISOString(),
              updatedAt: inc.resolved_at || inc.acknowledged_at || inc.triggered_at || new Date().toISOString(),
              updates: []
            }))
          setIncidents(mappedIncidents)
        } else {
          setIncidents([])
        }
      } catch {
        setIncidents([])
      }

      setLastUpdated(new Date())
    } catch {
      // Show degraded status if health check fails
      setServices([
        { name: 'API Server', status: 'degraded', lastChecked: new Date().toISOString() },
        { name: 'WebSocket', status: 'degraded', lastChecked: new Date().toISOString() },
        { name: 'LLM Provider', status: 'degraded', lastChecked: new Date().toISOString() },
        { name: 'Database', status: 'degraded', lastChecked: new Date().toISOString() },
        { name: 'Authentication', status: 'degraded', lastChecked: new Date().toISOString() }
      ])
      setIncidents([])
    } finally {
      setLoading(false)
    }
  }

  const getOverallStatus = () => {
    if (services.some(s => s.status === 'outage')) return 'outage'
    if (services.some(s => s.status === 'degraded')) return 'degraded'
    if (services.some(s => s.status === 'maintenance')) return 'maintenance'
    return 'operational'
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'operational':
        return <CheckCircle size={20} style={{ color: 'var(--ok)' }} />
      case 'degraded':
        return <AlertTriangle size={20} style={{ color: 'var(--warn)' }} />
      case 'outage':
        return <XCircle size={20} style={{ color: 'var(--bad)' }} />
      case 'maintenance':
        return <Clock size={20} style={{ color: 'var(--text-2)' }} />
      default:
        return <Activity size={20} />
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'operational': return 'var(--ok)'
      case 'degraded': return 'var(--warn)'
      case 'outage': return 'var(--bad)'
      case 'maintenance': return 'var(--text-2)'
      default: return 'var(--text)'
    }
  }

  const getServiceIcon = (name: string) => {
    if (name.toLowerCase().includes('api')) return Server
    if (name.toLowerCase().includes('database')) return Database
    if (name.toLowerCase().includes('llm') || name.toLowerCase().includes('ai')) return Zap
    return Cloud
  }

  const overallStatus = getOverallStatus()

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--bg)' }}>
      {/* Header */}
      <header
        className="py-6"
        style={{
          backgroundColor: 'var(--surface)',
          borderBottom: '1px solid var(--border)'
        }}
      >
        <div className="max-w-3xl mx-auto px-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-lg flex items-center justify-center"
                style={{ backgroundColor: 'var(--accent)' }}
              >
                <Activity size={24} style={{ color: 'var(--bg)' }} />
              </div>
              <div>
                <h1
                  className="text-xl font-bold"
                  style={{ color: 'var(--text)' }}
                >
                  OmniPotent Status
                </h1>
                <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                  Real-time service health
                </p>
              </div>
            </div>
            {onBack && (
              <button
                onClick={onBack}
                className="px-4 py-2 rounded-lg text-sm font-medium"
                style={{
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)'
                }}
              >
                Back to Console
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Overall Status Banner */}
      <div
        className="py-8"
        style={{
          backgroundColor: getStatusColor(overallStatus),
          color: overallStatus === 'maintenance' ? 'var(--text)' : 'white'
        }}
      >
        <div className="max-w-3xl mx-auto px-4 text-center">
          <motion.div
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            className="flex items-center justify-center gap-3"
          >
            {getStatusIcon(overallStatus)}
            <h2 className="text-2xl font-bold">
              {overallStatus === 'operational' && 'All Systems Operational'}
              {overallStatus === 'degraded' && 'Partial System Outage'}
              {overallStatus === 'outage' && 'Major System Outage'}
              {overallStatus === 'maintenance' && 'Scheduled Maintenance'}
            </h2>
          </motion.div>
          <p className="mt-2 opacity-90">
            Last updated: {lastUpdated.toLocaleTimeString()}
          </p>
        </div>
      </div>

      {/* Main Content */}
      <main className="max-w-3xl mx-auto px-4 py-8">
        {/* Services */}
        <div
          className="rounded-xl overflow-hidden mb-8"
          style={{
            backgroundColor: 'var(--surface)',
            border: '1px solid var(--border)'
          }}
        >
          <div
            className="px-4 py-3 flex items-center justify-between"
            style={{ borderBottom: '1px solid var(--border)' }}
          >
            <h3 className="font-semibold" style={{ color: 'var(--text)' }}>
              Services
            </h3>
            <button
              onClick={loadStatus}
              disabled={loading}
              className="p-1.5 rounded-lg transition-colors"
              style={{ color: 'var(--text-2)' }}
            >
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>

          <div>
            {services.map((service, i) => {
              const ServiceIcon = getServiceIcon(service.name)
              return (
                <motion.div
                  key={service.name}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05 }}
                  className="px-4 py-3 flex items-center justify-between"
                  style={{
                    borderBottom: i < services.length - 1 ? '1px solid var(--border)' : 'none'
                  }}
                >
                  <div className="flex items-center gap-3">
                    <ServiceIcon size={18} style={{ color: 'var(--text-2)' }} />
                    <span style={{ color: 'var(--text)' }}>{service.name}</span>
                  </div>
                  <div className="flex items-center gap-4">
                    {service.latency && (
                      <span className="text-sm" style={{ color: 'var(--text-2)' }}>
                        {service.latency}ms
                      </span>
                    )}
                    {service.uptime && (
                      <span className="text-sm" style={{ color: 'var(--text-2)' }}>
                        {service.uptime}% uptime
                      </span>
                    )}
                    <div className="flex items-center gap-1.5">
                      {getStatusIcon(service.status)}
                      <span
                        className="text-sm font-medium capitalize"
                        style={{ color: getStatusColor(service.status) }}
                      >
                        {service.status}
                      </span>
                    </div>
                  </div>
                </motion.div>
              )
            })}
          </div>
        </div>

        {/* Incidents */}
        <div
          className="rounded-xl overflow-hidden"
          style={{
            backgroundColor: 'var(--surface)',
            border: '1px solid var(--border)'
          }}
        >
          <div
            className="px-4 py-3"
            style={{ borderBottom: '1px solid var(--border)' }}
          >
            <h3 className="font-semibold" style={{ color: 'var(--text)' }}>
              Recent Incidents
            </h3>
          </div>

          {incidents.length === 0 ? (
            <div className="px-4 py-8 text-center">
              <CheckCircle
                size={32}
                className="mx-auto mb-2"
                style={{ color: 'var(--ok)' }}
              />
              <p style={{ color: 'var(--text-2)' }}>
                No incidents in the last 30 days
              </p>
            </div>
          ) : (
            <div>
              {incidents.map((incident, i) => (
                <div
                  key={incident.id}
                  className="px-4 py-4"
                  style={{
                    borderBottom: i < incidents.length - 1 ? '1px solid var(--border)' : 'none'
                  }}
                >
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <h4 className="font-medium" style={{ color: 'var(--text)' }}>
                        {incident.title}
                      </h4>
                      <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                        {new Date(incident.createdAt).toLocaleDateString()}
                      </p>
                    </div>
                    <span
                      className="px-2 py-1 rounded text-xs font-medium capitalize"
                      style={{
                        backgroundColor: incident.status === 'resolved' ? 'var(--ok)' : 'var(--warn)',
                        color: 'white'
                      }}
                    >
                      {incident.status}
                    </span>
                  </div>
                  <div className="space-y-2 mt-3">
                    {incident.updates.slice(0, 2).map((update, j) => (
                      <div
                        key={j}
                        className="pl-3"
                        style={{ borderLeft: '2px solid var(--border)' }}
                      >
                        <p className="text-sm" style={{ color: 'var(--text)' }}>
                          {update.message}
                        </p>
                        <p className="text-xs mt-1" style={{ color: 'var(--text-2)' }}>
                          {new Date(update.timestamp).toLocaleString()}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="mt-8 text-center">
          <p className="text-sm" style={{ color: 'var(--text-2)' }}>
            Powered by OmniPotent
          </p>
        </div>
      </main>
    </div>
  )
}
