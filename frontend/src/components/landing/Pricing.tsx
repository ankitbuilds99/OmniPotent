import { useState } from 'react'
import { motion } from 'framer-motion'
import { Check, Sparkles, Zap, Building } from 'lucide-react'

const PLANS = [
  {
    id: 'starter',
    name: 'Starter',
    icon: Sparkles,
    price: 0,
    period: 'forever',
    description: 'Perfect for individual developers and small projects',
    color: '#00E7FF',
    features: [
      '5 deployments per month',
      '1 cloud provider',
      'Basic AI assistance',
      'Community support',
      '1 team member',
      'Standard monitoring'
    ],
    cta: 'Get Started Free'
  },
  {
    id: 'professional',
    name: 'Professional',
    icon: Zap,
    price: 49,
    period: '/month',
    description: 'For growing teams that need more power',
    color: '#7C3AED',
    popular: true,
    features: [
      'Unlimited deployments',
      'All cloud providers',
      'Advanced AI automation',
      'Priority support',
      'Up to 10 team members',
      'Advanced monitoring & alerts',
      'Custom integrations',
      'API access'
    ],
    cta: 'Start 14-day Trial'
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    icon: Building,
    price: null,
    period: '',
    description: 'Custom solutions for large organizations',
    color: '#00FFB3',
    features: [
      'Everything in Professional',
      'Unlimited team members',
      'Dedicated infrastructure',
      'Custom SLAs',
      '24/7 premium support',
      'Advanced security controls',
      'Custom AI training',
      'On-premise deployment'
    ],
    cta: 'Contact Sales'
  }
]

function PricingCard({
  plan,
  index,
  isYearly,
  onLaunch
}: {
  plan: typeof PLANS[0]
  index: number
  isYearly: boolean
  onLaunch?: () => void
}) {
  const Icon = plan.icon
  const yearlyPrice = plan.price ? Math.round(plan.price * 10) : null
  const displayPrice = isYearly ? yearlyPrice : plan.price

  return (
    <motion.div
      className={`
        relative floating-card p-8 flex flex-col
        ${plan.popular ? 'border-primary/50' : ''}
      `}
      initial={{ opacity: 0, y: 50 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
      whileHover={{ y: -10 }}
    >
      {/* Popular badge */}
      {plan.popular && (
        <div className="absolute -top-4 left-1/2 -translate-x-1/2">
          <span className="px-4 py-1 bg-gradient-to-r from-primary to-accent text-background text-sm font-semibold rounded-full">
            Most Popular
          </span>
        </div>
      )}

      {/* Icon */}
      <div
        className="w-14 h-14 rounded-2xl flex items-center justify-center mb-6"
        style={{ backgroundColor: `${plan.color}15` }}
      >
        <Icon className="w-7 h-7" style={{ color: plan.color }} />
      </div>

      {/* Plan name */}
      <h3 className="font-display text-2xl font-bold text-white mb-2">
        {plan.name}
      </h3>

      {/* Description */}
      <p className="text-white/60 text-sm mb-6">
        {plan.description}
      </p>

      {/* Price */}
      <div className="mb-8">
        {displayPrice !== null ? (
          <div className="flex items-baseline gap-1">
            <span className="font-display text-5xl font-bold text-white">
              ${displayPrice}
            </span>
            <span className="text-white/60">
              {isYearly ? '/year' : plan.period}
            </span>
          </div>
        ) : (
          <div className="font-display text-3xl font-bold text-white">
            Custom
          </div>
        )}
        {isYearly && plan.price && (
          <p className="text-success text-sm mt-1">
            Save ${plan.price * 12 - (yearlyPrice ?? 0)}/year
          </p>
        )}
      </div>

      {/* Features */}
      <ul className="space-y-3 mb-8 flex-grow">
        {plan.features.map((feature) => (
          <li key={feature} className="flex items-start gap-3">
            <Check className="w-5 h-5 text-success flex-shrink-0 mt-0.5" />
            <span className="text-white/80 text-sm">{feature}</span>
          </li>
        ))}
      </ul>

      {/* CTA */}
      <motion.button
        className={`
          w-full py-4 rounded-xl font-display font-semibold transition-all duration-300 cursor-pointer
          ${plan.popular
            ? 'bg-gradient-to-r from-primary to-accent text-background shadow-glow-primary'
            : 'bg-glass border border-glass-border text-white hover:border-primary'
          }
        `}
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={() => onLaunch?.()}
      >
        {plan.cta}
      </motion.button>

      {/* Hover glow */}
      <div
        className="absolute inset-0 rounded-[20px] opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
        style={{
          background: `radial-gradient(circle at 50% 50%, ${plan.color}10, transparent 70%)`
        }}
      />
    </motion.div>
  )
}

export function Pricing({ onLaunch }: { onLaunch?: () => void }) {
  const [isYearly, setIsYearly] = useState(false)

  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background to-background-secondary" />

      {/* Decorative elements */}
      <div className="absolute top-1/3 -left-40 w-80 h-80 bg-primary/5 rounded-full blur-3xl" />
      <div className="absolute bottom-1/3 -right-40 w-80 h-80 bg-secondary/5 rounded-full blur-3xl" />

      <div className="container-wide relative z-10">
        {/* Section header */}
        <div className="text-center mb-12">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            Pricing
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Simple, <span className="gradient-text">transparent</span> pricing
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto mb-8"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Start free and scale as you grow. No hidden fees.
          </motion.p>

          {/* Billing toggle */}
          <motion.div
            className="flex items-center justify-center gap-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.3 }}
          >
            <span className={`text-sm ${!isYearly ? 'text-white' : 'text-white/50'}`}>
              Monthly
            </span>
            <button
              onClick={() => setIsYearly(!isYearly)}
              className={`
                relative w-14 h-7 rounded-full transition-colors duration-300
                ${isYearly ? 'bg-primary' : 'bg-glass-border'}
              `}
            >
              <motion.div
                className="absolute top-1 w-5 h-5 bg-white rounded-full"
                animate={{ left: isYearly ? 'calc(100% - 24px)' : '4px' }}
                transition={{ type: 'spring', stiffness: 500, damping: 30 }}
              />
            </button>
            <span className={`text-sm ${isYearly ? 'text-white' : 'text-white/50'}`}>
              Yearly
              <span className="ml-1 text-success">(Save 17%)</span>
            </span>
          </motion.div>
        </div>

        {/* Pricing cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 max-w-5xl mx-auto">
          {PLANS.map((plan, i) => (
            <PricingCard
              key={plan.id}
              plan={plan}
              index={i}
              isYearly={isYearly}
              onLaunch={onLaunch}
            />
          ))}
        </div>

        {/* FAQ teaser */}
        <motion.div
          className="text-center mt-12"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.4 }}
        >
          <p className="text-white/60">
            Have questions?{' '}
            <a href="#" className="text-primary hover:underline">
              Check our FAQ
            </a>{' '}
            or{' '}
            <a href="#" className="text-primary hover:underline">
              contact us
            </a>
          </p>
        </motion.div>
      </div>
    </section>
  )
}
