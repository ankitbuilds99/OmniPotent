import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronLeft, ChevronRight, Quote } from 'lucide-react'

const TESTIMONIALS = [
  {
    id: 1,
    name: 'Sarah Chen',
    role: 'Principal DevOps Engineer',
    company: 'TechCorp',
    avatar: 'SC',
    content: 'DevOps Copilot reduced our deployment time from hours to minutes. The AI-generated Terraform configs are production-ready out of the box.',
    rating: 5
  },
  {
    id: 2,
    name: 'Marcus Johnson',
    role: 'VP of Engineering',
    company: 'StartupXYZ',
    avatar: 'MJ',
    content: 'We went from manual kubectl commands to fully automated GitOps workflows. Our team can now focus on building features instead of managing infrastructure.',
    rating: 5
  },
  {
    id: 3,
    name: 'Elena Rodriguez',
    role: 'SRE Lead',
    company: 'FinanceHub',
    avatar: 'ER',
    content: 'The incident response automation is incredible. Mean time to resolution dropped by 70% after implementing DevOps Copilot.',
    rating: 5
  },
  {
    id: 4,
    name: 'David Park',
    role: 'Cloud Architect',
    company: 'Enterprise Co',
    avatar: 'DP',
    content: 'Multi-cloud management was a nightmare before. Now we have a unified view and AI-assisted optimization across AWS, Azure, and GCP.',
    rating: 5
  },
  {
    id: 5,
    name: 'Lisa Thompson',
    role: 'CTO',
    company: 'ScaleUp Inc',
    avatar: 'LT',
    content: 'The ROI was immediate. We saved $50k/month on cloud costs just from the AI recommendations in the first week.',
    rating: 5
  }
]

function TestimonialCard({
  testimonial,
  isActive
}: {
  testimonial: typeof TESTIMONIALS[0]
  isActive: boolean
}) {
  return (
    <motion.div
      className={`
        floating-card p-8 transition-all duration-500
        ${isActive ? 'scale-100 opacity-100' : 'scale-95 opacity-50'}
      `}
      initial={{ opacity: 0, y: 50 }}
      animate={{ opacity: isActive ? 1 : 0.5, y: 0 }}
      transition={{ duration: 0.5 }}
    >
      {/* Quote icon */}
      <div className="mb-6">
        <Quote className="w-10 h-10 text-primary/30" />
      </div>

      {/* Content */}
      <p className="text-white/80 text-lg leading-relaxed mb-8">
        "{testimonial.content}"
      </p>

      {/* Author */}
      <div className="flex items-center gap-4">
        {/* Avatar */}
        <div className="w-14 h-14 rounded-full bg-gradient-to-br from-primary to-secondary flex items-center justify-center text-background font-bold text-lg">
          {testimonial.avatar}
        </div>

        <div>
          <div className="font-display font-semibold text-white">
            {testimonial.name}
          </div>
          <div className="text-white/60 text-sm">
            {testimonial.role}
          </div>
          <div className="text-primary text-sm">
            {testimonial.company}
          </div>
        </div>

        {/* Rating */}
        <div className="ml-auto flex gap-1">
          {Array.from({ length: testimonial.rating }).map((_, i) => (
            <svg
              key={i}
              className="w-5 h-5 text-warning"
              fill="currentColor"
              viewBox="0 0 20 20"
            >
              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
          ))}
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
    }, 5000)

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
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background-secondary to-background" />

      {/* Decorative elements */}
      <div className="absolute top-1/4 left-0 w-64 h-64 bg-primary/5 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 right-0 w-64 h-64 bg-secondary/5 rounded-full blur-3xl" />

      <div className="container-wide relative z-10">
        {/* Section header */}
        <div className="text-center mb-16">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            Testimonials
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Loved by <span className="gradient-text">developers</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            See what engineering teams are saying about DevOps Copilot
          </motion.p>
        </div>

        {/* Carousel */}
        <div className="relative max-w-3xl mx-auto">
          <AnimatePresence mode="wait">
            <TestimonialCard
              key={TESTIMONIALS[activeIndex].id}
              testimonial={TESTIMONIALS[activeIndex]}
              isActive={true}
            />
          </AnimatePresence>

          {/* Navigation */}
          <div className="flex items-center justify-center gap-4 mt-8">
            <button
              onClick={goToPrev}
              className="p-3 rounded-full glass hover:bg-white/10 transition-colors"
            >
              <ChevronLeft className="w-5 h-5 text-white" />
            </button>

            {/* Dots */}
            <div className="flex gap-2">
              {TESTIMONIALS.map((_, i) => (
                <button
                  key={i}
                  onClick={() => {
                    setIsAutoPlaying(false)
                    setActiveIndex(i)
                  }}
                  className={`
                    w-2 h-2 rounded-full transition-all duration-300
                    ${i === activeIndex
                      ? 'w-8 bg-primary'
                      : 'bg-white/20 hover:bg-white/40'
                    }
                  `}
                />
              ))}
            </div>

            <button
              onClick={goToNext}
              className="p-3 rounded-full glass hover:bg-white/10 transition-colors"
            >
              <ChevronRight className="w-5 h-5 text-white" />
            </button>
          </div>
        </div>

        {/* Company logos */}
        <motion.div
          className="mt-16 flex flex-wrap items-center justify-center gap-8 opacity-40"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 0.4, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }}
        >
          {['TechCorp', 'StartupXYZ', 'FinanceHub', 'Enterprise Co', 'ScaleUp Inc'].map((company) => (
            <div
              key={company}
              className="text-white font-display font-bold text-xl"
            >
              {company}
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  )
}
