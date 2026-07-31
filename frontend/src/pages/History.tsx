/**
 * Incident History Page
 * Shows past investigations with ability to view details
 */

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  Search,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  FileText,
  Zap,
  Filter,
  RefreshCw,
  Download,
  FileJson,
  FileCode,
  MessageSquare
} from 'lucide-react'
import { downloadInvestigation } from '../utils/exportReport'
import { shareToSlack } from '../api'
import { useSupabaseAuth } from '../hooks/useSupabaseAuth'
import { initSupabase } from '../lib/supabase'

interface Investigation {
  id: string
  thread_id: string
  query: string
  summary: string | null
  status: string
  tokens_used: number
  latency_ms: number | null
  feedback_rating: string | null
  created_at: string
  completed_at: string | null
  report: Record<string, unknown> | null
}

interface HistoryProps {
  onBack: () => void
  onViewInvestigation?: (threadId: string) => void
}

export function History({ onBack, onViewInvestigation }: HistoryProps) {
  const { user } = useSupabaseAuth()
  const [investigations, setInvestigations] = useState<Investigation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [page, setPage] = useState(1)
  const [selectedInvestigation, setSelectedInvestigation] = useState<Investigation | null>(null)
  const pageSize = 10

  useEffect(() => {
    loadInvestigations()
  }, [user, page, statusFilter])

  const loadInvestigations = async () => {
    setLoading(true)
    setError(null)

    try {
      const supabase = await initSupabase()
      if (!supabase) {
        // Fallback to API if Supabase not configured
        const res = await fetch('/api/investigations?limit=50')
        if (res.ok) {
          const data = await res.json()
          setInvestigations(data.investigations || [])
        }
        setLoading(false)
        return
      }

      let query = supabase
        .from('investigations')
        .select('*')
        .order('created_at', { ascending: false })
        .range((page - 1) * pageSize, page * pageSize - 1)

      if (statusFilter !== 'all') {
        query = query.eq('status', statusFilter)
      }

      const { data, error: queryError } = await query

      if (queryError) {
        throw queryError
      }

      setInvestigations(data || [])
    } catch (err) {
      console.error('Failed to load investigations:', err)
      setError('Failed to load investigation history')
      // Load demo data as fallback
      setInvestigations(getDemoInvestigations())
    } finally {
      setLoading(false)
    }
  }

  const filteredInvestigations = investigations.filter(inv =>
    searchQuery === '' ||
    inv.query.toLowerCase().includes(searchQuery.toLowerCase()) ||
    inv.summary?.toLowerCase().includes(searchQuery.toLowerCase())
  )

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle size={16} className="text-green-500" style={{ color: 'var(--ok)' }} />
      case 'error':
        return <XCircle size={16} style={{ color: 'var(--bad)' }} />
      case 'active':
        return <RefreshCw size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
      default:
        return <AlertCircle size={16} style={{ color: 'var(--warn)' }} />
    }
  }

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)

    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    if (diffDays < 7) return `${diffDays}d ago`
    return date.toLocaleDateString()
  }

  const formatLatency = (ms: number | null) => {
    if (!ms) return '-'
    if (ms < 1000) return `${ms}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--bg)' }}>
      {/* Header */}
      <header className="nav">
        <button
          className="nav__btn"
          onClick={onBack}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <ChevronLeft size={18} />
          <span>Back to Console</span>
        </button>
        <div className="nav__right">
          <span className="nav__tag">Investigation History</span>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-5xl mx-auto px-4 py-8">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          {/* Search & Filters */}
          <div
            className="rounded-xl p-4 mb-6"
            style={{
              backgroundColor: 'var(--surface)',
              border: '1px solid var(--border)'
            }}
          >
            <div className="flex flex-wrap gap-4 items-center">
              {/* Search */}
              <div className="flex-1 min-w-[200px] relative">
                <Search
                  size={18}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--text-2)' }}
                />
                <input
                  type="text"
                  placeholder="Search investigations..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg"
                  style={{
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)'
                  }}
                />
              </div>

              {/* Status Filter */}
              <div className="flex items-center gap-2">
                <Filter size={18} style={{ color: 'var(--text-2)' }} />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="px-3 py-2 rounded-lg"
                  style={{
                    backgroundColor: 'var(--bg)',
                    border: '1px solid var(--border)',
                    color: 'var(--text)'
                  }}
                >
                  <option value="all">All Status</option>
                  <option value="completed">Completed</option>
                  <option value="active">Active</option>
                  <option value="error">Error</option>
                </select>
              </div>

              {/* Refresh */}
              <button
                onClick={loadInvestigations}
                className="p-2 rounded-lg transition-colors"
                style={{
                  backgroundColor: 'var(--bg)',
                  border: '1px solid var(--border)'
                }}
                title="Refresh"
              >
                <RefreshCw size={18} style={{ color: 'var(--text-2)' }} />
              </button>
            </div>
          </div>

          {/* Error State */}
          {error && (
            <div
              className="rounded-xl p-4 mb-6 flex items-center gap-3"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--bad)'
              }}
            >
              <AlertCircle size={20} style={{ color: 'var(--bad)' }} />
              <span style={{ color: 'var(--text)' }}>{error}</span>
            </div>
          )}

          {/* Loading State */}
          {loading && (
            <div className="text-center py-12">
              <RefreshCw
                size={32}
                className="animate-spin mx-auto mb-4"
                style={{ color: 'var(--accent)' }}
              />
              <p style={{ color: 'var(--text-2)' }}>Loading investigations...</p>
            </div>
          )}

          {/* Empty State */}
          {!loading && filteredInvestigations.length === 0 && (
            <div
              className="rounded-xl p-12 text-center"
              style={{
                backgroundColor: 'var(--surface)',
                border: '1px solid var(--border)'
              }}
            >
              <FileText
                size={48}
                className="mx-auto mb-4"
                style={{ color: 'var(--text-2)' }}
              />
              <h2
                className="text-xl font-semibold mb-2"
                style={{ color: 'var(--text)' }}
              >
                No investigations yet
              </h2>
              <p style={{ color: 'var(--text-2)' }}>
                Start an investigation from the console to see it here.
              </p>
            </div>
          )}

          {/* Investigation List */}
          {!loading && filteredInvestigations.length > 0 && (
            <div className="space-y-3">
              {filteredInvestigations.map((inv, index) => (
                <motion.div
                  key={inv.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.05 }}
                  className="rounded-xl p-4 cursor-pointer transition-all hover:scale-[1.01]"
                  style={{
                    backgroundColor: 'var(--surface)',
                    border: selectedInvestigation?.id === inv.id
                      ? '2px solid var(--accent)'
                      : '1px solid var(--border)'
                  }}
                  onClick={() => setSelectedInvestigation(
                    selectedInvestigation?.id === inv.id ? null : inv
                  )}
                >
                  <div className="flex items-start justify-between gap-4">
                    {/* Main Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-2">
                        {getStatusIcon(inv.status)}
                        <span
                          className="text-sm font-medium"
                          style={{ color: 'var(--text-2)' }}
                        >
                          {inv.status.charAt(0).toUpperCase() + inv.status.slice(1)}
                        </span>
                        <span style={{ color: 'var(--text-2)' }}>·</span>
                        <Clock size={14} style={{ color: 'var(--text-2)' }} />
                        <span
                          className="text-sm"
                          style={{ color: 'var(--text-2)' }}
                        >
                          {formatDate(inv.created_at)}
                        </span>
                      </div>

                      <h3
                        className="font-medium mb-1 line-clamp-2"
                        style={{ color: 'var(--text)' }}
                      >
                        {inv.query}
                      </h3>

                      {inv.summary && (
                        <p
                          className="text-sm line-clamp-2"
                          style={{ color: 'var(--text-2)' }}
                        >
                          {inv.summary}
                        </p>
                      )}
                    </div>

                    {/* Stats */}
                    <div className="flex items-center gap-4 text-sm shrink-0">
                      <div className="text-right">
                        <p style={{ color: 'var(--text-2)' }}>Tokens</p>
                        <p style={{ color: 'var(--text)' }}>
                          {inv.tokens_used.toLocaleString()}
                        </p>
                      </div>
                      <div className="text-right">
                        <p style={{ color: 'var(--text-2)' }}>Latency</p>
                        <p style={{ color: 'var(--text)' }}>
                          {formatLatency(inv.latency_ms)}
                        </p>
                      </div>
                      {inv.feedback_rating && (
                        <div className="text-right">
                          <p style={{ color: 'var(--text-2)' }}>Feedback</p>
                          <p style={{ color: inv.feedback_rating === 'up' ? 'var(--ok)' : 'var(--bad)' }}>
                            {inv.feedback_rating === 'up' ? '👍' : '👎'}
                          </p>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* Expanded Details */}
                  {selectedInvestigation?.id === inv.id && inv.report && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      className="mt-4 pt-4"
                      style={{ borderTop: '1px solid var(--border)' }}
                    >
                      <h4
                        className="font-medium mb-2 flex items-center gap-2"
                        style={{ color: 'var(--text)' }}
                      >
                        <Zap size={16} style={{ color: 'var(--accent)' }} />
                        Report Summary
                      </h4>
                      <pre
                        className="text-sm p-3 rounded-lg overflow-auto max-h-64"
                        style={{
                          backgroundColor: 'var(--bg)',
                          color: 'var(--text-2)'
                        }}
                      >
                        {JSON.stringify(inv.report, null, 2)}
                      </pre>

                      {/* Export & Share Buttons */}
                      <div className="flex flex-wrap items-center gap-2 mt-3">
                        {/* Share to Slack */}
                        <button
                          onClick={async (e) => {
                            e.stopPropagation()
                            try {
                              const result = await shareToSlack(inv.query, inv.report || {})
                              if (result.success) {
                                alert('Report shared to Slack!')
                              }
                            } catch (err) {
                              alert(err instanceof Error ? err.message : 'Failed to share')
                            }
                          }}
                          className="px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-colors"
                          style={{
                            backgroundColor: '#4A154B',
                            color: 'white'
                          }}
                          title="Share to Slack"
                        >
                          <MessageSquare size={14} />
                          Slack
                        </button>

                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            downloadInvestigation(inv, 'markdown')
                          }}
                          className="px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-colors"
                          style={{
                            backgroundColor: 'var(--bg)',
                            border: '1px solid var(--border)',
                            color: 'var(--text)'
                          }}
                          title="Export as Markdown"
                        >
                          <Download size={14} />
                          Markdown
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            downloadInvestigation(inv, 'html')
                          }}
                          className="px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-colors"
                          style={{
                            backgroundColor: 'var(--bg)',
                            border: '1px solid var(--border)',
                            color: 'var(--text)'
                          }}
                          title="Export as HTML"
                        >
                          <FileCode size={14} />
                          HTML
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            downloadInvestigation(inv, 'json')
                          }}
                          className="px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1.5 transition-colors"
                          style={{
                            backgroundColor: 'var(--bg)',
                            border: '1px solid var(--border)',
                            color: 'var(--text)'
                          }}
                          title="Export as JSON"
                        >
                          <FileJson size={14} />
                          JSON
                        </button>

                        {onViewInvestigation && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              onViewInvestigation(inv.thread_id)
                            }}
                            className="px-4 py-1.5 rounded-lg text-sm font-medium"
                            style={{
                              backgroundColor: 'var(--accent)',
                              color: 'var(--bg)'
                            }}
                          >
                            Continue
                          </button>
                        )}
                      </div>
                    </motion.div>
                  )}
                </motion.div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {!loading && filteredInvestigations.length > 0 && (
            <div className="flex items-center justify-between mt-6">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="flex items-center gap-2 px-4 py-2 rounded-lg transition-colors disabled:opacity-50"
                style={{
                  backgroundColor: 'var(--surface)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)'
                }}
              >
                <ChevronLeft size={18} />
                Previous
              </button>

              <span style={{ color: 'var(--text-2)' }}>
                Page {page}
              </span>

              <button
                onClick={() => setPage(p => p + 1)}
                disabled={filteredInvestigations.length < pageSize}
                className="flex items-center gap-2 px-4 py-2 rounded-lg transition-colors disabled:opacity-50"
                style={{
                  backgroundColor: 'var(--surface)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)'
                }}
              >
                Next
                <ChevronRight size={18} />
              </button>
            </div>
          )}
        </motion.div>
      </main>
    </div>
  )
}

// Demo data for when Supabase is not configured
function getDemoInvestigations(): Investigation[] {
  return [
    {
      id: '1',
      thread_id: 'demo-1',
      query: 'Why is the checkout API throwing 500 errors?',
      summary: 'Root cause identified: null pointer exception in discount calculation after recent deploy.',
      status: 'completed',
      tokens_used: 2450,
      latency_ms: 12500,
      feedback_rating: 'up',
      created_at: new Date(Date.now() - 3600000).toISOString(),
      completed_at: new Date(Date.now() - 3500000).toISOString(),
      report: {
        root_cause: 'Null pointer in discount calculation',
        confidence: 'high',
        affected_services: ['checkout-api', 'payment-service']
      }
    },
    {
      id: '2',
      thread_id: 'demo-2',
      query: 'Database connection pool exhaustion on user-service',
      summary: 'Connection leak found in user session handler. Fix deployed.',
      status: 'completed',
      tokens_used: 3200,
      latency_ms: 18000,
      feedback_rating: 'up',
      created_at: new Date(Date.now() - 86400000).toISOString(),
      completed_at: new Date(Date.now() - 86300000).toISOString(),
      report: {
        root_cause: 'Connection leak in session handler',
        confidence: 'high',
        resolution: 'Added connection.close() in finally block'
      }
    },
    {
      id: '3',
      thread_id: 'demo-3',
      query: 'High latency on search API endpoints',
      summary: 'Missing index on products table causing full table scans.',
      status: 'completed',
      tokens_used: 1800,
      latency_ms: 9500,
      feedback_rating: null,
      created_at: new Date(Date.now() - 172800000).toISOString(),
      completed_at: new Date(Date.now() - 172700000).toISOString(),
      report: {
        root_cause: 'Missing database index',
        confidence: 'high',
        recommendation: 'CREATE INDEX idx_products_category ON products(category_id)'
      }
    }
  ]
}
