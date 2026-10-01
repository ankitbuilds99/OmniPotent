/**
 * Public Status Page
 * Shows service health status, incidents, and upcoming maintenance
 */

import { useState, useEffect, useCallback } from 'react'
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
  Zap,
  Calendar,
  ChevronDown,
  ChevronUp,
  Bell,
  Mail
} from 'lucide-react'
import { apiFetch } from '../lib/apiClient'

interface ComponentData {
  id: string
  name: string
  description: string
  status: 'operational' | 'degraded' | 'partial_outage' | 'major_outage' | 'maintenance'
  status_display: string
  group: string | null
  display_order: number
  uptime_30d?: number
  uptime_90d?: number
  created_at: string
  updated_at: string
}

interface IncidentUpdate {
  id: string
  incident_id: string
  status: string
  message: string
  created_at: string
  created_by: string | null
}

interface IncidentData {
  id: string
  title: string
  status: 'investigating' | 'identified' | 'monitoring' | 'resolved'
  severity: 'minor' | 'major' | 'critical'
  message: string
  affected_components: string[]
  created_at: string
  updated_at: string
  resolved_at: string | null
  is_active: boolean
  duration_minutes: number | null
  updates: IncidentUpdate[]
}

interface MaintenanceData {
  id: string
  title: string
  description: string
  status: 'scheduled' | 'in_progress' | 'completed' | 'cancelled'
  affected_components: string[]
  scheduled_start: string
  scheduled_end: string
  actual_start: string | null
  actual_end: string | null
  is_upcoming: boolean
  is_active: boolean
}

interface StatusSummary {
  overall_status: string
  overall_status_display: string
  components: ComponentData[]
  groups: Record<string, ComponentData[]>
  uptimes_30d: Record<string, number>
  uptimes_90d: Record<string, number>
  active_incidents: IncidentData[]
  recent_incidents: IncidentData[]
  upcoming_maintenances: MaintenanceData[]
  updated_at: string
}

interface StatusPageProps {
  onBack?: () => void
}

export function StatusPage({ onBack }: StatusPageProps) {
  const [status, setStatus] = useState<StatusSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date())
  const [expandedIncidents, setExpandedIncidents] = useState<Set<string>>(new Set())
  const [showSubscribe, setShowSubscribe] = useState(false)
  const [subscribeEmail, setSubscribeEmail] = useState('')
  const [subscribeStatus, setSubscribeStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle')
  const [subscribeMessage, setSubscribeMessage] = useState('')
  const [uptimeView, setUptimeView] = useState<'30d' | '90d'>('30d')

  const loadStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiFetch('/api/status')
      if (res.ok) {
        const data: StatusSummary = await res.json()
        setStatus(data)
        setLastUpdated(new Date())
      } else {
        throw new Error('Failed to fetch status')
      }
    } catch (err) {
      setError('Unable to load status. Please try again.')
      console.error('Status fetch error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    const interval = setInterval(loadStatus, 60000) // Refresh every minute
    return () => clearInterval(interval)
  }, [loadStatus])

  const handleSubscribe = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!subscribeEmail) return

    setSubscribeStatus('loading')
    try {
      const res = await apiFetch('/api/status/subscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: subscribeEmail }),
      })
      const data = await res.json()
      if (res.ok) {
        setSubscribeStatus('success')
        setSubscribeMessage(data.message || 'Subscribed successfully!')
        setSubscribeEmail('')
      } else {
        throw new Error(data.detail || 'Subscription failed')
      }
    } catch (err) {
      setSubscribeStatus('error')
      setSubscribeMessage(err instanceof Error ? err.message : 'Subscription failed')
    }
  }

  const toggleIncident = (id: string) => {
    setExpandedIncidents(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'operational':
        return <CheckCircle size={20} style={{ color: 'var(--ok)' }} />
      case 'degraded':
        return <AlertTriangle size={20} style={{ color: 'var(--warn)' }} />
      case 'partial_outage':
      case 'major_outage':
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
      case 'partial_outage':
      case 'major_outage': return 'var(--bad)'
      case 'maintenance': return 'var(--text-2)'
      default: return 'var(--text)'
    }
  }

  const getSeverityColor = (severity: string) => {
    switch (severity) {
      case 'critical': return 'var(--bad)'
      case 'major': return 'var(--warn)'
      case 'minor': return 'var(--accent)'
      default: return 'var(--text-2)'
    }
  }

  const getServiceIcon = (name: string) => {
    const lower = name.toLowerCase()
    if (lower.includes('api')) return Server
    if (lower.includes('database') || lower.includes('db')) return Database
    if (lower.includes('llm') || lower.includes('ai') || lower.includes('auth')) return Zap
    return Cloud
  }

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const formatDuration = (minutes: number) => {
    if (minutes < 60) return `${Math.round(minutes)}m`
    const hours = Math.floor(minutes / 60)
    const mins = Math.round(minutes % 60)
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
  }

  if (error && !status) {
    return (
      <div className="min-h-screen omni-workspace-page omni-status-page flex items-center justify-center" style={{ backgroundColor: 'var(--bg)' }}>
        <div className="text-center">
          <XCircle size={48} className="mx-auto mb-4" style={{ color: 'var(--bad)' }} />
          <p style={{ color: 'var(--text)' }}>{error}</p>
          <button
            onClick={loadStatus}
            className="mt-4 px-4 py-2 rounded-lg"
            style={{ backgroundColor: 'var(--accent)', color: 'var(--bg)' }}
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  const overallStatus = status?.overall_status || 'operational'
  const components = status?.components || []
  // GET /api/status carries uptime in two top-level maps keyed by component
  // id, not on the entries of `components` — those only ever have it inside
  // `groups`. Reading `component.uptime_30d` off this array was always
  // undefined, so no service showed an uptime and the 30d/90d buttons changed
  // nothing but their own colour.
  const uptimes = (uptimeView === '30d' ? status?.uptimes_30d : status?.uptimes_90d) || {}
  const activeIncidents = status?.active_incidents || []
  const recentIncidents = status?.recent_incidents || []
  const upcomingMaintenances = status?.upcoming_maintenances || []

  return (
    <div className="min-h-screen omni-workspace-page omni-status-page" style={{ backgroundColor: 'var(--bg)' }}>
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
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowSubscribe(!showSubscribe)}
                className="px-3 py-2 rounded-lg text-sm font-medium flex items-center gap-2"
                style={{
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)'
                }}
              >
                <Bell size={16} />
                Subscribe
              </button>
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

          {/* Subscribe Form */}
          {showSubscribe && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="mt-4 p-4 rounded-lg"
              style={{ backgroundColor: 'var(--bg)', border: '1px solid var(--border)' }}
            >
              <form onSubmit={handleSubscribe} className="flex gap-2">
                <div className="flex-1 relative">
                  <Mail size={16} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-2)' }} />
                  <input
                    type="email"
                    value={subscribeEmail}
                    onChange={(e) => setSubscribeEmail(e.target.value)}
                    placeholder="Enter your email"
                    className="w-full pl-10 pr-4 py-2 rounded-lg"
                    style={{
                      backgroundColor: 'var(--surface)',
                      border: '1px solid var(--border)',
                      color: 'var(--text)'
                    }}
                    required
                  />
                </div>
                <button
                  type="submit"
                  disabled={subscribeStatus === 'loading'}
                  className="px-4 py-2 rounded-lg font-medium"
                  style={{ backgroundColor: 'var(--accent)', color: 'var(--bg)' }}
                >
                  {subscribeStatus === 'loading' ? 'Subscribing...' : 'Subscribe'}
                </button>
              </form>
              {subscribeMessage && (
                <p
                  className="mt-2 text-sm"
                  style={{ color: subscribeStatus === 'error' ? 'var(--bad)' : 'var(--ok)' }}
                >
                  {subscribeMessage}
                </p>
              )}
            </motion.div>
          )}
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
              {status?.overall_status_display || 'All Systems Operational'}
            </h2>
          </motion.div>
          <p className="mt-2 opacity-90">
            Last updated: {lastUpdated.toLocaleTimeString()}
          </p>
        </div>
      </div>

      {/* Main Content */}
      <main className="max-w-3xl mx-auto px-4 py-8">
        {/* Active Incidents */}
        {activeIncidents.length > 0 && (
          <div
            className="rounded-xl overflow-hidden mb-8"
            style={{
              backgroundColor: 'var(--surface)',
              border: '2px solid var(--bad)'
            }}
          >
            <div
              className="px-4 py-3 flex items-center gap-2"
              style={{ backgroundColor: 'var(--bad)', color: 'white' }}
            >
              <AlertTriangle size={18} />
              <h3 className="font-semibold">Active Incidents</h3>
            </div>
            <div>
              {activeIncidents.map((incident) => (
                <div
                  key={incident.id}
                  className="px-4 py-4"
                  style={{ borderBottom: '1px solid var(--border)' }}
                >
                  <div
                    className="flex items-start justify-between cursor-pointer"
                    onClick={() => toggleIncident(incident.id)}
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <h4 className="font-medium" style={{ color: 'var(--text)' }}>
                          {incident.title}
                        </h4>
                        <span
                          className="px-2 py-0.5 rounded text-xs font-medium uppercase"
                          style={{
                            backgroundColor: getSeverityColor(incident.severity),
                            color: 'white'
                          }}
                        >
                          {incident.severity}
                        </span>
                      </div>
                      <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
                        {incident.message}
                      </p>
                      <p className="text-xs mt-2" style={{ color: 'var(--text-2)' }}>
                        Started {formatDate(incident.created_at)} - Status: {incident.status}
                      </p>
                    </div>
                    {incident.updates.length > 0 && (
                      <button className="p-1" style={{ color: 'var(--text-2)' }}>
                        {expandedIncidents.has(incident.id) ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
                      </button>
                    )}
                  </div>
                  {expandedIncidents.has(incident.id) && incident.updates.length > 0 && (
                    <motion.div
                      initial={{ height: 0 }}
                      animate={{ height: 'auto' }}
                      className="mt-4 space-y-3"
                    >
                      {incident.updates.map((update) => (
                        <div
                          key={update.id}
                          className="pl-3"
                          style={{ borderLeft: '2px solid var(--border)' }}
                        >
                          <p className="text-sm" style={{ color: 'var(--text)' }}>
                            {update.message}
                          </p>
                          <p className="text-xs mt-1" style={{ color: 'var(--text-2)' }}>
                            {formatDate(update.created_at)} - {update.status}
                          </p>
                        </div>
                      ))}
                    </motion.div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Upcoming Maintenance */}
        {upcomingMaintenances.length > 0 && (
          <div
            className="rounded-xl overflow-hidden mb-8"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)'
            }}
          >
            <div
              className="px-4 py-3 flex items-center gap-2"
              style={{ borderBottom: '1px solid var(--border)' }}
            >
              <Calendar size={18} style={{ color: 'var(--text-2)' }} />
              <h3 className="font-semibold" style={{ color: 'var(--text)' }}>
                Scheduled Maintenance
              </h3>
            </div>
            <div>
              {upcomingMaintenances.map((maint, i) => (
                <div
                  key={maint.id}
                  className="px-4 py-4"
                  style={{
                    borderBottom: i < upcomingMaintenances.length - 1 ? '1px solid var(--border)' : 'none'
                  }}
                >
                  <h4 className="font-medium" style={{ color: 'var(--text)' }}>
                    {maint.title}
                  </h4>
                  <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
                    {maint.description}
                  </p>
                  <p className="text-xs mt-2" style={{ color: 'var(--text-2)' }}>
                    {formatDate(maint.scheduled_start)} - {formatDate(maint.scheduled_end)}
                  </p>
                  {maint.affected_components.length > 0 && (
                    <p className="text-xs mt-1" style={{ color: 'var(--text-2)' }}>
                      Affects: {maint.affected_components.join(', ')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Components */}
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
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
                <button
                  onClick={() => setUptimeView('30d')}
                  className="px-2 py-1 text-xs"
                  style={{
                    backgroundColor: uptimeView === '30d' ? 'var(--accent)' : 'transparent',
                    color: uptimeView === '30d' ? 'var(--bg)' : 'var(--text-2)'
                  }}
                >
                  30d
                </button>
                <button
                  onClick={() => setUptimeView('90d')}
                  className="px-2 py-1 text-xs"
                  style={{
                    backgroundColor: uptimeView === '90d' ? 'var(--accent)' : 'transparent',
                    color: uptimeView === '90d' ? 'var(--bg)' : 'var(--text-2)'
                  }}
                >
                  90d
                </button>
              </div>
              <button
                onClick={loadStatus}
                disabled={loading}
                className="p-1.5 rounded-lg transition-colors"
                style={{ color: 'var(--text-2)' }}
              >
                <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
              </button>
            </div>
          </div>

          <div>
            {components.map((component, i) => {
              const ServiceIcon = getServiceIcon(component.name)
              // A component absent from the map has no measurement, which is
              // not the same as 0% — redis really is at 0.0% here.
              const uptime = uptimes[component.id] ?? component[`uptime_${uptimeView}`]
              return (
                <motion.div
                  key={component.id}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05 }}
                  className="px-4 py-3 flex items-center justify-between"
                  style={{
                    borderBottom: i < components.length - 1 ? '1px solid var(--border)' : 'none'
                  }}
                >
                  <div className="flex items-center gap-3">
                    <ServiceIcon size={18} style={{ color: 'var(--text-2)' }} />
                    <div>
                      <span style={{ color: 'var(--text)' }}>{component.name}</span>
                      {component.description && (
                        <p className="text-xs" style={{ color: 'var(--text-2)' }}>
                          {component.description}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {uptime !== undefined && (
                      <span className="text-sm" style={{ color: 'var(--text-2)' }}>
                        {uptime.toFixed(2)}% uptime
                      </span>
                    )}
                    <div className="flex items-center gap-1.5">
                      {getStatusIcon(component.status)}
                      <span
                        className="text-sm font-medium"
                        style={{ color: getStatusColor(component.status) }}
                      >
                        {component.status_display}
                      </span>
                    </div>
                  </div>
                </motion.div>
              )
            })}
          </div>
        </div>

        {/* Recent Incidents */}
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

          {recentIncidents.length === 0 ? (
            <div className="px-4 py-8 text-center">
              <CheckCircle
                size={32}
                className="mx-auto mb-2"
                style={{ color: 'var(--ok)' }}
              />
              <p style={{ color: 'var(--text-2)' }}>
                No incidents in the last 7 days
              </p>
            </div>
          ) : (
            <div>
              {recentIncidents.map((incident, i) => (
                <div
                  key={incident.id}
                  className="px-4 py-4"
                  style={{
                    borderBottom: i < recentIncidents.length - 1 ? '1px solid var(--border)' : 'none'
                  }}
                >
                  <div
                    className="flex items-start justify-between cursor-pointer"
                    onClick={() => toggleIncident(incident.id)}
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <h4 className="font-medium" style={{ color: 'var(--text)' }}>
                          {incident.title}
                        </h4>
                        <span
                          className="px-2 py-0.5 rounded text-xs font-medium"
                          style={{
                            backgroundColor: 'var(--ok)',
                            color: 'white'
                          }}
                        >
                          Resolved
                        </span>
                      </div>
                      <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
                        {formatDate(incident.created_at)}
                        {incident.duration_minutes && ` - Duration: ${formatDuration(incident.duration_minutes)}`}
                      </p>
                    </div>
                    {incident.updates.length > 0 && (
                      <button className="p-1" style={{ color: 'var(--text-2)' }}>
                        {expandedIncidents.has(incident.id) ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
                      </button>
                    )}
                  </div>
                  {expandedIncidents.has(incident.id) && incident.updates.length > 0 && (
                    <motion.div
                      initial={{ height: 0 }}
                      animate={{ height: 'auto' }}
                      className="mt-4 space-y-3"
                    >
                      {incident.updates.map((update) => (
                        <div
                          key={update.id}
                          className="pl-3"
                          style={{ borderLeft: '2px solid var(--border)' }}
                        >
                          <p className="text-sm" style={{ color: 'var(--text)' }}>
                            {update.message}
                          </p>
                          <p className="text-xs mt-1" style={{ color: 'var(--text-2)' }}>
                            {formatDate(update.created_at)} - {update.status}
                          </p>
                        </div>
                      ))}
                    </motion.div>
                  )}
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
