import { motion } from 'framer-motion'
import { Zap, Brain, Users, ArrowRight } from 'lucide-react'

const CARDS = [
  {
    number: '01',
    icon: Brain,
    title: 'AI Agents for Production',
    description:
      'Autonomous agents that understand your infrastructure, investigate incidents, and propose fixes with full context awareness.',
    highlight: '5x faster MTTR',
    color: '#00E7FF',
  },
  {
    number: '02',
    icon: Zap,
    title: 'Deep Root Cause Analysis',
    description:
      'Go beyond symptoms to find the actual cause. Our AI correlates logs, metrics, traces, and changes to pinpoint issues.',
    highlight: '90% accuracy',
    color: '#7C3AED',
  },
  {
    number: '03',
    icon: Users,
    title: 'Human-in-the-Loop Safety',
    description:
      'Every action requires your approval. The AI proposes, you decide. Full audit trail and confidence scoring for every recommendation.',
    highlight: 'Zero auto-deploy',
    color: '#00FFB3',
  },
]

export function WhySection() {
  return (
    <section className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-[#030508]" />

      <div className="max-w-7xl mx-auto px-6 relative z-10">
        <div className="text-center mb-20">
          <motion.span
            className="inline-block text-[#00E7FF] text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            Why DevOps Copilot
          </motion.span>
          <motion.h2
            className="text-4xl md:text-5xl lg:text-6xl font-bold text-white mb-6"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
          >
            AI for{' '}
            <span className="bg-gradient-to-r from-[#00E7FF] via-[#7C3AED] to-[#00FFB3] bg-clip-text text-transparent">
              production
            </span>
          </motion.h2>
          <motion.p
            className="text-white/50 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.2 }}
          >
            Engineers get back to building. Let AI handle the investigation.
          </motion.p>
        </div>

        <div className="grid md:grid-cols-3 gap-6">
          {CARDS.map((card, i) => (
            <motion.div
              key={card.title}
              className="group relative"
              initial={{ opacity: 0, y: 40 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.15 }}
            >
              <div className="relative h-full p-8 rounded-2xl bg-gradient-to-b from-white/[0.05] to-transparent border border-white/[0.08] hover:border-white/[0.15] transition-all duration-500 overflow-hidden">
                <div
                  className="absolute top-0 left-0 right-0 h-1 opacity-60"
                  style={{ background: `linear-gradient(90deg, ${card.color}, transparent)` }}
                />

                <div className="absolute top-6 right-6 text-white/10 text-6xl font-bold">
                  {card.number}
                </div>

                <div
                  className="w-14 h-14 rounded-xl flex items-center justify-center mb-6"
                  style={{ background: `${card.color}15` }}
                >
                  <card.icon className="w-7 h-7" style={{ color: card.color }} />
                </div>

                <h3 className="text-xl font-bold text-white mb-3">{card.title}</h3>
                <p className="text-white/50 text-sm leading-relaxed mb-6">{card.description}</p>

                <div className="flex items-center gap-2">
                  <span
                    className="px-3 py-1 rounded-full text-xs font-semibold"
                    style={{
                      background: `${card.color}15`,
                      color: card.color,
                    }}
                  >
                    {card.highlight}
                  </span>
                </div>

                <div className="absolute bottom-0 left-0 right-0 h-32 bg-gradient-to-t from-[#030508]/80 to-transparent pointer-events-none" />
              </div>
            </motion.div>
          ))}
        </div>

        <motion.div
          className="text-center mt-16"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ delay: 0.5 }}
        >
          <a
            href="#features"
            className="inline-flex items-center gap-2 text-[#00E7FF] hover:text-white font-medium transition-colors group"
          >
            See all capabilities
            <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
          </a>
        </motion.div>
      </div>
    </section>
  )
}
