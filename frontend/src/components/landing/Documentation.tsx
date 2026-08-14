import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Book,
  Code,
  Terminal,
  Rocket,
  Settings,
  Zap,
  GitBranch,
  Cloud,
  Shield,
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
  ExternalLink,
  X
} from 'lucide-react'
import {
  buildProjectRepoLinks,
  useProjectRepoUrl,
} from '../../lib/project'
import { useApiBaseUrl } from '../../lib/runtime'

function getDocsSections(repoUrl: string) {
  return [
    {
      id: 'quickstart',
      title: 'Quick Start',
      icon: Rocket,
      color: '#00E7FF',
      description: 'Get up and running in under 5 minutes',
      content: {
        steps: [
          {
            title: 'Clone the repository',
            code: `git clone ${repoUrl}.git\ncd OmniPotent`
          },
          {
            title: 'Install dependencies',
            code: '# Backend\nuv sync\n\n# Frontend\ncd frontend && npm install'
          },
          {
            title: 'Configure environment',
            code: 'cp .env.example .env\n# Add your API keys to .env'
          },
          {
            title: 'Start the application',
            code: '# Start backend\nuv run uvicorn app.api.main:app --reload\n\n# Start frontend (new terminal)\ncd frontend && npm run dev'
          }
        ]
      }
    },
    {
      id: 'api',
      title: 'API Reference',
      icon: Code,
      color: '#7C3AED',
      description: 'Complete API documentation with OpenAPI spec',
      content: {
        endpoints: [
          { method: 'POST', path: '/chat', desc: 'Start or continue an AI investigation' },
          { method: 'POST', path: '/chat/stream', desc: 'Stream investigation progress (SSE)' },
          { method: 'POST', path: '/approve', desc: 'Approve or reject a pending action' },
          { method: 'GET', path: '/healthz', desc: 'Liveness probe' },
          { method: 'GET', path: '/config', desc: 'Get agent configuration' }
        ],
        links: [
          { label: 'Swagger UI', url: '/docs', desc: 'Interactive API explorer' },
          { label: 'ReDoc', url: '/redoc', desc: 'Beautiful API documentation' },
          { label: 'OpenAPI Spec', url: '/openapi.json', desc: 'Generate client SDKs' }
        ]
      }
    },
    {
      id: 'cli',
      title: 'CLI Usage',
      icon: Terminal,
      color: '#00FFB3',
      description: 'Command-line interface guide',
      content: {
        commands: [
          { cmd: 'copilot investigate "Why is API slow?"', desc: 'Start investigation' },
          { cmd: 'copilot deploy --env production', desc: 'Deploy to production' },
          { cmd: 'copilot logs --service api --tail', desc: 'Stream service logs' },
          { cmd: 'copilot status', desc: 'Check system status' }
        ]
      }
    },
    {
      id: 'config',
      title: 'Configuration',
      icon: Settings,
      color: '#FF9900',
      description: 'Environment and settings',
      content: {
        vars: [
          { name: 'COPILOT_PROVIDER', desc: 'LLM provider (anthropic, openai, groq)', default: 'groq' },
          { name: 'DD_API_KEY', desc: 'Datadog API key for metrics', default: '' },
          { name: 'GITHUB_TOKEN', desc: 'GitHub token for PR creation', default: '' },
          { name: 'SLACK_BOT_TOKEN', desc: 'Slack bot for notifications', default: '' }
        ]
      }
    }
  ]
}

const FEATURES_DOCS = [
  { icon: Zap, title: 'AI-Powered RCA', desc: 'Automatic root cause analysis using LLMs' },
  { icon: GitBranch, title: 'Auto PR Creation', desc: 'Generate and submit fixes automatically' },
  { icon: Cloud, title: 'Multi-Cloud', desc: 'AWS, GCP, Azure support out of the box' },
  { icon: Shield, title: 'Human-in-Loop', desc: 'Approval required for all write operations' }
]

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative group">
      <pre className="bg-[#0d1117] border border-white/10 rounded-lg p-4 overflow-x-auto">
        <code className="text-sm text-white/80 font-mono">{code}</code>
      </pre>
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 p-2 rounded-md bg-white/5 hover:bg-white/10 opacity-0 group-hover:opacity-100 transition-all cursor-pointer"
      >
        {copied ? (
          <Check className="w-4 h-4 text-green-400" />
        ) : (
          <Copy className="w-4 h-4 text-white/50" />
        )}
      </button>
    </div>
  )
}

function DocSection({ section, isActive }: { section: any; isActive: boolean }) {
  const Icon = section.icon
  const apiBaseUrl = useApiBaseUrl()

  if (!isActive) return null

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3 }}
      className="space-y-6"
    >
      <div className="flex items-center gap-3 mb-6">
        <div
          className="w-12 h-12 rounded-xl flex items-center justify-center"
          style={{ backgroundColor: `${section.color}20` }}
        >
          <Icon className="w-6 h-6" style={{ color: section.color }} />
        </div>
        <div>
          <h3 className="text-xl font-bold text-white">{section.title}</h3>
          <p className="text-white/50 text-sm">{section.description}</p>
        </div>
      </div>

      {section.id === 'quickstart' && section.content.steps && (
        <div className="space-y-6">
          {section.content.steps.map((step: any, i: number) => (
            <div key={i}>
              <div className="flex items-center gap-2 mb-2">
                <span
                  className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold"
                  style={{ backgroundColor: section.color, color: '#000' }}
                >
                  {i + 1}
                </span>
                <span className="text-white font-medium">{step.title}</span>
              </div>
              <CodeBlock code={step.code} />
            </div>
          ))}
        </div>
      )}

      {section.id === 'api' && section.content.endpoints && (
        <div className="space-y-6">
          {/* API Links */}
          {section.content.links && (
            <div className="flex flex-wrap gap-3 mb-4">
              {section.content.links.map((link: { label: string; url: string; desc: string }, i: number) => (
                <a
                  key={i}
                  href={apiBaseUrl ? `${apiBaseUrl}${link.url}` : link.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-2 px-4 py-2 bg-primary/10 hover:bg-primary/20 border border-primary/30 rounded-lg text-primary text-sm font-medium transition-colors"
                >
                  {link.label}
                  <ExternalLink className="w-3 h-3" />
                </a>
              ))}
            </div>
          )}

          {/* Endpoints */}
          <div className="space-y-3">
            {section.content.endpoints.map((ep: { method: string; path: string; desc: string }, i: number) => (
              <div
                key={i}
                className="flex items-center gap-4 p-4 bg-white/5 rounded-lg border border-white/10"
              >
                <span
                  className={`px-2 py-1 rounded text-xs font-bold ${
                    ep.method === 'GET' ? 'bg-green-500/20 text-green-400' : 'bg-blue-500/20 text-blue-400'
                  }`}
                >
                  {ep.method}
                </span>
                <code className="text-primary font-mono text-sm flex-1">{ep.path}</code>
                <span className="text-white/50 text-sm">{ep.desc}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {section.id === 'cli' && section.content.commands && (
        <div className="space-y-3">
          {section.content.commands.map((cmd: any, i: number) => (
            <div key={i} className="p-4 bg-white/5 rounded-lg border border-white/10">
              <CodeBlock code={cmd.cmd} />
              <p className="text-white/50 text-sm mt-2">{cmd.desc}</p>
            </div>
          ))}
        </div>
      )}

      {section.id === 'config' && section.content.vars && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10">
                <th className="text-left py-3 px-4 text-white/70 font-medium">Variable</th>
                <th className="text-left py-3 px-4 text-white/70 font-medium">Description</th>
                <th className="text-left py-3 px-4 text-white/70 font-medium">Default</th>
              </tr>
            </thead>
            <tbody>
              {section.content.vars.map((v: any, i: number) => (
                <tr key={i} className="border-b border-white/5">
                  <td className="py-3 px-4">
                    <code className="text-primary font-mono">{v.name}</code>
                  </td>
                  <td className="py-3 px-4 text-white/70">{v.desc}</td>
                  <td className="py-3 px-4 text-white/50 font-mono">
                    {v.default || <span className="text-white/30">-</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </motion.div>
  )
}

export function Documentation() {
  const [isOpen, setIsOpen] = useState(false)
  const [activeSection, setActiveSection] = useState('quickstart')
  const repoUrl = useProjectRepoUrl()
  const links = buildProjectRepoLinks(repoUrl)
  const docsSections = getDocsSections(repoUrl)

  return (
    <section id="docs" className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-background via-[#030810] to-background" />

      <div className="container-wide relative z-10">
        {/* Header */}
        <div className="text-center mb-12">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            Documentation
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
          >
            Everything you need to <span className="gradient-text">get started</span>
          </motion.h2>
          <motion.p
            className="text-white/50 text-lg max-w-2xl mx-auto mb-8"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.2 }}
          >
            Comprehensive guides, API references, and examples
          </motion.p>

          {/* Open Docs Button */}
          <motion.button
            onClick={() => setIsOpen(!isOpen)}
            className="inline-flex items-center gap-3 px-8 py-4 bg-primary/10 hover:bg-primary/20 border border-primary/30 rounded-xl text-primary font-semibold transition-all cursor-pointer"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.3 }}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            <Book className="w-5 h-5" />
            {isOpen ? 'Close Documentation' : 'Open Documentation'}
            {isOpen ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
          </motion.button>
        </div>

        {/* Features grid - always visible */}
        <motion.div
          className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ delay: 0.3 }}
        >
          {FEATURES_DOCS.map((feature, i) => {
            const Icon = feature.icon
            return (
              <div
                key={i}
                className="p-4 bg-white/5 rounded-xl border border-white/10 hover:border-primary/30 transition-colors cursor-pointer"
                onClick={() => setIsOpen(true)}
              >
                <Icon className="w-8 h-8 text-primary mb-3" />
                <h4 className="font-semibold text-white mb-1">{feature.title}</h4>
                <p className="text-white/50 text-sm">{feature.desc}</p>
              </div>
            )
          })}
        </motion.div>

        {/* Expandable Docs Content */}
        <AnimatePresence>
          {isOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.4, ease: 'easeInOut' }}
              className="overflow-hidden"
            >
              <div className="grid lg:grid-cols-4 gap-8 pt-8">
                {/* Sidebar */}
                <div className="lg:col-span-1">
                  <nav className="space-y-2 sticky top-24">
                    {docsSections.map((section) => {
                      const Icon = section.icon
                      const isActive = activeSection === section.id
                      return (
                        <button
                          key={section.id}
                          onClick={() => setActiveSection(section.id)}
                          className={`
                            w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left transition-all cursor-pointer
                            ${isActive
                              ? 'bg-primary/10 border border-primary/30 text-white'
                              : 'bg-white/5 border border-transparent text-white/60 hover:text-white hover:bg-white/10'
                            }
                          `}
                        >
                          <Icon
                            className="w-5 h-5"
                            style={{ color: isActive ? section.color : undefined }}
                          />
                          <span className="font-medium">{section.title}</span>
                          {isActive && <ChevronRight className="w-4 h-4 ml-auto" />}
                        </button>
                      )
                    })}

                    {/* GitHub link */}
                    <a
                      href={links.readme}
                      target="_blank"
                      rel="noreferrer"
                      className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white/60 hover:text-white hover:bg-white/10 transition-all mt-4"
                    >
                      <Book className="w-5 h-5" />
                      <span className="font-medium">Full Docs on GitHub</span>
                      <ExternalLink className="w-4 h-4 ml-auto" />
                    </a>

                    {/* Close button */}
                    <button
                      onClick={() => setIsOpen(false)}
                      className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl bg-white/5 border border-white/10 text-white/60 hover:text-white hover:bg-white/10 transition-all mt-4 cursor-pointer"
                    >
                      <X className="w-4 h-4" />
                      <span className="font-medium">Close</span>
                    </button>
                  </nav>
                </div>

                {/* Content */}
                <div className="lg:col-span-3 bg-white/[0.02] rounded-2xl border border-white/10 p-6 md:p-8 min-h-[500px]">
                  {docsSections.map((section) => (
                    <DocSection
                      key={section.id}
                      section={section}
                      isActive={activeSection === section.id}
                    />
                  ))}
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </section>
  )
}
