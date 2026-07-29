import { motion } from 'framer-motion';

interface UsageBarProps {
  label: string;
  used: number;
  limit: number | null;
  unlimited?: boolean;
  color?: string;
  showPercentage?: boolean;
}

export function UsageBar({
  label,
  used,
  limit,
  unlimited = false,
  color = '#00E7FF',
  showPercentage = true,
}: UsageBarProps) {
  const percentage = unlimited || !limit ? 0 : Math.min((used / limit) * 100, 100);
  const isWarning = percentage >= 80;
  const isCritical = percentage >= 95;

  const barColor = isCritical ? '#EF4444' : isWarning ? '#F59E0B' : color;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-300">{label}</span>
        <span className="text-sm text-slate-400">
          {used.toLocaleString()}
          {unlimited ? (
            <span className="text-green-400"> / Unlimited</span>
          ) : limit ? (
            <>
              <span className="text-slate-500"> / </span>
              {limit.toLocaleString()}
            </>
          ) : null}
        </span>
      </div>

      <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
        {unlimited ? (
          <div className="h-full w-full bg-gradient-to-r from-green-500/30 to-green-400/30" />
        ) : (
          <motion.div
            className="h-full rounded-full"
            style={{ backgroundColor: barColor }}
            initial={{ width: 0 }}
            animate={{ width: `${percentage}%` }}
            transition={{ duration: 0.5, ease: 'easeOut' }}
          />
        )}
      </div>

      {showPercentage && !unlimited && limit && (
        <div className="flex items-center justify-between text-xs">
          <span
            className={`${
              isCritical ? 'text-red-400' : isWarning ? 'text-amber-400' : 'text-slate-500'
            }`}
          >
            {percentage.toFixed(1)}% used
          </span>
          {limit - used > 0 && (
            <span className="text-slate-500">
              {(limit - used).toLocaleString()} remaining
            </span>
          )}
        </div>
      )}
    </div>
  );
}
