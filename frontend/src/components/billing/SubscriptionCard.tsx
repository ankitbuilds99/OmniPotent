import { motion } from 'framer-motion';
import { Calendar, AlertTriangle, Clock, Zap, ExternalLink } from 'lucide-react';
import type { Subscription } from '../../types/billing';

interface SubscriptionCardProps {
  subscription: Subscription;
  onManage?: () => void;
  onCancel?: () => void;
  onReactivate?: () => void;
  loading?: boolean;
}

const STATUS_CONFIG: Record<string, { color: string; bgColor: string; label: string }> = {
  active: { color: 'text-green-400', bgColor: 'bg-green-400/10', label: 'Active' },
  trialing: { color: 'text-blue-400', bgColor: 'bg-blue-400/10', label: 'Trial' },
  past_due: { color: 'text-amber-400', bgColor: 'bg-amber-400/10', label: 'Past Due' },
  canceled: { color: 'text-red-400', bgColor: 'bg-red-400/10', label: 'Canceled' },
  incomplete: { color: 'text-amber-400', bgColor: 'bg-amber-400/10', label: 'Incomplete' },
  incomplete_expired: { color: 'text-red-400', bgColor: 'bg-red-400/10', label: 'Expired' },
  unpaid: { color: 'text-red-400', bgColor: 'bg-red-400/10', label: 'Unpaid' },
  paused: { color: 'text-slate-400', bgColor: 'bg-slate-400/10', label: 'Paused' },
};

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

export function SubscriptionCard({
  subscription,
  onManage,
  onCancel,
  onReactivate,
  loading,
}: SubscriptionCardProps) {
  const status = STATUS_CONFIG[subscription.status] || STATUS_CONFIG.active;
  const plan = subscription.plan;

  return (
    <motion.div
      className="bg-slate-800/50 rounded-2xl p-6 border border-slate-700/50"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h3 className="text-xl font-bold text-white">{plan.display_name}</h3>
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${status.color} ${status.bgColor}`}>
              {status.label}
            </span>
          </div>
          <p className="text-slate-400 text-sm">{plan.description}</p>
        </div>

        <div className="text-right">
          <div className="text-2xl font-bold text-white">
            ${subscription.billing_cycle === 'annual'
              ? plan.annual_monthly_price.toFixed(0)
              : plan.monthly_price.toFixed(0)
            }
            <span className="text-sm text-slate-400 font-normal">/mo</span>
          </div>
          <div className="text-xs text-slate-500 capitalize">
            Billed {subscription.billing_cycle}
          </div>
        </div>
      </div>

      {subscription.is_trialing && subscription.days_remaining_trial !== null && (
        <div className="flex items-center gap-2 p-3 bg-blue-400/10 rounded-lg mb-4">
          <Clock className="w-4 h-4 text-blue-400" />
          <span className="text-blue-400 text-sm">
            {subscription.days_remaining_trial} days left in trial
          </span>
        </div>
      )}

      {subscription.cancel_at_period_end && (
        <div className="flex items-center gap-2 p-3 bg-amber-400/10 rounded-lg mb-4">
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          <span className="text-amber-400 text-sm">
            Cancels at end of billing period
          </span>
        </div>
      )}

      {subscription.status === 'past_due' && (
        <div className="flex items-center gap-2 p-3 bg-red-400/10 rounded-lg mb-4">
          <AlertTriangle className="w-4 h-4 text-red-400" />
          <span className="text-red-400 text-sm">
            Payment failed. Please update your payment method.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-slate-700 rounded-lg flex items-center justify-center">
            <Calendar className="w-5 h-5 text-slate-400" />
          </div>
          <div>
            <div className="text-xs text-slate-500">Next billing date</div>
            <div className="text-sm text-white">
              {subscription.current_period_end
                ? formatDate(subscription.current_period_end)
                : 'N/A'
              }
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-slate-700 rounded-lg flex items-center justify-center">
            <Zap className="w-5 h-5 text-slate-400" />
          </div>
          <div>
            <div className="text-xs text-slate-500">Renews in</div>
            <div className="text-sm text-white">
              {subscription.days_until_renewal} days
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        {onManage && (
          <motion.button
            onClick={onManage}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-primary text-slate-900 rounded-lg font-medium hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            <ExternalLink className="w-4 h-4" />
            Manage Subscription
          </motion.button>
        )}

        {subscription.cancel_at_period_end && onReactivate ? (
          <motion.button
            onClick={onReactivate}
            disabled={loading}
            className="flex-1 py-2.5 bg-green-500 text-white rounded-lg font-medium hover:bg-green-600 transition-colors cursor-pointer disabled:opacity-50"
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            Reactivate
          </motion.button>
        ) : onCancel && subscription.status === 'active' && !subscription.cancel_at_period_end ? (
          <motion.button
            onClick={onCancel}
            disabled={loading}
            className="px-4 py-2.5 text-slate-400 hover:text-red-400 transition-colors cursor-pointer disabled:opacity-50"
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            Cancel
          </motion.button>
        ) : null}
      </div>
    </motion.div>
  );
}
