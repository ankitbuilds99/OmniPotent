import { motion } from 'framer-motion'
import { Sparkles, Clock, Zap, Shield, Code, GitBranch } from 'lucide-react'

const FEATURES_HIGHLIGHT = [
  {
    icon: Clock,
    title: '15-Minute Resolution',
    description: 'AI investigates incidents in parallel, correlating logs, metrics, and traces instantly.',
    color: '#00E7FF',
  },
  {
    icon: Zap,
    title: 'Multi-LLM Support',
    description: 'Choose from Anthropic, OpenAI, Gemini, Groq, or DeepSeek based on your needs.',
    color: '#7C3AED',
  },
  {
    icon: Shield,
    title: 'Human-in-the-Loop',
    description: 'Every production action requires your explicit approval. AI proposes, you decide.',
    color: '#00FFB3',
  },
  {
    icon: Code,
    title: '35+ Connectors',
    description: 'Pre-built integrations with Kubernetes, Datadog, PagerDuty, GitHub, and more.',
    color: '#FF6B6B',
  },
  {
    icon: GitBranch,
    title: 'Auto PR Generation',
    description: 'AI drafts fix PRs with full context and test plans, ready for your review.',
    color: '#FFD93D',
  },
  {
    icon: Sparkles,
    title: 'Incident Learning',
    description: 'Learns from past incidents to improve future investigations automatically.',
    color: '#E91E63',
  },
]

export function Testimonials() {
  return (
    <section className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-[#030508]" />

      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[#00E7FF]/5 rounded-full blur-[150px]" />

      <div className="max-w-6xl mx-auto px-6 relative z-10">
        <div className="text-center mb-16">
          <motion.span
            className="inline-block text-[#00E7FF] text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            Why Teams Choose Us
          </motion.span>
          <motion.h2
            className="text-4xl md:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
          >
            Built for{' '}
            <span className="bg-gradient-to-r from-[#00E7FF] to-[#7C3AED] bg-clip-text text-transparent">
              real incidents
            </span>
          </motion.h2>
          <motion.p
            className="text-white/50 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.2 }}
          >
            Everything you need to automate incident investigation and resolution
          </motion.p>
        </div>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-6">
          {FEATURES_HIGHLIGHT.map((feature, i) => (
            <motion.div
              key={feature.title}
              className="relative p-6 rounded-2xl bg-gradient-to-b from-white/[0.05] to-transparent border border-white/[0.08] hover:border-white/[0.15] transition-all duration-300 group"
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1 }}
            >
              <div
                className="w-12 h-12 rounded-xl flex items-center justify-center mb-4"
                style={{ background: `${feature.color}15` }}
              >
                <feature.icon className="w-6 h-6" style={{ color: feature.color }} />
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">{feature.title}</h3>
              <p className="text-white/50 text-sm leading-relaxed">{feature.description}</p>
            </motion.div>
          ))}
        </div>

        <motion.div
          className="mt-20 text-center"
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.5 }}
        >
          <p className="text-white/30 text-sm mb-6">
            Open source and ready for your infrastructure
          </p>
          <a
            href="https://github.com/ankit25bcs10610/DevOps-Copilot"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-white/[0.05] border border-white/[0.1] text-white/70 hover:text-white hover:bg-white/[0.1] transition-all"
          >
            <GitBranch className="w-4 h-4" />
            View on GitHub
          </a>
        </motion.div>
      </div>
    </section>
  )
}
