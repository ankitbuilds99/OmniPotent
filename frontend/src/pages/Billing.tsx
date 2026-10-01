import { useState, useEffect, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  CreditCard,
  Receipt,
  BarChart3,
  Settings,
  TrendingUp,
  AlertCircle,
  Loader2,
  ExternalLink,
} from 'lucide-react';
import { PlanCard } from '../components/billing/PlanCard';
import { BillingToggle } from '../components/billing/BillingToggle';
import { UsageBar } from '../components/billing/UsageBar';
import { UsageChart } from '../components/billing/UsageChart';
import { InvoiceList } from '../components/billing/InvoiceList';
import { PaymentMethodCard } from '../components/billing/PaymentMethodCard';
import { SubscriptionCard } from '../components/billing/SubscriptionCard';
import { CancelModal } from '../components/billing/CancelModal';
import * as billingApi from '../api/billing';
import { bestAnnualSavingsPercent } from '../lib/planLimits';
import { CONTACT_EMAIL } from '../config';
import type {
  PricingPlan,
  Subscription,
  UsageData,
  UsageHistory,
  Invoice,
  PaymentMethod,
  BillingCycle,
} from '../types/billing';

type Tab = 'overview' | 'plans' | 'usage' | 'invoices' | 'settings';

type Settled<T> = { ok: true; value: T } | { ok: false; message: string };

/** Run a request whose failure should be reported on its own rather than
 *  bringing down the whole page load. */
async function settle<T>(promise: Promise<T>): Promise<Settled<T>> {
  try {
    return { ok: true, value: await promise };
  } catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : String(err) };
  }
}

interface BillingProps {
  onBack: () => void;
}

export function Billing({ onBack }: BillingProps) {
  const [tab, setTab] = useState<Tab>('overview');
  const [billingCycle, setBillingCycle] = useState<BillingCycle>('monthly');
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [subscriptionError, setSubscriptionError] = useState<string | null>(null);

  // Data states
  const [plans, setPlans] = useState<PricingPlan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [usageHistory, setUsageHistory] = useState<UsageHistory[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([]);

  // Modal states
  const [showCancelModal, setShowCancelModal] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      // The subscription is settled separately: an unreadable one must not
      // blank the rest of the page, but it must not be mistaken for "you have
      // no subscription" either — everything that keys off `subscription`
      // (the current-plan chip, the Current Plan badge, Cancel) would quietly
      // misdescribe the account.
      const [plansData, subResult, usageData, historyData, invoicesData, pmData] = await Promise.all([
        billingApi.listPlans(true),
        settle(billingApi.getSubscription()),
        billingApi.getUsage(),
        billingApi.getUsageHistory(12),
        billingApi.getInvoices(10),
        billingApi.getPaymentMethods(),
      ]);

      setPlans(plansData);
      setSubscription(subResult.ok ? subResult.value : null);
      setSubscriptionError(subResult.ok ? null : subResult.message);
      setUsage(usageData);
      setUsageHistory(historyData);
      setInvoices(invoicesData);
      setPaymentMethods(pmData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load billing data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // `setActionLoading(false)` belongs in `finally`, not only in `catch`. On the
  // success path these hand off to `window.location.href`, and a navigation that
  // does not actually leave the page — the URL the server hands back being the
  // one already open, say — leaves the spinner running with no way to clear it.
  // Resetting unconditionally costs nothing when the browser really does leave.
  const handleSelectPlan = async (plan: PricingPlan) => {
    setActionLoading(true);
    try {
      const result = await billingApi.createCheckout(
        plan.name,
        billingCycle,
        undefined,
        window.location.href + '?success=true',
        window.location.href + '?canceled=true'
      );
      window.location.href = result.checkout_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create checkout');
    } finally {
      setActionLoading(false);
    }
  };

  const handleManageSubscription = async () => {
    setActionLoading(true);
    try {
      const result = await billingApi.createPortalSession(window.location.href);
      window.location.href = result.portal_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to open portal');
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancelSubscription = async (reason: string, feedback: string, immediate: boolean) => {
    setActionLoading(true);
    try {
      const result = await billingApi.cancelSubscription(reason, immediate, feedback);
      setSubscription({ ...subscription!, ...result });
      setShowCancelModal(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel subscription');
    } finally {
      setActionLoading(false);
    }
  };

  const handleReactivate = async () => {
    setActionLoading(true);
    try {
      const result = await billingApi.reactivateSubscription();
      setSubscription({ ...subscription!, ...result });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reactivate subscription');
    } finally {
      setActionLoading(false);
    }
  };

  const tabs = [
    { id: 'overview', label: 'Overview', icon: BarChart3 },
    { id: 'plans', label: 'Plans', icon: CreditCard },
    { id: 'usage', label: 'Usage', icon: TrendingUp },
    { id: 'invoices', label: 'Invoices', icon: Receipt },
    { id: 'settings', label: 'Settings', icon: Settings },
  ] as const;

  if (loading) {
    return (
      <div className="min-h-screen omni-workspace-page omni-billing-page flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-primary animate-spin mx-auto mb-4" />
          <p className="text-slate-400">Loading billing data...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen omni-workspace-page omni-billing-page">
      {/* Header */}
      <header className="sticky top-0 z-40 bg-slate-950/80 backdrop-blur-xl border-b border-slate-800">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <button
                onClick={onBack}
                className="p-2 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
              >
                <ArrowLeft className="w-5 h-5 text-slate-400" />
              </button>
              <div>
                <h1 className="text-xl font-bold text-white">Billing & Subscription</h1>
                <p className="text-sm text-slate-400">Manage your plan, usage, and payment methods</p>
              </div>
            </div>

            {subscription && (
              <div className="flex items-center gap-2">
                <span className="text-slate-400 text-sm">Current plan:</span>
                <span className="px-3 py-1 bg-primary/10 text-primary rounded-full text-sm font-medium">
                  {subscription.plan.display_name}
                </span>
              </div>
            )}
          </div>

          {/* Tabs */}
          <nav className="flex gap-1 mt-4 -mb-px">
            {tabs.map((t) => {
              const Icon = t.icon;
              const isActive = tab === t.id;
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`
                    flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-t-lg transition-colors cursor-pointer
                    ${isActive
                      ? 'bg-slate-800 text-white'
                      : 'text-slate-400 hover:text-white hover:bg-slate-800/50'
                    }
                  `}
                >
                  <Icon className="w-4 h-4" />
                  {t.label}
                </button>
              );
            })}
          </nav>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div className="max-w-7xl mx-auto px-6 pt-4">
          <div className="flex items-center gap-3 p-4 bg-red-500/10 border border-red-500/20 rounded-lg">
            <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0" />
            <p className="text-red-400">{error}</p>
            <button
              onClick={() => setError(null)}
              className="ml-auto text-red-400 hover:text-red-300 cursor-pointer"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Whatever this page says about the current plan while this is set is a
          guess, so say so once, above every tab. */}
      {subscriptionError && (
        <div className="max-w-7xl mx-auto px-6 pt-4">
          <div
            role="alert"
            className="flex items-start gap-3 p-4 bg-amber-500/10 border border-amber-500/20 rounded-lg"
          >
            <AlertCircle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
            <p className="text-amber-400">
              Could not load your current subscription: {subscriptionError}. This page cannot
              show which plan you are on until it loads.{' '}
              <button onClick={loadData} className="underline cursor-pointer">
                Retry
              </button>
            </p>
          </div>
        </div>
      )}

      {/* Content */}
      <main className="max-w-7xl mx-auto px-6 py-8">
        {tab === 'overview' && (
          <div className="space-y-8">
            {/* Subscription Card */}
            {subscription && (
              <SubscriptionCard
                subscription={subscription}
                onManage={handleManageSubscription}
                onCancel={() => setShowCancelModal(true)}
                onReactivate={handleReactivate}
                loading={actionLoading}
              />
            )}

            {/* Usage Overview */}
            {usage && (
              <motion.div
                className="bg-slate-800/30 rounded-2xl p-6 border border-slate-700/50"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1 }}
              >
                <h2 className="text-lg font-semibold text-white mb-6">Current Usage</h2>

                <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                  <UsageBar
                    label="AI Investigations"
                    used={usage.limits.ai_investigations.used}
                    limit={usage.limits.ai_investigations.limit}
                    unlimited={usage.limits.ai_investigations.unlimited}
                    color="#00E7FF"
                  />
                  <UsageBar
                    label="Alerts Processed"
                    used={usage.limits.alerts.used}
                    limit={usage.limits.alerts.limit}
                    unlimited={usage.limits.alerts.unlimited}
                    color="#7C3AED"
                  />
                  <UsageBar
                    label="Team Members"
                    used={usage.limits.users.used}
                    limit={usage.limits.users.limit}
                    unlimited={usage.limits.users.unlimited}
                    color="#00FFB3"
                  />
                </div>
              </motion.div>
            )}

            {/* Quick Actions */}
            <motion.div
              className="grid gap-4 md:grid-cols-3"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
            >
              <button
                onClick={() => setTab('plans')}
                className="flex items-center gap-3 p-4 bg-slate-800/30 rounded-xl border border-slate-700/50 hover:border-primary/50 transition-colors text-left cursor-pointer"
              >
                <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center">
                  <CreditCard className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <div className="text-white font-medium">View Plans</div>
                  <div className="text-slate-400 text-sm">Compare and upgrade</div>
                </div>
              </button>

              <button
                onClick={() => setTab('usage')}
                className="flex items-center gap-3 p-4 bg-slate-800/30 rounded-xl border border-slate-700/50 hover:border-primary/50 transition-colors text-left cursor-pointer"
              >
                <div className="w-10 h-10 bg-green-500/10 rounded-lg flex items-center justify-center">
                  <TrendingUp className="w-5 h-5 text-green-400" />
                </div>
                <div>
                  <div className="text-white font-medium">Usage Analytics</div>
                  <div className="text-slate-400 text-sm">Track your consumption</div>
                </div>
              </button>

              <button
                onClick={handleManageSubscription}
                disabled={actionLoading}
                className="flex items-center gap-3 p-4 bg-slate-800/30 rounded-xl border border-slate-700/50 hover:border-primary/50 transition-colors text-left cursor-pointer disabled:opacity-50"
              >
                <div className="w-10 h-10 bg-purple-500/10 rounded-lg flex items-center justify-center">
                  <ExternalLink className="w-5 h-5 text-purple-400" />
                </div>
                <div>
                  <div className="text-white font-medium">Billing Portal</div>
                  <div className="text-slate-400 text-sm">Manage via Stripe</div>
                </div>
              </button>
            </motion.div>
          </div>
        )}

        {tab === 'plans' && (
          <div className="space-y-8">
            <div className="text-center">
              <h2 className="text-2xl font-bold text-white mb-2">Choose Your Plan</h2>
              <p className="text-slate-400 mb-6">
                Select the plan that best fits your team's needs
              </p>
              <BillingToggle
                value={billingCycle}
                onChange={setBillingCycle}
                savingsPercent={bestAnnualSavingsPercent(plans)}
              />
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {plans.map((plan, index) => (
                <PlanCard
                  key={plan.name}
                  plan={plan}
                  billingCycle={billingCycle}
                  isCurrent={subscription?.plan.name === plan.name}
                  onSelect={handleSelectPlan}
                  onContact={() => window.open(`mailto:${CONTACT_EMAIL}`, '_blank')}
                  loading={actionLoading}
                  index={index}
                />
              ))}
            </div>
          </div>
        )}

        {tab === 'usage' && (
          <div className="space-y-8">
            {/* Current Period Usage */}
            {usage && (
              <motion.div
                className="bg-slate-800/30 rounded-2xl p-6 border border-slate-700/50"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
              >
                <h2 className="text-lg font-semibold text-white mb-6">This Month's Usage</h2>

                <div className="grid gap-6 md:grid-cols-2">
                  <UsageBar
                    label="AI Investigations"
                    used={usage.limits.ai_investigations.used}
                    limit={usage.limits.ai_investigations.limit}
                    unlimited={usage.limits.ai_investigations.unlimited}
                    color="#00E7FF"
                  />
                  <UsageBar
                    label="Alerts Processed"
                    used={usage.limits.alerts.used}
                    limit={usage.limits.alerts.limit}
                    unlimited={usage.limits.alerts.unlimited}
                    color="#7C3AED"
                  />
                </div>
              </motion.div>
            )}

            {/* Historical Charts */}
            <motion.div
              className="bg-slate-800/30 rounded-2xl p-6 border border-slate-700/50"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
            >
              <h2 className="text-lg font-semibold text-white mb-6">Usage History</h2>

              <div className="grid gap-8 md:grid-cols-2">
                <UsageChart
                  data={usageHistory}
                  metric="ai_investigations"
                  label="AI Investigations"
                  color="#00E7FF"
                />
                <UsageChart
                  data={usageHistory}
                  metric="alerts_processed"
                  label="Alerts Processed"
                  color="#7C3AED"
                />
                <UsageChart
                  data={usageHistory}
                  metric="api_calls"
                  label="API Calls"
                  color="#00FFB3"
                />
                <UsageChart
                  data={usageHistory}
                  metric="postmortems_generated"
                  label="Postmortems Generated"
                  color="#FF6B6B"
                />
              </div>
            </motion.div>
          </div>
        )}

        {tab === 'invoices' && (
          <motion.div
            className="bg-slate-800/30 rounded-2xl p-6 border border-slate-700/50"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-lg font-semibold text-white">Invoice History</h2>
              <button
                onClick={handleManageSubscription}
                className="flex items-center gap-2 text-sm text-primary hover:underline cursor-pointer"
              >
                View all in Stripe
                <ExternalLink className="w-4 h-4" />
              </button>
            </div>

            <InvoiceList invoices={invoices} />
          </motion.div>
        )}

        {tab === 'settings' && (
          <div className="space-y-8">
            {/* Payment Methods */}
            <motion.div
              className="bg-slate-800/30 rounded-2xl p-6 border border-slate-700/50"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-lg font-semibold text-white">Payment Methods</h2>
                <button
                  onClick={handleManageSubscription}
                  className="flex items-center gap-2 text-sm text-primary hover:underline cursor-pointer"
                >
                  Add new
                  <ExternalLink className="w-4 h-4" />
                </button>
              </div>

              {paymentMethods.length > 0 ? (
                <div className="grid gap-4 md:grid-cols-2">
                  {paymentMethods.map((pm) => (
                    <PaymentMethodCard key={pm.id} method={pm} />
                  ))}
                </div>
              ) : (
                <div className="text-center py-8">
                  <CreditCard className="w-12 h-12 text-slate-600 mx-auto mb-3" />
                  <p className="text-slate-400">No payment methods on file</p>
                  <button
                    onClick={handleManageSubscription}
                    className="mt-4 px-4 py-2 bg-primary text-slate-900 rounded-lg font-medium hover:opacity-90 transition-opacity cursor-pointer"
                  >
                    Add Payment Method
                  </button>
                </div>
              )}
            </motion.div>

            {/* Billing Settings */}
            <motion.div
              className="bg-slate-800/30 rounded-2xl p-6 border border-slate-700/50"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 }}
            >
              <h2 className="text-lg font-semibold text-white mb-6">Billing Settings</h2>

              <div className="space-y-4">
                <button
                  onClick={handleManageSubscription}
                  disabled={actionLoading}
                  className="w-full flex items-center justify-between p-4 bg-slate-800/50 rounded-lg hover:bg-slate-800 transition-colors cursor-pointer disabled:opacity-50"
                >
                  <div>
                    <div className="text-white font-medium">Update billing information</div>
                    <div className="text-slate-400 text-sm">Change your billing address and details</div>
                  </div>
                  <ExternalLink className="w-5 h-5 text-slate-400" />
                </button>

                <button
                  onClick={handleManageSubscription}
                  disabled={actionLoading}
                  className="w-full flex items-center justify-between p-4 bg-slate-800/50 rounded-lg hover:bg-slate-800 transition-colors cursor-pointer disabled:opacity-50"
                >
                  <div>
                    <div className="text-white font-medium">Change billing email</div>
                    <div className="text-slate-400 text-sm">Update where invoices are sent</div>
                  </div>
                  <ExternalLink className="w-5 h-5 text-slate-400" />
                </button>

                {subscription && subscription.status === 'active' && !subscription.cancel_at_period_end && (
                  <button
                    onClick={() => setShowCancelModal(true)}
                    className="w-full flex items-center justify-between p-4 bg-red-500/10 rounded-lg hover:bg-red-500/20 transition-colors cursor-pointer"
                  >
                    <div>
                      <div className="text-red-400 font-medium">Cancel subscription</div>
                      <div className="text-red-400/60 text-sm">Your access will continue until the end of the billing period</div>
                    </div>
                  </button>
                )}
              </div>
            </motion.div>
          </div>
        )}
      </main>

      {/* Cancel Modal */}
      {subscription && (
        <CancelModal
          isOpen={showCancelModal}
          onClose={() => setShowCancelModal(false)}
          onConfirm={handleCancelSubscription}
          loading={actionLoading}
          planName={subscription.plan.display_name}
          daysRemaining={subscription.days_until_renewal}
        />
      )}
    </div>
  );
}
