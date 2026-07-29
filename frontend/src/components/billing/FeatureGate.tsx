import { useState, useEffect, type ReactNode } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Lock, ArrowUpRight, X } from 'lucide-react';
import { checkFeature } from '../../api/billing';
import type { FeatureCheckResult } from '../../types/billing';

interface FeatureGateProps {
  feature: string;
  children: ReactNode;
  fallback?: ReactNode;
  onUpgrade?: () => void;
  showUpgradePrompt?: boolean;
}

export function FeatureGate({
  feature,
  children,
  fallback,
  onUpgrade,
  showUpgradePrompt = true,
}: FeatureGateProps) {
  const [result, setResult] = useState<FeatureCheckResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    checkFeature(feature)
      .then(setResult)
      .catch(() => setResult({ available: false, feature, reason: 'Failed to check feature' }))
      .finally(() => setLoading(false));
  }, [feature]);

  if (loading) {
    return (
      <div className="animate-pulse bg-slate-800/50 rounded-lg h-20" />
    );
  }

  if (result?.available) {
    return <>{children}</>;
  }

  if (fallback) {
    return <>{fallback}</>;
  }

  return (
    <>
      <div
        className="relative p-4 bg-slate-800/30 rounded-lg border border-slate-700/50 cursor-pointer group"
        onClick={() => showUpgradePrompt && setShowModal(true)}
      >
        <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm rounded-lg flex items-center justify-center">
          <div className="text-center">
            <div className="w-12 h-12 bg-slate-700 rounded-full flex items-center justify-center mx-auto mb-2 group-hover:bg-primary/20 transition-colors">
              <Lock className="w-5 h-5 text-slate-400 group-hover:text-primary transition-colors" />
            </div>
            <p className="text-slate-400 text-sm">
              {result?.upgrade?.required_plan_display || 'Upgrade'} required
            </p>
            {showUpgradePrompt && (
              <p className="text-primary text-xs mt-1 group-hover:underline">
                Click to upgrade
              </p>
            )}
          </div>
        </div>
        <div className="opacity-30 pointer-events-none">
          {children}
        </div>
      </div>

      <AnimatePresence>
        {showModal && result?.upgrade && (
          <motion.div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div
              className="absolute inset-0 bg-black/60 backdrop-blur-sm"
              onClick={() => setShowModal(false)}
            />

            <motion.div
              className="relative bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-md w-full"
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
            >
              <button
                onClick={() => setShowModal(false)}
                className="absolute top-4 right-4 p-1 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
              >
                <X className="w-5 h-5 text-slate-400" />
              </button>

              <div className="w-16 h-16 bg-primary/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                <Lock className="w-8 h-8 text-primary" />
              </div>

              <h3 className="text-xl font-bold text-white text-center mb-2">
                Upgrade to {result.upgrade.required_plan_display}
              </h3>

              <p className="text-slate-400 text-center mb-6">
                {result.reason || `This feature requires the ${result.upgrade.required_plan_display} plan.`}
              </p>

              {result.upgrade.price && (
                <div className="bg-slate-800/50 rounded-lg p-4 mb-6">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Monthly</span>
                    <span className="text-white font-medium">
                      ${result.upgrade.price.monthly?.toFixed(0) || '—'}/mo
                    </span>
                  </div>
                  {result.upgrade.price.annual_monthly && (
                    <div className="flex items-center justify-between mt-2">
                      <span className="text-slate-400">Annual</span>
                      <span className="text-white font-medium">
                        ${result.upgrade.price.annual_monthly.toFixed(0)}/mo
                        <span className="text-green-400 text-xs ml-1">Save 17%</span>
                      </span>
                    </div>
                  )}
                </div>
              )}

              <div className="flex gap-3">
                <button
                  onClick={() => setShowModal(false)}
                  className="flex-1 py-3 bg-slate-700 text-white rounded-xl font-medium hover:bg-slate-600 transition-colors cursor-pointer"
                >
                  Maybe Later
                </button>
                <motion.button
                  onClick={() => {
                    setShowModal(false);
                    onUpgrade?.();
                  }}
                  className="flex-1 flex items-center justify-center gap-2 py-3 bg-primary text-slate-900 rounded-xl font-medium hover:opacity-90 transition-opacity cursor-pointer"
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                >
                  Upgrade Now
                  <ArrowUpRight className="w-4 h-4" />
                </motion.button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
