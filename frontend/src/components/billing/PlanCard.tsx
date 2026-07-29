import { motion } from 'framer-motion';
import { Check, Sparkles, Zap, Building2, Rocket, Crown, Star, Shield } from 'lucide-react';
import type { PricingPlan, BillingCycle } from '../../types/billing';

const TIER_ICONS: Record<string, typeof Sparkles> = {
  community: Sparkles,
  starter: Zap,
  team: Rocket,
  growth: Star,
  business: Building2,
  scale: Crown,
  enterprise: Shield,
};

const TIER_COLORS: Record<string, string> = {
  community: '#00E7FF',
  starter: '#7C3AED',
  team: '#00FFB3',
  growth: '#FF6B6B',
  business: '#FFD93D',
  scale: '#FF8C00',
  enterprise: '#E91E63',
};

interface PlanCardProps {
  plan: PricingPlan;
  billingCycle: BillingCycle;
  isCurrent?: boolean;
  onSelect?: (plan: PricingPlan) => void;
  onContact?: () => void;
  loading?: boolean;
  index?: number;
}

export function PlanCard({
  plan,
  billingCycle,
  isCurrent,
  onSelect,
  onContact,
  loading,
  index = 0,
}: PlanCardProps) {
  const Icon = TIER_ICONS[plan.tier] || Sparkles;
  const color = TIER_COLORS[plan.tier] || '#00E7FF';
  const isEnterprise = plan.tier === 'enterprise';
  const price = billingCycle === 'annual' ? plan.annual_monthly_price : plan.monthly_price;

  const featureList = [
    plan.max_users === -1 ? 'Unlimited users' : `${plan.max_users} users`,
    plan.max_ai_investigations_monthly === null
      ? 'Unlimited AI investigations'
      : `${plan.max_ai_investigations_monthly} AI investigations/mo`,
    plan.max_alerts_monthly === null
      ? 'Unlimited alerts'
      : `${plan.max_alerts_monthly} alerts/mo`,
    plan.features.github_integration && 'GitHub integration',
    plan.features.slack_integration && 'Slack integration',
    plan.features.multi_llm && 'Multi-LLM support',
    plan.features.auto_fix_pr && 'Auto-fix PRs',
    plan.features.ai_postmortem && 'AI postmortems',
    plan.features.similar_incident_search && 'Similar incident search',
    plan.features.advanced_deduplication && 'Advanced deduplication',
    plan.features.mtta_mttr_dashboard && 'MTTA/MTTR dashboard',
    plan.features.api_access && 'API access',
    plan.features.cascade_detection && 'Cascade detection',
    plan.features.pattern_detection && 'Pattern detection',
    plan.features.sso_saml && 'SSO/SAML',
    plan.features.rbac && 'Role-based access control',
    plan.features.audit_logs && 'Audit logs',
    plan.features.soc2_compliance && 'SOC2 compliance',
    plan.features.priority_support && 'Priority support',
    plan.features.self_hosted && 'Self-hosted option',
    plan.features.dedicated_infrastructure && 'Dedicated infrastructure',
    plan.features.hipaa_compliance && 'HIPAA compliance',
    plan.features.custom_sla && 'Custom SLA',
    plan.features.phone_support_24_7 && '24/7 phone support',
  ].filter(Boolean) as string[];

  return (
    <motion.div
      className={`
        relative rounded-2xl p-6 flex flex-col h-full
        bg-slate-900/50 backdrop-blur-sm border
        ${isCurrent ? 'border-primary ring-2 ring-primary/20' : 'border-slate-700/50'}
        ${plan.is_popular ? 'border-primary/50' : ''}
        transition-all duration-300 hover:border-slate-600
      `}
      initial={{ opacity: 0, y: 30 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.1 }}
      whileHover={{ y: -5 }}
    >
      {plan.is_popular && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <span className="px-4 py-1 bg-gradient-to-r from-primary to-accent text-slate-900 text-xs font-semibold rounded-full">
            Most Popular
          </span>
        </div>
      )}

      {isCurrent && (
        <div className="absolute -top-3 right-4">
          <span className="px-3 py-1 bg-green-500 text-white text-xs font-semibold rounded-full">
            Current Plan
          </span>
        </div>
      )}

      <div
        className="w-12 h-12 rounded-xl flex items-center justify-center mb-4"
        style={{ backgroundColor: `${color}15` }}
      >
        <Icon className="w-6 h-6" style={{ color }} />
      </div>

      <h3 className="text-xl font-bold text-white mb-1">{plan.display_name}</h3>
      <p className="text-slate-400 text-sm mb-4">{plan.description}</p>

      <div className="mb-6">
        {isEnterprise ? (
          <div className="text-3xl font-bold text-white">Custom</div>
        ) : (
          <div className="flex items-baseline gap-1">
            <span className="text-4xl font-bold text-white">
              ${price.toFixed(0)}
            </span>
            <span className="text-slate-400">/mo</span>
          </div>
        )}
        {billingCycle === 'annual' && !isEnterprise && plan.annual_savings_percent > 0 && (
          <p className="text-green-400 text-sm mt-1">
            Save {plan.annual_savings_percent.toFixed(0)}% with annual billing
          </p>
        )}
      </div>

      <ul className="space-y-2 mb-6 flex-grow">
        {featureList.slice(0, 10).map((feature) => (
          <li key={feature} className="flex items-start gap-2">
            <Check className="w-4 h-4 text-green-400 flex-shrink-0 mt-0.5" />
            <span className="text-slate-300 text-sm">{feature}</span>
          </li>
        ))}
        {featureList.length > 10 && (
          <li className="text-slate-400 text-sm pl-6">
            +{featureList.length - 10} more features
          </li>
        )}
      </ul>

      {isEnterprise ? (
        <motion.button
          onClick={onContact}
          disabled={loading}
          className="w-full py-3 rounded-xl font-semibold bg-gradient-to-r from-primary to-accent text-slate-900 hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          Contact Sales
        </motion.button>
      ) : isCurrent ? (
        <button
          disabled
          className="w-full py-3 rounded-xl font-semibold bg-slate-700 text-slate-400 cursor-not-allowed"
        >
          Current Plan
        </button>
      ) : (
        <motion.button
          onClick={() => onSelect?.(plan)}
          disabled={loading}
          className={`
            w-full py-3 rounded-xl font-semibold transition-all cursor-pointer disabled:opacity-50
            ${plan.is_popular
              ? 'bg-gradient-to-r from-primary to-accent text-slate-900 hover:opacity-90'
              : 'bg-slate-700 text-white hover:bg-slate-600'
            }
          `}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {loading ? 'Loading...' : plan.monthly_price === 0 ? 'Get Started Free' : 'Upgrade'}
        </motion.button>
      )}
    </motion.div>
  );
}
