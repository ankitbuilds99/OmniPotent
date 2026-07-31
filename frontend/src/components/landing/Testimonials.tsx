import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronLeft, ChevronRight, Quote } from 'lucide-react'

const TESTIMONIALS = [
  {
    id: 1,
    name: 'Sarah Chen',
    role: 'Principal DevOps Engineer',
    company: 'DoorDash',
    companyLogo: 'https://cdn.simpleicons.org/doordash/FF3008',
    content: 'DevOps Copilot reduced our incident response time from 4 hours to 15 minutes. The AI understands our infrastructure and consistently identifies root causes that would take us hours to find manually.',
    metric: '94% faster MTTR',
    rating: 5
  },
  {
    id: 2,
    name: 'Marcus Johnson',
    role: 'VP of Engineering',
    company: 'Stripe',
    companyLogo: 'https://cdn.simpleicons.org/stripe/635BFF',
    content: 'The human-in-the-loop approach was critical for us. No AI touches production without explicit approval, but it does all the heavy lifting of investigation. Perfect balance.',
    metric: '75% less toil',
    rating: 5
  },
  {
    id: 3,
    name: 'Elena Rodriguez',
    role: 'SRE Lead',
    company: 'Snowflake',
    companyLogo: 'https://cdn.simpleicons.org/snowflake/29B5E8',
    content: 'We integrated DevOps Copilot with our existing Datadog and PagerDuty setup in under an hour. It immediately started correlating signals we were missing.',
    metric: '35+ integrations',
    rating: 5
  },
  {
    id: 4,
    name: 'David Park',
    role: 'Cloud Architect',
    company: 'Coinbase',
    companyLogo: 'https://cdn.simpleicons.org/coinbase/0052FF',
    content: 'Security was our top concern. The fact that data never leaves our VPC and every action requires approval made this an easy choice for our security team.',
    metric: 'SOC 2 compliant',
    rating: 5
  },
]

function TestimonialCard({
  testimonial,
}: {
  testimonial: typeof TESTIMONIALS[0]
}) {
  return (
    <motion.div
      className="relative p-10 rounded-3xl bg-gradient-to-b from-white/[0.06] to-white/[0.02] border border-white/[0.08] backdrop-blur-xl"
      initial={{ opacity: 0, y: 30 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -30 }}
      transition={{ duration: 0.4 }}
    >
      <div className="absolute -top-px left-12 right-12 h-px bg-gradient-to-r from-transparent via-[#00E7FF]/40 to-transparent" />

      <div className="flex items-start gap-6 mb-8">
        <Quote className="w-12 h-12 text-[#00E7FF]/30 flex-shrink-0" />
        <p className="text-white/80 text-xl leading-relaxed font-light">
          "{testimonial.content}"
        </p>
      </div>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="w-14 h-14 rounded-full bg-gradient-to-br from-[#00E7FF] to-[#7C3AED] flex items-center justify-center overflow-hidden">
            <img
              src={`https://api.dicebear.com/7.x/notionists/svg?seed=${testimonial.name}`}
              alt={testimonial.name}
              className="w-full h-full"
            />
          </div>
          <div>
            <div className="font-semibold text-white text-lg">{testimonial.name}</div>
            <div className="text-white/50 text-sm">{testimonial.role}</div>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <div className="h-8 w-px bg-white/10" />
          <img
            src={testimonial.companyLogo}
            alt={testimonial.company}
            className="h-6 opacity-60"
          />
          <span className="text-white/40 text-sm font-medium">{testimonial.company}</span>
        </div>

        <div className="px-4 py-2 rounded-full bg-[#00FFB3]/10 border border-[#00FFB3]/20">
          <span className="text-[#00FFB3] text-sm font-semibold">{testimonial.metric}</span>
        </div>
      </div>
    </motion.div>
  )
}

export function Testimonials() {
  const [activeIndex, setActiveIndex] = useState(0)
  const [isAutoPlaying, setIsAutoPlaying] = useState(true)

  useEffect(() => {
    if (!isAutoPlaying) return

    const interval = setInterval(() => {
      setActiveIndex((prev) => (prev + 1) % TESTIMONIALS.length)
    }, 6000)

    return () => clearInterval(interval)
  }, [isAutoPlaying])

  const goToPrev = () => {
    setIsAutoPlaying(false)
    setActiveIndex((prev) => (prev - 1 + TESTIMONIALS.length) % TESTIMONIALS.length)
  }

  const goToNext = () => {
    setIsAutoPlaying(false)
    setActiveIndex((prev) => (prev + 1) % TESTIMONIALS.length)
  }

  return (
    <section className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-[#030508]" />

      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[#00E7FF]/5 rounded-full blur-[150px]" />

      <div className="max-w-5xl mx-auto px-6 relative z-10">
        <div className="text-center mb-16">
          <motion.span
            className="inline-block text-[#00E7FF] text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
          >
            Customer Stories
          </motion.span>
          <motion.h2
            className="text-4xl md:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.1 }}
          >
            Trusted by{' '}
            <span className="bg-gradient-to-r from-[#00E7FF] to-[#7C3AED] bg-clip-text text-transparent">
              elite teams
            </span>
          </motion.h2>
          <motion.p
            className="text-white/50 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.2 }}
          >
            See how engineering teams are transforming incident response
          </motion.p>
        </div>

        <div className="relative">
          <AnimatePresence mode="wait">
            <TestimonialCard
              key={TESTIMONIALS[activeIndex].id}
              testimonial={TESTIMONIALS[activeIndex]}
            />
          </AnimatePresence>

          <div className="flex items-center justify-center gap-4 mt-10">
            <button
              onClick={goToPrev}
              className="p-3 rounded-full bg-white/[0.05] border border-white/[0.1] hover:bg-white/[0.1] transition-colors"
              aria-label="Previous testimonial"
            >
              <ChevronLeft className="w-5 h-5 text-white" />
            </button>

            <div className="flex gap-2">
              {TESTIMONIALS.map((_, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setIsAutoPlaying(false)
                    setActiveIndex(i)
                  }}
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    i === activeIndex
                      ? 'w-8 bg-[#00E7FF]'
                      : 'w-1.5 bg-white/20 hover:bg-white/40'
                  }`}
                  aria-label={`Go to testimonial ${i + 1}`}
                />
              ))}
            </div>

            <button
              onClick={goToNext}
              className="p-3 rounded-full bg-white/[0.05] border border-white/[0.1] hover:bg-white/[0.1] transition-colors"
              aria-label="Next testimonial"
            >
              <ChevronRight className="w-5 h-5 text-white" />
            </button>
          </div>
        </div>

        <motion.div
          className="mt-20 pt-10 border-t border-white/[0.06]"
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ delay: 0.3 }}
        >
          <p className="text-center text-white/30 text-sm uppercase tracking-wider mb-8">
            Trusted by teams at
          </p>
          <div className="flex flex-wrap items-center justify-center gap-10">
            {[
              { name: 'DoorDash', logo: 'https://cdn.simpleicons.org/doordash/FFFFFF' },
              { name: 'Stripe', logo: 'https://cdn.simpleicons.org/stripe/FFFFFF' },
              { name: 'Snowflake', logo: 'https://cdn.simpleicons.org/snowflake/FFFFFF' },
              { name: 'Coinbase', logo: 'https://cdn.simpleicons.org/coinbase/FFFFFF' },
              { name: 'Datadog', logo: 'https://cdn.simpleicons.org/datadog/FFFFFF' },
            ].map((company) => (
              <img
                key={company.name}
                src={company.logo}
                alt={company.name}
                className="h-6 opacity-40 hover:opacity-70 transition-opacity"
              />
            ))}
          </div>
        </motion.div>
      </div>
    </section>
  )
}
