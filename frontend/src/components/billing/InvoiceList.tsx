import { motion } from 'framer-motion';
import { FileText, Download, ExternalLink, CheckCircle, Clock, AlertCircle } from 'lucide-react';
import type { Invoice } from '../../types/billing';

interface InvoiceListProps {
  invoices: Invoice[];
  loading?: boolean;
}

const STATUS_CONFIG: Record<string, { icon: typeof CheckCircle; color: string; label: string }> = {
  paid: { icon: CheckCircle, color: 'text-green-400', label: 'Paid' },
  open: { icon: Clock, color: 'text-amber-400', label: 'Open' },
  draft: { icon: FileText, color: 'text-slate-400', label: 'Draft' },
  void: { icon: AlertCircle, color: 'text-slate-500', label: 'Void' },
  uncollectible: { icon: AlertCircle, color: 'text-red-400', label: 'Uncollectible' },
};

function formatCurrency(cents: number, currency = 'usd'): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: currency.toUpperCase(),
  }).format(cents / 100);
}

function formatDate(dateString: string): string {
  return new Date(dateString).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function InvoiceList({ invoices, loading }: InvoiceListProps) {
  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="h-16 bg-slate-800/50 rounded-lg animate-pulse"
          />
        ))}
      </div>
    );
  }

  if (!invoices.length) {
    return (
      <div className="text-center py-8">
        <FileText className="w-12 h-12 text-slate-600 mx-auto mb-3" />
        <p className="text-slate-400">No invoices yet</p>
        <p className="text-slate-500 text-sm mt-1">
          Invoices will appear here after your first billing cycle
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {invoices.map((invoice, index) => {
        const status = STATUS_CONFIG[invoice.status] || STATUS_CONFIG.draft;
        const StatusIcon = status.icon;

        return (
          <motion.div
            key={invoice.id}
            className="flex items-center justify-between p-4 bg-slate-800/30 rounded-lg hover:bg-slate-800/50 transition-colors"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.05 }}
          >
            <div className="flex items-center gap-4">
              <div className="w-10 h-10 bg-slate-700 rounded-lg flex items-center justify-center">
                <FileText className="w-5 h-5 text-slate-400" />
              </div>

              <div>
                <div className="flex items-center gap-2">
                  <span className="text-white font-medium">{invoice.number}</span>
                  <span className={`flex items-center gap-1 text-xs ${status.color}`}>
                    <StatusIcon className="w-3 h-3" />
                    {status.label}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-sm text-slate-400">
                  <span>{formatDate(invoice.created_at)}</span>
                  {invoice.due_date && invoice.status === 'open' && (
                    <>
                      <span className="text-slate-600">|</span>
                      <span>Due {formatDate(invoice.due_date)}</span>
                    </>
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-4">
              <div className="text-right">
                <div className="text-white font-medium">
                  {formatCurrency(invoice.amount_due, invoice.currency)}
                </div>
                {invoice.amount_paid > 0 && invoice.amount_paid < invoice.amount_due && (
                  <div className="text-xs text-slate-400">
                    {formatCurrency(invoice.amount_paid, invoice.currency)} paid
                  </div>
                )}
              </div>

              <div className="flex items-center gap-2">
                {invoice.invoice_pdf && (
                  <a
                    href={invoice.invoice_pdf}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
                    title="Download PDF"
                  >
                    <Download className="w-4 h-4 text-slate-400" />
                  </a>
                )}
                {invoice.hosted_invoice_url && (
                  <a
                    href={invoice.hosted_invoice_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
                    title="View invoice"
                  >
                    <ExternalLink className="w-4 h-4 text-slate-400" />
                  </a>
                )}
              </div>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
