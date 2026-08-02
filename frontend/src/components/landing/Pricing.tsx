import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Check, Sparkles, Zap, Rocket, Crown, Star, Shield, Loader2 } from 'lucide-react';
import * as billingApi from '../../api/billing';
import type { PricingPlan, BillingCycle } from '../../types/billing';

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

const FALLBACK_PLANS: PricingPlan[] = [
  {
    name: 'starter',
    display_name: 'Starter',
    description: 'Perfect for individual developers and small projects',
    tier: 'starter',
    monthly_price: 0,
    annual_price: 0,
    annual_monthly_price: 0,
    annual_savings_percent: 0,
    max_users: 1,
    max_ai_investigations_monthly: 25,
    max_alerts_monthly: 100,
    features: {
      ai_investigation: true,
      slack_integration: true,
      basic_deduplication: true,
      github_integration: false,
      multi_llm: false,
      auto_fix_pr: false,
      ai_postmortem: false,
      similar_incident_search: false,
      advanced_deduplication: false,
      mtta_mttr_dashboard: false,
      api_access: false,
      limit_history_days: 14,
      limit_llm_providers: 1,
      limit_integrations: 1,
    },
    is_popular: false,
  },
  {
    name: 'team',
    display_name: 'Team',
    description: 'For growing teams that need more power',
    tier: 'team',
    monthly_price: 299,
    annual_price: 3048,
    annual_monthly_price: 254,
    annual_savings_percent: 15,
    max_users: 10,
    max_ai_investigations_monthly: 500,
    max_alerts_monthly: 5000,
    features: {
      ai_investigation: true,
      slack_integration: true,
      basic_deduplication: true,
      github_integration: true,
      multi_llm: true,
      auto_fix_pr: true,
      ai_postmortem: true,
      similar_incident_search: true,
      advanced_deduplication: false,
      mtta_mttr_dashboard: false,
      api_access: false,
      limit_history_days: 365,
      limit_llm_providers: 5,
      limit_integrations: 15,
    },
    is_popular: true,
  },
  {
    name: 'growth',
    display_name: 'Growth',
    description: 'For scaling organizations with advanced needs',
    tier: 'growth',
    monthly_price: 699,
    annual_price: 7128,
    annual_monthly_price: 594,
    annual_savings_percent: 15,
    max_users: 25,
    max_ai_investigations_monthly: null,
    max_alerts_monthly: null,
    features: {
      ai_investigation: true,
      slack_integration: true,
      basic_deduplication: true,
      github_integration: true,
      multi_llm: true,
      auto_fix_pr: true,
      ai_postmortem: true,
      similar_incident_search: true,
      advanced_deduplication: true,
      mtta_mttr_dashboard: true,
      api_access: true,
      cascade_detection: true,
      service_dependency_map: true,
      pattern_detection: true,
      limit_history_days: 730,
      limit_llm_providers: 5,
      limit_integrations: null,
    },
    is_popular: false,
  },
  {
    name: 'business',
    display_name: 'Business',
    description: 'For mid-size organizations with compliance needs',
    tier: 'business',
    monthly_price: 1499,
    annual_price: 15288,
    annual_monthly_price: 1274,
    annual_savings_percent: 15,
    max_users: 50,
    max_ai_investigations_monthly: null,
    max_alerts_monthly: null,
    features: {
      ai_investigation: true,
      slack_integration: true,
      basic_deduplication: true,
      github_integration: true,
      multi_llm: true,
      auto_fix_pr: true,
      ai_postmortem: true,
      similar_incident_search: true,
      advanced_deduplication: true,
      mtta_mttr_dashboard: true,
      api_access: true,
      cascade_detection: true,
      service_dependency_map: true,
      pattern_detection: true,
      sso_saml: true,
      rbac: true,
      audit_logs: true,
      priority_support: true,
      limit_history_days: null,
      limit_llm_providers: 5,
      limit_integrations: null,
    },
    is_popular: false,
  },
  {
    name: 'scale',
    display_name: 'Scale',
    description: 'For larger organizations scaling their AI operations',
    tier: 'scale',
    monthly_price: 2499,
    annual_price: 25488,
    annual_monthly_price: 2124,
    annual_savings_percent: 15,
    max_users: 100,
    max_ai_investigations_monthly: null,
    max_alerts_monthly: null,
    features: {
      ai_investigation: true,
      slack_integration: true,
      basic_deduplication: true,
      github_integration: true,
      multi_llm: true,
      auto_fix_pr: true,
      ai_postmortem: true,
      similar_incident_search: true,
      advanced_deduplication: true,
      mtta_mttr_dashboard: true,
      api_access: true,
      cascade_detection: true,
      service_dependency_map: true,
      pattern_detection: true,
      sso_saml: true,
      rbac: true,
      audit_logs: true,
      priority_support: true,
      self_hosted: true,
      dedicated_support_manager: true,
      limit_history_days: null,
      limit_llm_providers: null,
      limit_integrations: null,
    },
    is_popular: false,
  },
  {
    name: 'enterprise',
    display_name: 'Enterprise',
    description: 'Custom solutions for large organizations',
    tier: 'enterprise',
    monthly_price: 0,
    annual_price: 0,
    annual_monthly_price: 0,
    annual_savings_percent: 0,
    max_users: 999999,
    max_ai_investigations_monthly: null,
    max_alerts_monthly: null,
    features: {
      ai_investigation: true,
      slack_integration: true,
      basic_deduplication: true,
      github_integration: true,
      multi_llm: true,
      auto_fix_pr: true,
      ai_postmortem: true,
      similar_incident_search: true,
      advanced_deduplication: true,
      mtta_mttr_dashboard: true,
      api_access: true,
      cascade_detection: true,
      service_dependency_map: true,
      pattern_detection: true,
      sso_saml: true,
      rbac: true,
      audit_logs: true,
      priority_support: true,
      self_hosted: true,
      dedicated_support_manager: true,
      dedicated_infrastructure: true,
      hipaa_compliance: true,
      custom_sla: true,
      phone_support_24_7: true,
      limit_history_days: null,
      limit_llm_providers: null,
      limit_integrations: null,
    },
    is_popular: false,
  },
];

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
    plan.max_users === -1 ? 'Unlimited users' : `${plan.max_users} users`,
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
  const [actionLoading, setActionLoading] = useState(false);

  useEffect(() => {
    billingApi
      .listPlans(true)
      .then((data) => {
        if (data && data.length > 0) {
          setPlans(
            data.map((p) => ({
              ...p,
              is_popular: p.tier === 'team',
            }))
          );
        } else {
          setPlans(FALLBACK_PLANS);
        }
      })
      .catch(() => {
        // Use fallback plans when API is unavailable
        setPlans(FALLBACK_PLANS);
      })
      .finally(() => setLoading(false));
  }, []);

  const handlePlanSelect = async (plan: PricingPlan) => {
    if (plan.tier === 'enterprise') {
      window.open('mailto:sales@omnipotent.ai?subject=Enterprise%20Inquiry', '_blank');
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
              <span className="ml-1 text-success">(Save 15%)</span>
            </span>
          </motion.div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="w-8 h-8 text-primary animate-spin" />
          </div>
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
            <a href="mailto:support@omnipotent.ai" className="text-primary hover:underline">
              contact us
            </a>
          </p>
        </motion.div>
      </div>
    </section>
  );
}
