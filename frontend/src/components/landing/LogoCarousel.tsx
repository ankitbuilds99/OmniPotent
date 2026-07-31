import { motion } from 'framer-motion'

const COMPANIES = [
  { name: 'Coinbase', logo: 'https://cdn.simpleicons.org/coinbase/0052FF' },
  { name: 'DoorDash', logo: 'https://cdn.simpleicons.org/doordash/FF3008' },
  { name: 'Snowflake', logo: 'https://cdn.simpleicons.org/snowflake/29B5E8' },
  { name: 'Stripe', logo: 'https://cdn.simpleicons.org/stripe/635BFF' },
  { name: 'Datadog', logo: 'https://cdn.simpleicons.org/datadog/632CA6' },
  { name: 'PagerDuty', logo: 'https://cdn.simpleicons.org/pagerduty/06AC38' },
  { name: 'HashiCorp', logo: 'https://cdn.simpleicons.org/hashicorp/000000' },
  { name: 'Cloudflare', logo: 'https://cdn.simpleicons.org/cloudflare/F38020' },
  { name: 'Vercel', logo: 'https://cdn.simpleicons.org/vercel/FFFFFF' },
  { name: 'MongoDB', logo: 'https://cdn.simpleicons.org/mongodb/47A248' },
  { name: 'Redis', logo: 'https://cdn.simpleicons.org/redis/DC382D' },
  { name: 'Grafana', logo: 'https://cdn.simpleicons.org/grafana/F46800' },
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
          Trusted by engineering teams at
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
            {[...COMPANIES, ...COMPANIES].map((company, i) => (
              <div
                key={`${company.name}-${i}`}
                className="flex items-center gap-3 px-6 py-3 rounded-xl bg-white/[0.02] border border-white/[0.05] hover:bg-white/[0.05] hover:border-white/[0.1] transition-all duration-300 flex-shrink-0 group"
              >
                <img
                  src={company.logo}
                  alt={company.name}
                  className="w-6 h-6 opacity-60 group-hover:opacity-100 transition-opacity"
                  loading="lazy"
                />
                <span className="text-white/50 group-hover:text-white/80 text-sm font-medium whitespace-nowrap transition-colors">
                  {company.name}
                </span>
              </div>
            ))}
          </motion.div>
        </div>
      </div>
    </section>
  )
}
