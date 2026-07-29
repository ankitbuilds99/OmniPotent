import { useMemo } from 'react';
import { motion } from 'framer-motion';
import type { UsageHistory } from '../../types/billing';

interface UsageChartProps {
  data: UsageHistory[];
  metric: keyof Omit<UsageHistory, 'year_month'>;
  label: string;
  color?: string;
  height?: number;
}

export function UsageChart({
  data,
  metric,
  label,
  color = '#00E7FF',
  height = 120,
}: UsageChartProps) {
  const chartData = useMemo(() => {
    if (!data.length) return { points: [], max: 0, labels: [] };

    const values = data.map((d) => (d[metric] as number) || 0);
    const max = Math.max(...values, 1);
    const labels = data.map((d) => {
      const [year, month] = d.year_month.split('-');
      return new Date(parseInt(year), parseInt(month) - 1).toLocaleDateString('en-US', {
        month: 'short',
      });
    });

    const points = values.map((v, i) => ({
      x: (i / (values.length - 1 || 1)) * 100,
      y: 100 - (v / max) * 100,
      value: v,
    }));

    return { points, max, labels };
  }, [data, metric]);

  if (!data.length) {
    return (
      <div
        className="flex items-center justify-center bg-slate-800/50 rounded-lg"
        style={{ height }}
      >
        <span className="text-slate-500 text-sm">No data available</span>
      </div>
    );
  }

  const pathD = chartData.points
    .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`)
    .join(' ');

  const areaD = `${pathD} L 100 100 L 0 100 Z`;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-300">{label}</span>
        <span className="text-sm text-slate-400">
          {chartData.points[chartData.points.length - 1]?.value.toLocaleString() || 0} this month
        </span>
      </div>

      <div className="relative" style={{ height }}>
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="w-full h-full"
        >
          <defs>
            <linearGradient id={`gradient-${metric}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.3" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>

          <motion.path
            d={areaD}
            fill={`url(#gradient-${metric})`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.5 }}
          />

          <motion.path
            d={pathD}
            fill="none"
            stroke={color}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 1, ease: 'easeOut' }}
          />

          {chartData.points.map((p, i) => (
            <motion.circle
              key={i}
              cx={p.x}
              cy={p.y}
              r="3"
              fill={color}
              initial={{ scale: 0 }}
              animate={{ scale: 1 }}
              transition={{ delay: 0.5 + i * 0.05 }}
              className="hover:r-4 cursor-pointer"
            />
          ))}
        </svg>

        <div className="absolute bottom-0 left-0 right-0 flex justify-between text-xs text-slate-500 pt-1">
          {chartData.labels.filter((_, i) => i % Math.ceil(chartData.labels.length / 6) === 0).map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
