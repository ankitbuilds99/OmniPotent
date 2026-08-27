import { motion } from 'framer-motion';
import type { BillingCycle } from '../../types/billing';

interface BillingToggleProps {
  value: BillingCycle;
  onChange: (value: BillingCycle) => void;
  /**
   * The best annual saving across the plans on offer — see
   * `bestAnnualSavingsPercent`. Required on purpose: the old default of 17%
   * was an invention, and every plan the API describes saves ~15%.
   */
  savingsPercent: number;
}

export function BillingToggle({ value, onChange, savingsPercent }: BillingToggleProps) {
  const isAnnual = value === 'annual';

  return (
    <div className="flex items-center justify-center gap-4">
      <span
        className={`text-sm transition-colors cursor-pointer ${
          !isAnnual ? 'text-white font-medium' : 'text-slate-400 hover:text-slate-300'
        }`}
        onClick={() => onChange('monthly')}
      >
        Monthly
      </span>

      <button
        onClick={() => onChange(isAnnual ? 'monthly' : 'annual')}
        className={`
          relative w-14 h-7 rounded-full transition-colors duration-300 cursor-pointer
          ${isAnnual ? 'bg-primary' : 'bg-slate-600'}
        `}
        aria-label={`Switch to ${isAnnual ? 'monthly' : 'annual'} billing`}
      >
        <motion.div
          className="absolute top-1 w-5 h-5 bg-white rounded-full shadow-md"
          animate={{ left: isAnnual ? 'calc(100% - 24px)' : '4px' }}
          transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        />
      </button>

      <span
        className={`text-sm transition-colors cursor-pointer ${
          isAnnual ? 'text-white font-medium' : 'text-slate-400 hover:text-slate-300'
        }`}
        onClick={() => onChange('annual')}
      >
        Annual
        {savingsPercent > 0 && (
          <span className="ml-1 text-green-400">
            (Save up to {savingsPercent.toFixed(0)}%)
          </span>
        )}
      </span>
    </div>
  );
}
