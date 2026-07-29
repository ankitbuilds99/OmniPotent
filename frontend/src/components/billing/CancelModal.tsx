import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, X } from 'lucide-react';

interface CancelModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (reason: string, feedback: string, immediate: boolean) => void;
  loading?: boolean;
  planName: string;
  daysRemaining: number;
}

const CANCEL_REASONS = [
  { value: 'too_expensive', label: 'Too expensive' },
  { value: 'missing_features', label: 'Missing features I need' },
  { value: 'found_alternative', label: 'Found an alternative' },
  { value: 'not_using', label: 'Not using it enough' },
  { value: 'temporary', label: 'Temporary pause' },
  { value: 'other', label: 'Other' },
];

export function CancelModal({
  isOpen,
  onClose,
  onConfirm,
  loading,
  planName,
  daysRemaining,
}: CancelModalProps) {
  const [reason, setReason] = useState('');
  const [feedback, setFeedback] = useState('');
  const [immediate, setImmediate] = useState(false);
  const [step, setStep] = useState(1);

  const handleConfirm = () => {
    onConfirm(reason, feedback, immediate);
  };

  const reset = () => {
    setReason('');
    setFeedback('');
    setImmediate(false);
    setStep(1);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={handleClose}
          />

          <motion.div
            className="relative bg-slate-900 border border-slate-700 rounded-2xl p-6 max-w-lg w-full"
            initial={{ scale: 0.9, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.9, opacity: 0 }}
          >
            <button
              onClick={handleClose}
              className="absolute top-4 right-4 p-1 hover:bg-slate-800 rounded-lg transition-colors cursor-pointer"
            >
              <X className="w-5 h-5 text-slate-400" />
            </button>

            {step === 1 && (
              <>
                <div className="w-16 h-16 bg-amber-400/10 rounded-2xl flex items-center justify-center mx-auto mb-4">
                  <AlertTriangle className="w-8 h-8 text-amber-400" />
                </div>

                <h3 className="text-xl font-bold text-white text-center mb-2">
                  Cancel {planName}?
                </h3>

                <p className="text-slate-400 text-center mb-6">
                  We're sorry to see you go. Help us understand why you're leaving.
                </p>

                <div className="space-y-2 mb-6">
                  {CANCEL_REASONS.map((r) => (
                    <label
                      key={r.value}
                      className={`flex items-center p-3 rounded-lg border cursor-pointer transition-all ${
                        reason === r.value
                          ? 'border-primary bg-primary/5'
                          : 'border-slate-700 hover:border-slate-600'
                      }`}
                    >
                      <input
                        type="radio"
                        name="reason"
                        value={r.value}
                        checked={reason === r.value}
                        onChange={(e) => setReason(e.target.value)}
                        className="sr-only"
                      />
                      <div
                        className={`w-4 h-4 rounded-full border-2 mr-3 flex items-center justify-center ${
                          reason === r.value ? 'border-primary' : 'border-slate-500'
                        }`}
                      >
                        {reason === r.value && (
                          <div className="w-2 h-2 rounded-full bg-primary" />
                        )}
                      </div>
                      <span className="text-white">{r.label}</span>
                    </label>
                  ))}
                </div>

                <div className="flex gap-3">
                  <button
                    onClick={handleClose}
                    className="flex-1 py-3 bg-slate-700 text-white rounded-xl font-medium hover:bg-slate-600 transition-colors cursor-pointer"
                  >
                    Keep Subscription
                  </button>
                  <button
                    onClick={() => setStep(2)}
                    disabled={!reason}
                    className="flex-1 py-3 bg-red-500 text-white rounded-xl font-medium hover:bg-red-600 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Continue
                  </button>
                </div>
              </>
            )}

            {step === 2 && (
              <>
                <h3 className="text-xl font-bold text-white mb-4">
                  Anything else you'd like to share?
                </h3>

                <textarea
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="Your feedback helps us improve..."
                  className="w-full h-32 px-4 py-3 bg-slate-800 border border-slate-700 rounded-lg text-white placeholder-slate-500 resize-none focus:outline-none focus:border-primary transition-colors"
                />

                <div className="mt-4 p-4 bg-slate-800/50 rounded-lg">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={immediate}
                      onChange={(e) => setImmediate(e.target.checked)}
                      className="mt-1"
                    />
                    <div>
                      <span className="text-white font-medium">Cancel immediately</span>
                      <p className="text-slate-400 text-sm mt-1">
                        {immediate
                          ? 'Your subscription will end now and you will lose access immediately.'
                          : `Your subscription will remain active for ${daysRemaining} more days until the end of your billing period.`
                        }
                      </p>
                    </div>
                  </label>
                </div>

                <div className="flex gap-3 mt-6">
                  <button
                    onClick={() => setStep(1)}
                    className="flex-1 py-3 bg-slate-700 text-white rounded-xl font-medium hover:bg-slate-600 transition-colors cursor-pointer"
                  >
                    Back
                  </button>
                  <button
                    onClick={handleConfirm}
                    disabled={loading}
                    className="flex-1 py-3 bg-red-500 text-white rounded-xl font-medium hover:bg-red-600 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {loading ? 'Canceling...' : 'Confirm Cancellation'}
                  </button>
                </div>
              </>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
