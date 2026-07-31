import { motion } from 'framer-motion'

const INTEGRATIONS = [
  { name: 'Kubernetes', logo: 'https://cdn.simpleicons.org/kubernetes/326CE5' },
  { name: 'Docker', logo: 'https://cdn.simpleicons.org/docker/2496ED' },
  { name: 'Terraform', logo: 'https://cdn.simpleicons.org/terraform/844FBA' },
  { name: 'GitHub', logo: 'https://cdn.simpleicons.org/github/FFFFFF' },
  { name: 'Datadog', logo: 'https://cdn.simpleicons.org/datadog/632CA6' },
  { name: 'PagerDuty', logo: 'https://cdn.simpleicons.org/pagerduty/06AC38' },
  { name: 'Prometheus', logo: 'https://cdn.simpleicons.org/prometheus/E6522C' },
  { name: 'Grafana', logo: 'https://cdn.simpleicons.org/grafana/F46800' },
  { name: 'Sentry', logo: 'https://cdn.simpleicons.org/sentry/362D59' },
  { name: 'AWS', logo: 'https://cdn.simpleicons.org/amazonaws/FF9900' },
  { name: 'ArgoCD', logo: 'https://cdn.simpleicons.org/argo/EF7B4D' },
  { name: 'Slack', logo: 'https://cdn.simpleicons.org/slack/4A154B' },
]

export function LogoCarousel() {
  return (
    <section className="relative py-16 overflow-hidden border-y border-white/[0.06] bg-[#030508]">
      <div className="max-w-7xl mx-auto px-6 mb-8">
        <motion.p
          className="text-center text-white/40 text-sm uppercase tracking-[0.2em] font-medium"
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
        >
          Integrates with your stack
        </motion.p>
      </div>

      <div className="relative">
        <div className="absolute left-0 top-0 bottom-0 w-32 bg-gradient-to-r from-[#030508] to-transparent z-10" />
        <div className="absolute right-0 top-0 bottom-0 w-32 bg-gradient-to-l from-[#030508] to-transparent z-10" />

        <div className="flex overflow-hidden">
          <motion.div
            className="flex gap-12 py-4"
            animate={{ x: ['0%', '-50%'] }}
            transition={{
              duration: 30,
              ease: 'linear',
              repeat: Infinity,
            }}
          >
            {[...INTEGRATIONS, ...INTEGRATIONS].map((integration, i) => (
              <div
                key={`${integration.name}-${i}`}
                className="flex items-center gap-3 px-6 py-3 rounded-xl bg-white/[0.02] border border-white/[0.05] hover:bg-white/[0.05] hover:border-white/[0.1] transition-all duration-300 flex-shrink-0 group"
              >
                <img
                  src={integration.logo}
                  alt={integration.name}
                  className="w-6 h-6 opacity-60 group-hover:opacity-100 transition-opacity"
                  loading="lazy"
                />
                <span className="text-white/50 group-hover:text-white/80 text-sm font-medium whitespace-nowrap transition-colors">
                  {integration.name}
                </span>
              </div>
            ))}
          </motion.div>
        </div>
      </div>
    </section>
  )
}
