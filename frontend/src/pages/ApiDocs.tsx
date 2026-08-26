import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import {
  Book,
  Code,
  ExternalLink,
  ChevronRight,
  Copy,
  Check,
  Zap,
  Shield,
  Server,
  Terminal,
  FileJson,
  Webhook,
  Users,
  Activity,
  ArrowLeft
} from 'lucide-react'
// Shown to the user as "your API base URL" and pasted into copyable curl
// examples, so it has to be an absolute URL and the same value the rest of the
// app actually calls. ../config resolves it once: the page's own origin in a
// deployed build rather than the reader's own localhost:8000, which was the old
// hardcoded default.
import { API_BASE_URL } from '../config'

interface EndpointGroup {
  name: string
  description: string
  icon: typeof Code
  color: string
  endpoints: {
    method: 'GET' | 'POST' | 'DELETE' | 'PATCH'
    path: string
    summary: string
    description?: string
  }[]
}

const ENDPOINT_GROUPS: EndpointGroup[] = [
  {
    name: 'Health',
    description: 'Health and readiness probes for orchestration platforms',
    icon: Activity,
    color: '#00E7FF',
    endpoints: [
      { method: 'GET', path: '/healthz', summary: 'Liveness probe' },
      { method: 'GET', path: '/readyz', summary: 'Readiness probe' }
    ]
  },
  {
    name: 'Chat',
    description: 'Core investigation endpoints for AI-powered incident analysis',
    icon: Terminal,
    color: '#7C3AED',
    endpoints: [
      { method: 'POST', path: '/chat', summary: 'Start or continue an investigation' },
      { method: 'POST', path: '/chat/stream', summary: 'Stream investigation progress (SSE)' }
    ]
  },
  {
    name: 'Approval',
    description: 'Human-in-the-loop approval workflow for risky actions',
    icon: Shield,
    color: '#00FFB3',
    endpoints: [
      { method: 'POST', path: '/approve', summary: 'Approve or reject a pending action' },
      { method: 'POST', path: '/approve/stream', summary: 'Stream approval continuation (SSE)' }
    ]
  },
  {
    name: 'Config',
    description: 'Runtime configuration management',
    icon: Server,
    color: '#FF9900',
    endpoints: [
      { method: 'GET', path: '/me', summary: 'Get current identity' },
      { method: 'GET', path: '/config', summary: 'Get agent configuration' },
      { method: 'GET', path: '/github/status', summary: 'Get GitHub connection status' },
      { method: 'POST', path: '/github/connect', summary: 'Connect GitHub repository' },
      { method: 'POST', path: '/github/disconnect', summary: 'Disconnect GitHub' },
      { method: 'POST', path: '/model/configure', summary: 'Configure LLM provider' },
      { method: 'POST', path: '/sources/repo', summary: 'Set repository source path' },
      { method: 'POST', path: '/sources/logs', summary: 'Set logs source path' },
      { method: 'POST', path: '/reset', summary: 'Reset configuration to defaults' }
    ]
  },
  {
    name: 'Metrics',
    description: 'Observability and usage statistics',
    icon: Activity,
    color: '#FF5757',
    endpoints: [
      { method: 'GET', path: '/metrics', summary: 'Get real-time metrics' },
      { method: 'GET', path: '/metrics/slo', summary: 'Get SLO metrics (Prometheus format)' },
      { method: 'GET', path: '/usage', summary: 'Get usage statistics' },
      { method: 'GET', path: '/cache/stats', summary: 'Get cache statistics' }
    ]
  },
  {
    name: 'Feedback',
    description: 'Investigation quality feedback for continuous improvement',
    icon: Zap,
    color: '#FFD700',
    endpoints: [
      { method: 'POST', path: '/feedback', summary: 'Submit investigation feedback' }
    ]
  },
  {
    name: 'Audit',
    description: 'Compliance and audit trail',
    icon: FileJson,
    color: '#9333EA',
    endpoints: [
      { method: 'GET', path: '/audit', summary: 'Query audit trail' },
      { method: 'GET', path: '/audit/verify', summary: 'Verify audit chain integrity' }
    ]
  },
  {
    name: 'Admin',
    description: 'Multi-tenant organization management',
    icon: Users,
    color: '#EC4899',
    endpoints: [
      { method: 'GET', path: '/admin/org', summary: 'Get organization details' },
      { method: 'GET', path: '/admin/api-keys', summary: 'List API keys' },
      { method: 'POST', path: '/admin/api-keys', summary: 'Create API key' },
      { method: 'DELETE', path: '/admin/api-keys/{key_id}', summary: 'Revoke API key' },
      { method: 'POST', path: '/admin/members', summary: 'Add organization member' },
      { method: 'GET', path: '/admin/integrations', summary: 'List integrations' },
      { method: 'POST', path: '/admin/integrations', summary: 'Set integration secret' },
      { method: 'PATCH', path: '/admin/plan', summary: 'Update organization plan' },
      { method: 'POST', path: '/signup', summary: 'Self-serve organization signup' }
    ]
  },
  {
    name: 'Webhooks',
    description: 'External service integrations',
    icon: Webhook,
    color: '#06B6D4',
    endpoints: [
      { method: 'POST', path: '/webhooks/pagerduty', summary: 'PagerDuty webhook receiver' },
      { method: 'POST', path: '/webhooks/stripe', summary: 'Stripe webhook receiver' },
      { method: 'POST', path: '/webhooks/slack/interactions', summary: 'Slack interactions receiver' }
    ]
  },
  {
    name: 'Autonomy',
    description: 'Progressive autonomy remediation',
    icon: Zap,
    color: '#10B981',
    endpoints: [
      { method: 'POST', path: '/remediate', summary: 'Execute autonomous remediation' }
    ]
  }
]

const METHOD_COLORS: Record<string, { bg: string; text: string }> = {
  GET: { bg: 'bg-green-500/20', text: 'text-green-400' },
  POST: { bg: 'bg-blue-500/20', text: 'text-blue-400' },
  DELETE: { bg: 'bg-red-500/20', text: 'text-red-400' },
  PATCH: { bg: 'bg-yellow-500/20', text: 'text-yellow-400' }
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="p-1.5 rounded-md bg-white/5 hover:bg-white/10 transition-colors cursor-pointer"
      title="Copy to clipboard"
    >
      {copied ? (
        <Check className="w-4 h-4 text-green-400" />
      ) : (
        <Copy className="w-4 h-4 text-white/50" />
      )}
    </button>
  )
}

function EndpointCard({ group }: { group: EndpointGroup }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = group.icon

  return (
    <motion.div
      className="bg-white/[0.02] rounded-xl border border-white/10 overflow-hidden"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-4 p-4 hover:bg-white/[0.02] transition-colors cursor-pointer"
      >
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center"
          style={{ backgroundColor: `${group.color}20` }}
        >
          <Icon className="w-5 h-5" style={{ color: group.color }} />
        </div>
        <div className="flex-1 text-left">
          <h3 className="font-semibold text-white">{group.name}</h3>
          <p className="text-sm text-white/50">{group.description}</p>
        </div>
        <span className="text-white/30 text-sm">{group.endpoints.length} endpoints</span>
        <ChevronRight
          className={`w-5 h-5 text-white/30 transition-transform ${expanded ? 'rotate-90' : ''}`}
        />
      </button>

      {expanded && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          className="border-t border-white/10"
        >
          {group.endpoints.map((endpoint, i) => {
            const colors = METHOD_COLORS[endpoint.method]
            return (
              <div
                key={i}
                className="flex items-center gap-3 p-4 border-b border-white/5 last:border-b-0 hover:bg-white/[0.02]"
              >
                <span
                  className={`px-2 py-1 rounded text-xs font-bold ${colors.bg} ${colors.text}`}
                >
                  {endpoint.method}
                </span>
                <code className="text-primary font-mono text-sm flex-1">{endpoint.path}</code>
                <span className="text-white/50 text-sm hidden md:block">{endpoint.summary}</span>
                <CopyButton text={`${API_BASE_URL}${endpoint.path}`} />
              </div>
            )
          })}
        </motion.div>
      )}
    </motion.div>
  )
}

export function ApiDocs({ onBack }: { onBack?: () => void }) {
  const [apiHealth, setApiHealth] = useState<'checking' | 'online' | 'offline'>('checking')

  useEffect(() => {
    fetch(`${API_BASE_URL}/healthz`)
      .then((res) => setApiHealth(res.ok ? 'online' : 'offline'))
      .catch(() => setApiHealth('offline'))
  }, [])

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-white/10 bg-background/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="container-wide flex items-center justify-between h-16 px-4">
          <div className="flex items-center gap-4">
            {onBack && (
              <button
                onClick={onBack}
                className="p-2 rounded-lg hover:bg-white/5 transition-colors cursor-pointer"
              >
                <ArrowLeft className="w-5 h-5 text-white/70" />
              </button>
            )}
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
                <Book className="w-4 h-4 text-primary" />
              </div>
              <h1 className="font-display text-xl font-bold text-white">API Documentation</h1>
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <div
                className={`w-2 h-2 rounded-full ${
                  apiHealth === 'online'
                    ? 'bg-green-400'
                    : apiHealth === 'offline'
                    ? 'bg-red-400'
                    : 'bg-yellow-400 animate-pulse'
                }`}
              />
              <span className="text-sm text-white/50">
                {apiHealth === 'online' ? 'API Online' : apiHealth === 'offline' ? 'API Offline' : 'Checking...'}
              </span>
            </div>
            <a
              href={`${API_BASE_URL}/docs`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-2 bg-primary/10 hover:bg-primary/20 border border-primary/30 rounded-lg text-primary text-sm font-medium transition-colors"
            >
              <Code className="w-4 h-4" />
              Swagger UI
              <ExternalLink className="w-3 h-3" />
            </a>
            <a
              href={`${API_BASE_URL}/redoc`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-white/70 text-sm font-medium transition-colors"
            >
              ReDoc
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        </div>
      </header>

      <main className="container-wide py-12 px-4">
        {/* Intro */}
        <div className="max-w-3xl mb-12">
          <h2 className="font-display text-3xl font-bold text-white mb-4">
            OmniPotent REST API
          </h2>
          <p className="text-white/60 text-lg mb-6">
            The complete API reference for OmniPotent. Use these endpoints to integrate
            AI-powered incident investigation into your workflows.
          </p>

          {/* Quick Links */}
          <div className="flex flex-wrap gap-3">
            <a
              href={`${API_BASE_URL}/openapi.json`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-white/70 text-sm transition-colors"
            >
              <FileJson className="w-4 h-4" />
              OpenAPI Spec
              <ExternalLink className="w-3 h-3" />
            </a>
            <a
              href="https://github.com/ankit25bcs10610/OmniPotent"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 px-4 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-white/70 text-sm transition-colors"
            >
              <Code className="w-4 h-4" />
              GitHub
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        </div>

        {/* Authentication */}
        <div className="bg-white/[0.02] rounded-xl border border-white/10 p-6 mb-8">
          <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
            <Shield className="w-5 h-5 text-primary" />
            Authentication
          </h3>
          <p className="text-white/60 mb-4">
            All endpoints (except health checks and webhooks) require a Bearer token:
          </p>
          <div className="bg-[#0d1117] rounded-lg p-4 font-mono text-sm">
            <span className="text-white/50">Authorization:</span>{' '}
            <span className="text-primary">Bearer</span>{' '}
            <span className="text-green-400">{'<api_key>'}</span>
          </div>
        </div>

        {/* Base URL */}
        <div className="bg-white/[0.02] rounded-xl border border-white/10 p-6 mb-8">
          <h3 className="font-semibold text-white mb-3 flex items-center gap-2">
            <Server className="w-5 h-5 text-primary" />
            Base URL
          </h3>
          <div className="flex items-center gap-3 bg-[#0d1117] rounded-lg p-4">
            <code className="font-mono text-sm text-primary flex-1">{API_BASE_URL}</code>
            <CopyButton text={API_BASE_URL} />
          </div>
        </div>

        {/* Endpoint Groups */}
        <div className="space-y-4">
          <h3 className="font-semibold text-white text-lg mb-4">Endpoints</h3>
          {ENDPOINT_GROUPS.map((group) => (
            <EndpointCard key={group.name} group={group} />
          ))}
        </div>

        {/* Footer Note */}
        <div className="mt-12 text-center text-white/40 text-sm">
          <p>
            For interactive documentation, visit{' '}
            <a
              href={`${API_BASE_URL}/docs`}
              target="_blank"
              rel="noreferrer"
              className="text-primary hover:underline"
            >
              Swagger UI
            </a>{' '}
            or{' '}
            <a
              href={`${API_BASE_URL}/redoc`}
              target="_blank"
              rel="noreferrer"
              className="text-primary hover:underline"
            >
              ReDoc
            </a>
          </p>
        </div>
      </main>
    </div>
  )
}

export default ApiDocs
