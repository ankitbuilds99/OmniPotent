import { motion } from 'framer-motion';
import { CreditCard, Check } from 'lucide-react';
import type { PaymentMethod } from '../../types/billing';

interface PaymentMethodCardProps {
  method: PaymentMethod;
  onSetDefault?: (id: string) => void;
  onRemove?: (id: string) => void;
}

const CARD_BRANDS: Record<string, { color: string; gradient: string }> = {
  visa: { color: '#1A1F71', gradient: 'from-blue-600 to-blue-800' },
  mastercard: { color: '#EB001B', gradient: 'from-red-500 to-orange-500' },
  amex: { color: '#006FCF', gradient: 'from-blue-400 to-blue-600' },
  discover: { color: '#FF6000', gradient: 'from-orange-400 to-orange-600' },
  default: { color: '#6B7280', gradient: 'from-slate-500 to-slate-700' },
};

export function PaymentMethodCard({ method, onSetDefault, onRemove }: PaymentMethodCardProps) {
  const brandConfig = CARD_BRANDS[method.brand.toLowerCase()] || CARD_BRANDS.default;

  return (
    <motion.div
      className={`
        relative p-4 rounded-xl border transition-all
        ${method.is_default
          ? 'border-primary bg-primary/5'
          : 'border-slate-700 bg-slate-800/30 hover:border-slate-600'
        }
      `}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      whileHover={{ scale: 1.02 }}
    >
      {method.is_default && (
        <div className="absolute -top-2 -right-2">
          <div className="w-5 h-5 bg-primary rounded-full flex items-center justify-center">
            <Check className="w-3 h-3 text-slate-900" />
          </div>
        </div>
      )}

      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div
            className={`w-12 h-8 rounded-md bg-gradient-to-br ${brandConfig.gradient} flex items-center justify-center`}
          >
            <CreditCard className="w-5 h-5 text-white" />
          </div>

          <div>
            <div className="flex items-center gap-2">
              <span className="text-white font-medium capitalize">
                {method.brand}
              </span>
              <span className="text-slate-400">
                **** {method.last4}
              </span>
            </div>
            <div className="text-sm text-slate-500">
              Expires {method.exp_month.toString().padStart(2, '0')}/{method.exp_year}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {!method.is_default && onSetDefault && (
            <button
              onClick={() => onSetDefault(method.id)}
              className="text-xs text-slate-400 hover:text-primary transition-colors cursor-pointer"
            >
              Set default
            </button>
          )}
          {onRemove && (
            <button
              onClick={() => onRemove(method.id)}
              className="text-xs text-slate-400 hover:text-red-400 transition-colors cursor-pointer"
            >
              Remove
            </button>
          )}
        </div>
      </div>

      {method.is_default && (
        <div className="mt-2 text-xs text-primary">Default payment method</div>
      )}
    </motion.div>
  );
}
