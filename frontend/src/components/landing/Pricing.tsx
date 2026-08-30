import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Check, Sparkles, Zap, Rocket, Crown, Star, Shield, Loader2, Mail } from 'lucide-react';
import * as billingApi from '../../api/billing';
import type { PricingPlan, BillingCycle } from '../../types/billing';
import { bestAnnualSavingsPercent, describeUserLimit } from '../../lib/planLimits';
import { CONTACT_EMAIL } from '../../config';

const TIER_ICONS: Record<string, typeof Sparkles> = {
  starter: Sparkles,
  team: Zap,
  growth: Rocket,
  business: Star,
  scale: Crown,
  enterprise: Shield,
};

const TIER_COLORS: Record<string, string> = {
  starter: '#00E7FF',
  team: '#7C3AED',
  growth: '#00FFB3',
  business: '#FF6B6B',
  scale: '#FFD93D',
  enterprise: '#E91E63',
};

/*
 * FALLBACK_PLANS used to sit here: six fully-invented plans ($299 Team, $699
 * Growth, $1499 Business, $2499 Scale, complete with seat caps and feature
 * matrices) rendered silently whenever GET /billing/plans failed. A visitor
 * had no way to tell a real price list from one the frontend made up on the
 * spot. Deleted in the de-faking pass — when the API cannot answer, the
 * section now says so and offers the contact address instead of a fiction.
 */

function PricingCard({
  plan,
  index,
  isYearly,
  onLaunch,
  loading,
}: {
  plan: PricingPlan;
  index: number;
  isYearly: boolean;
  onLaunch?: (plan: PricingPlan) => void;
  loading?: boolean;
}) {
  const Icon = TIER_ICONS[plan.tier] || Sparkles;
  const color = TIER_COLORS[plan.tier] || '#00E7FF';
  const isEnterprise = plan.tier === 'enterprise';
  const displayPrice = isYearly ? plan.annual_monthly_price : plan.monthly_price;

  const featureList = [
    describeUserLimit(plan.max_users),
    plan.max_ai_investigations_monthly === null
      ? 'Unlimited AI investigations'
      : `${plan.max_ai_investigations_monthly} AI investigations/mo`,
    plan.features.github_integration && 'GitHub integration',
    plan.features.slack_integration && 'Slack integration',
    plan.features.multi_llm && 'Multi-LLM support',
    plan.features.auto_fix_pr && 'Auto-fix PRs',
    plan.features.ai_postmortem && 'AI postmortems',
    plan.features.similar_incident_search && 'Similar incident search',
    plan.features.api_access && 'API access',
    plan.features.sso_saml && 'SSO/SAML',
    plan.features.priority_support && 'Priority support',
  ].filter(Boolean) as string[];

  return (
    <motion.div
      className={`
        relative floating-card p-8 flex flex-col
        ${plan.is_popular ? 'border-primary/50' : ''}
      `}
      initial={{ opacity: 0, y: 50 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
      whileHover={{ y: -10 }}
    >
      {plan.is_popular && (
        <div className="absolute -top-4 left-1/2 -translate-x-1/2">
          <span className="px-4 py-1 bg-gradient-to-r from-primary to-accent text-background text-sm font-semibold rounded-full">
            Most Popular
          </span>
        </div>
      )}

      <div
        className="w-14 h-14 rounded-2xl flex items-center justify-center mb-6"
        style={{ backgroundColor: `${color}15` }}
      >
        <Icon className="w-7 h-7" style={{ color }} />
      </div>

      <h3 className="font-display text-2xl font-bold text-white mb-2">
        {plan.display_name}
      </h3>

      <p className="text-white/60 text-sm mb-6">
        {plan.description}
      </p>

      <div className="mb-8">
        {isEnterprise ? (
          <div className="font-display text-3xl font-bold text-white">
            Custom
          </div>
        ) : (
          <div className="flex items-baseline gap-1">
            <span className="font-display text-5xl font-bold text-white">
              ${displayPrice.toFixed(0)}
            </span>
            <span className="text-white/60">/mo</span>
          </div>
        )}
        {isYearly && !isEnterprise && plan.annual_savings_percent > 0 && (
          <p className="text-success text-sm mt-1">
            Save {plan.annual_savings_percent.toFixed(0)}% with annual billing
          </p>
        )}
      </div>

      <ul className="space-y-3 mb-8 flex-grow">
        {featureList.slice(0, 8).map((feature) => (
          <li key={feature} className="flex items-start gap-3">
            <Check className="w-5 h-5 text-success flex-shrink-0 mt-0.5" />
            <span className="text-white/80 text-sm">{feature}</span>
          </li>
        ))}
        {featureList.length > 8 && (
          <li className="text-white/50 text-sm pl-8">
            +{featureList.length - 8} more features
          </li>
        )}
      </ul>

      <motion.button
        className={`
          w-full py-4 rounded-xl font-display font-semibold transition-all duration-300 cursor-pointer
          ${plan.is_popular
            ? 'bg-gradient-to-r from-primary to-accent text-background shadow-glow-primary'
            : 'bg-glass border border-glass-border text-white hover:border-primary'
          }
        `}
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={() => onLaunch?.(plan)}
        disabled={loading}
      >
        {loading ? (
          <Loader2 className="w-5 h-5 animate-spin mx-auto" />
        ) : isEnterprise ? (
          'Contact Sales'
        ) : plan.monthly_price === 0 ? (
          'Get Started Free'
        ) : (
          'Start Free Trial'
        )}
      </motion.button>

      <div
        className="absolute inset-0 rounded-[20px] opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
        style={{
          background: `radial-gradient(circle at 50% 50%, ${color}10, transparent 70%)`,
        }}
      />
    </motion.div>
  );
}

export function Pricing({ onLaunch }: { onLaunch?: () => void }) {
  const [isYearly, setIsYearly] = useState(false);
  const [plans, setPlans] = useState<PricingPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [unavailable, setUnavailable] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    billingApi
      .listPlans(true)
      .then((data) => {
        if (data && data.length > 0) {
          setPlans(
            data.map((p) => ({
              ...p,
              // Honor the API's own flag. It used to be overwritten with
              // `p.tier === 'team'` unconditionally, so the backend could
              // never move the "Most Popular" badge; the heuristic now only
              // covers responses that omit the field.
              is_popular: p.is_popular ?? p.tier === 'team',
            }))
          );
        } else {
          setUnavailable(true);
        }
      })
      .catch(() => {
        // No invented FALLBACK_PLANS anymore — an honest "unavailable" state
        // renders instead of prices the backend never quoted.
        setUnavailable(true);
      })
      .finally(() => setLoading(false));
  }, []);

  // Read off the plans actually on screen; with none loaded this is 0 and the
  // "Save up to N%" badge stays hidden.
  const savingsPercent = bestAnnualSavingsPercent(plans);

  const handlePlanSelect = async (plan: PricingPlan) => {
    if (plan.tier === 'enterprise') {
      window.open(`mailto:${CONTACT_EMAIL}?subject=Enterprise%20Inquiry`, '_blank');
      return;
    }

    if (plan.monthly_price === 0) {
      onLaunch?.();
      return;
    }

    setActionLoading(true);
    try {
      const billingCycle: BillingCycle = isYearly ? 'annual' : 'monthly';
      const result = await billingApi.createCheckout(
        plan.name,
        billingCycle,
        undefined,
        window.location.origin + '/billing?success=true',
        window.location.origin + '/billing?canceled=true'
      );
      window.location.href = result.checkout_url;
    } catch (err) {
      console.error('Checkout failed:', err);
      onLaunch?.();
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <section id="pricing" className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-background to-background-secondary" />

      <div className="absolute top-1/3 -left-40 w-80 h-80 bg-primary/5 rounded-full blur-3xl" />
      <div className="absolute bottom-1/3 -right-40 w-80 h-80 bg-secondary/5 rounded-full blur-3xl" />

      <div className="container-wide relative z-10">
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

          <motion.div
            className="flex items-center justify-center gap-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.3 }}
          >
            <span
              className={`text-sm cursor-pointer ${!isYearly ? 'text-white' : 'text-white/50'}`}
              onClick={() => setIsYearly(false)}
            >
              Monthly
            </span>
            <button
              onClick={() => setIsYearly(!isYearly)}
              className={`
                relative w-14 h-7 rounded-full transition-colors duration-300 cursor-pointer
                ${isYearly ? 'bg-primary' : 'bg-glass-border'}
              `}
            >
              <motion.div
                className="absolute top-1 w-5 h-5 bg-white rounded-full"
                animate={{ left: isYearly ? 'calc(100% - 24px)' : '4px' }}
                transition={{ type: 'spring', stiffness: 500, damping: 30 }}
              />
            </button>
            <span
              className={`text-sm cursor-pointer ${isYearly ? 'text-white' : 'text-white/50'}`}
              onClick={() => setIsYearly(true)}
            >
              Yearly
              {savingsPercent > 0 && (
                <span className="ml-1 text-success">
                  (Save up to {savingsPercent.toFixed(0)}%)
                </span>
              )}
            </span>
          </motion.div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
          </div>
        ) : unavailable ? (
          <motion.div
            className="floating-card max-w-xl mx-auto p-10 text-center"
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            <div className="w-14 h-14 mx-auto rounded-2xl bg-primary/10 flex items-center justify-center mb-6">
              <Mail className="w-7 h-7 text-primary" />
            </div>
            <h3 className="font-display text-2xl font-bold text-white mb-3">
              Pricing is temporarily unavailable
            </h3>
            <p className="text-white/60 mb-8">
              We couldn't load the current plans. Reach out and we'll get back
              to you with pricing for your team.
            </p>
            <a
              href={`mailto:${CONTACT_EMAIL}?subject=OmniPotent%20Pricing%20Inquiry`}
              className="inline-block px-8 py-4 rounded-xl font-display font-semibold bg-gradient-to-r from-primary to-accent text-background shadow-glow-primary"
            >
              Contact Us
            </a>
          </motion.div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 max-w-6xl mx-auto">
            {plans.map((plan, i) => (
              <PricingCard
                key={plan.name}
                plan={plan}
                index={i}
                isYearly={isYearly}
                onLaunch={handlePlanSelect}
                loading={actionLoading}
              />
            ))}
          </div>
        )}

        <motion.div
          className="text-center mt-12"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.4 }}
        >
          <p className="text-white/60">
            Have questions?{' '}
            <a href="#docs" className="text-primary hover:underline">
              Check our docs
            </a>{' '}
            or{' '}
            <a href={`mailto:${CONTACT_EMAIL}`} className="text-primary hover:underline">
              contact us
            </a>
          </p>
        </motion.div>
      </div>
    </section>
  );
}
