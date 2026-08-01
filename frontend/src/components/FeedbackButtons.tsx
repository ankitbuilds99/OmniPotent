import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ThumbsUp, ThumbsDown, MessageSquare, X, Send, CheckCircle } from "lucide-react";

interface FeedbackButtonsProps {
  responseId: string;
  onFeedback?: (responseId: string, feedback: FeedbackData) => void;
  compact?: boolean;
}

interface FeedbackData {
  helpful: boolean;
  comment?: string;
  categories?: string[];
}

const FEEDBACK_CATEGORIES = [
  { id: "incorrect", label: "Incorrect information" },
  { id: "incomplete", label: "Missing information" },
  { id: "irrelevant", label: "Not relevant" },
  { id: "confusing", label: "Hard to understand" },
  { id: "slow", label: "Too slow" },
  { id: "other", label: "Other" },
];

export function FeedbackButtons({ responseId, onFeedback, compact = false }: FeedbackButtonsProps) {
  const [feedback, setFeedback] = useState<"helpful" | "not_helpful" | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [comment, setComment] = useState("");
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [submitted, setSubmitted] = useState(false);

  const handleFeedback = (isHelpful: boolean) => {
    if (isHelpful) {
      setFeedback("helpful");
      onFeedback?.(responseId, { helpful: true });
      setSubmitted(true);
    } else {
      setFeedback("not_helpful");
      setShowModal(true);
    }
  };

  const submitDetailedFeedback = () => {
    onFeedback?.(responseId, {
      helpful: false,
      comment: comment.trim() || undefined,
      categories: selectedCategories.length > 0 ? selectedCategories : undefined,
    });
    setShowModal(false);
    setSubmitted(true);
  };

  const toggleCategory = (id: string) => {
    setSelectedCategories((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    );
  };

  if (submitted) {
    return (
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        className="flex items-center gap-2 text-sm text-gray-400"
      >
        <CheckCircle className="w-4 h-4 text-green-500" />
        Thanks for your feedback!
      </motion.div>
    );
  }

  return (
    <>
      <div className={`flex items-center gap-2 ${compact ? "" : "mt-4 pt-4 border-t"}`} style={{ borderColor: "var(--border)" }}>
        {!compact && <span className="text-sm text-gray-500 mr-2">Was this helpful?</span>}

        <motion.button
          onClick={() => handleFeedback(true)}
          className={`p-2 rounded-lg transition-all ${
            feedback === "helpful"
              ? "bg-green-500/20 text-green-400"
              : "hover:bg-white/10 text-gray-400 hover:text-white"
          }`}
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          title="Helpful"
        >
          <ThumbsUp className={compact ? "w-4 h-4" : "w-5 h-5"} />
        </motion.button>

        <motion.button
          onClick={() => handleFeedback(false)}
          className={`p-2 rounded-lg transition-all ${
            feedback === "not_helpful"
              ? "bg-red-500/20 text-red-400"
              : "hover:bg-white/10 text-gray-400 hover:text-white"
          }`}
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          title="Not helpful"
        >
          <ThumbsDown className={compact ? "w-4 h-4" : "w-5 h-5"} />
        </motion.button>
      </div>

      {/* Detailed Feedback Modal */}
      <AnimatePresence>
        {showModal && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <motion.div
              initial={{ opacity: 0, scale: 0.95, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95, y: 20 }}
              className="w-full max-w-md rounded-2xl p-6"
              style={{ backgroundColor: "var(--panel)", border: "1px solid var(--border)" }}
            >
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <MessageSquare className="w-5 h-5 text-blue-400" />
                  <h3 className="text-lg font-semibold">Help Us Improve</h3>
                </div>
                <button
                  onClick={() => {
                    setShowModal(false);
                    setFeedback(null);
                  }}
                  className="p-1 hover:bg-white/10 rounded"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <p className="text-sm text-gray-400 mb-4">
                What went wrong? Your feedback helps us make better investigations.
              </p>

              {/* Categories */}
              <div className="mb-4">
                <label className="block text-sm font-medium mb-2">
                  What was the issue? (select all that apply)
                </label>
                <div className="flex flex-wrap gap-2">
                  {FEEDBACK_CATEGORIES.map((cat) => (
                    <button
                      key={cat.id}
                      onClick={() => toggleCategory(cat.id)}
                      className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                        selectedCategories.includes(cat.id)
                          ? "bg-blue-600 text-white"
                          : "hover:bg-white/10"
                      }`}
                      style={
                        !selectedCategories.includes(cat.id)
                          ? { backgroundColor: "var(--shell)" }
                          : {}
                      }
                    >
                      {cat.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Comment */}
              <div className="mb-6">
                <label className="block text-sm font-medium mb-2">
                  Additional details (optional)
                </label>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  rows={3}
                  className="w-full px-4 py-2 rounded-lg border resize-none"
                  style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
                  placeholder="Tell us what could be improved..."
                />
              </div>

              {/* Actions */}
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => {
                    setShowModal(false);
                    setFeedback(null);
                  }}
                  className="px-4 py-2 rounded-lg hover:bg-white/10"
                >
                  Cancel
                </button>
                <button
                  onClick={submitDetailedFeedback}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700"
                >
                  <Send className="w-4 h-4" />
                  Submit Feedback
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}

// Inline feedback for chat messages
export function InlineFeedback({
  responseId,
  onFeedback,
}: {
  responseId: string;
  onFeedback?: (responseId: string, helpful: boolean) => void;
}) {
  const [feedback, setFeedback] = useState<boolean | null>(null);

  if (feedback !== null) {
    return (
      <span className="text-xs text-gray-500">
        {feedback ? "Thanks!" : "We'll improve"}
      </span>
    );
  }

  return (
    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
      <button
        onClick={() => {
          setFeedback(true);
          onFeedback?.(responseId, true);
        }}
        className="p-1 rounded hover:bg-white/10 text-gray-500 hover:text-green-400"
        title="Helpful"
      >
        <ThumbsUp className="w-3 h-3" />
      </button>
      <button
        onClick={() => {
          setFeedback(false);
          onFeedback?.(responseId, false);
        }}
        className="p-1 rounded hover:bg-white/10 text-gray-500 hover:text-red-400"
        title="Not helpful"
      >
        <ThumbsDown className="w-3 h-3" />
      </button>
    </div>
  );
}
