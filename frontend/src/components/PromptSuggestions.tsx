import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Lightbulb,
  Clock,
  TrendingUp,
  AlertTriangle,
  GitBranch,
  Database,
  Server,
  Search,
  Sparkles,
  ChevronRight,
  History,
  Zap,
} from "lucide-react";

interface PromptSuggestion {
  id: string;
  text: string;
  category: "quick" | "trending" | "recent" | "ai";
  icon: React.ReactNode;
  description?: string;
}

interface PromptSuggestionsProps {
  onSelect: (prompt: string) => void;
  recentQueries?: string[];
  context?: {
    currentService?: string;
    recentIncident?: string;
    activeAlert?: string;
  };
}

const QUICK_PROMPTS: PromptSuggestion[] = [
  {
    id: "what-changed",
    text: "What changed in the last hour?",
    category: "quick",
    icon: <Clock className="w-4 h-4" />,
    description: "See recent deployments and config changes",
  },
  {
    id: "error-spike",
    text: "Why did error rates spike?",
    category: "quick",
    icon: <AlertTriangle className="w-4 h-4" />,
    description: "Investigate sudden error rate increases",
  },
  {
    id: "slow-endpoint",
    text: "Which endpoints are slow?",
    category: "quick",
    icon: <TrendingUp className="w-4 h-4" />,
    description: "Find latency issues across services",
  },
  {
    id: "recent-deploys",
    text: "Show recent deployments",
    category: "quick",
    icon: <GitBranch className="w-4 h-4" />,
    description: "List recent code deployments",
  },
  {
    id: "db-performance",
    text: "Check database performance",
    category: "quick",
    icon: <Database className="w-4 h-4" />,
    description: "Query times, connections, locks",
  },
  {
    id: "service-health",
    text: "What's the health of all services?",
    category: "quick",
    icon: <Server className="w-4 h-4" />,
    description: "Overall system health overview",
  },
];

const TRENDING_PROMPTS: PromptSuggestion[] = [
  {
    id: "memory-leak",
    text: "Is there a memory leak in checkout-service?",
    category: "trending",
    icon: <Zap className="w-4 h-4" />,
  },
  {
    id: "compare-deploys",
    text: "Compare performance before and after last deploy",
    category: "trending",
    icon: <TrendingUp className="w-4 h-4" />,
  },
  {
    id: "find-bottleneck",
    text: "Find the bottleneck in the payment flow",
    category: "trending",
    icon: <Search className="w-4 h-4" />,
  },
];

function PromptCard({
  suggestion,
  onSelect,
  compact = false,
}: {
  suggestion: PromptSuggestion;
  onSelect: () => void;
  compact?: boolean;
}) {
  return (
    <motion.button
      onClick={onSelect}
      className={`text-left rounded-xl border transition-all group ${
        compact ? "p-3" : "p-4"
      }`}
      style={{
        backgroundColor: "var(--shell)",
        borderColor: "var(--border)",
      }}
      whileHover={{
        scale: 1.02,
        borderColor: "var(--accent)",
      }}
      whileTap={{ scale: 0.98 }}
    >
      <div className="flex items-start gap-3">
        <div
          className={`${compact ? "w-8 h-8" : "w-10 h-10"} rounded-lg flex items-center justify-center flex-shrink-0 group-hover:bg-blue-600 transition-colors`}
          style={{ backgroundColor: "var(--panel)" }}
        >
          {suggestion.icon}
        </div>
        <div className="flex-1 min-w-0">
          <div className={`font-medium ${compact ? "text-sm" : ""} truncate`}>
            {suggestion.text}
          </div>
          {suggestion.description && !compact && (
            <div className="text-sm text-gray-500 mt-0.5">
              {suggestion.description}
            </div>
          )}
        </div>
        <ChevronRight className="w-4 h-4 text-gray-500 group-hover:text-white transition-colors flex-shrink-0" />
      </div>
    </motion.button>
  );
}

export function PromptSuggestions({
  onSelect,
  recentQueries = [],
  context,
}: PromptSuggestionsProps) {
  const [activeTab, setActiveTab] = useState<"quick" | "trending" | "recent" | "ai">("quick");

  const aiSuggestions: PromptSuggestion[] = [];

  if (context?.currentService) {
    aiSuggestions.push({
      id: "ctx-service",
      text: `Why is ${context.currentService} responding slowly?`,
      category: "ai",
      icon: <Sparkles className="w-4 h-4" />,
      description: "Based on your current context",
    });
  }

  if (context?.activeAlert) {
    aiSuggestions.push({
      id: "ctx-alert",
      text: `Investigate: ${context.activeAlert}`,
      category: "ai",
      icon: <AlertTriangle className="w-4 h-4" />,
      description: "Active alert detected",
    });
  }

  if (context?.recentIncident) {
    aiSuggestions.push({
      id: "ctx-incident",
      text: `What caused the ${context.recentIncident} incident?`,
      category: "ai",
      icon: <Zap className="w-4 h-4" />,
      description: "Recent incident",
    });
  }

  const recentSuggestions: PromptSuggestion[] = recentQueries.slice(0, 5).map((query, i) => ({
    id: `recent-${i}`,
    text: query,
    category: "recent" as const,
    icon: <History className="w-4 h-4" />,
  }));

  const tabs = [
    { id: "quick" as const, label: "Quick Start", icon: <Lightbulb className="w-4 h-4" /> },
    { id: "trending" as const, label: "Trending", icon: <TrendingUp className="w-4 h-4" /> },
    ...(recentSuggestions.length > 0
      ? [{ id: "recent" as const, label: "Recent", icon: <History className="w-4 h-4" /> }]
      : []),
    ...(aiSuggestions.length > 0
      ? [{ id: "ai" as const, label: "For You", icon: <Sparkles className="w-4 h-4" /> }]
      : []),
  ];

  const currentSuggestions =
    activeTab === "quick"
      ? QUICK_PROMPTS
      : activeTab === "trending"
      ? TRENDING_PROMPTS
      : activeTab === "recent"
      ? recentSuggestions
      : aiSuggestions;

  return (
    <div className="w-full">
      {/* Tabs */}
      <div className="flex items-center gap-2 mb-4 overflow-x-auto pb-1">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm whitespace-nowrap transition-colors ${
              activeTab === tab.id
                ? "bg-blue-600 text-white"
                : "hover:bg-white/10"
            }`}
            style={activeTab !== tab.id ? { backgroundColor: "var(--shell)" } : {}}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      {/* Suggestions Grid */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.2 }}
          className="grid grid-cols-1 md:grid-cols-2 gap-3"
        >
          {currentSuggestions.map((suggestion) => (
            <PromptCard
              key={suggestion.id}
              suggestion={suggestion}
              onSelect={() => onSelect(suggestion.text)}
              compact={activeTab === "recent"}
            />
          ))}
        </motion.div>
      </AnimatePresence>

      {/* Empty state for recent */}
      {activeTab === "recent" && recentSuggestions.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          <History className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p>No recent queries yet</p>
          <p className="text-sm">Your investigation history will appear here</p>
        </div>
      )}
    </div>
  );
}

// Inline suggestions for the chat input
export function InlineSuggestions({
  onSelect,
  visible = true,
}: {
  onSelect: (prompt: string) => void;
  visible?: boolean;
}) {
  if (!visible) return null;

  const suggestions = [
    "What changed recently?",
    "Why is X slow?",
    "Check service health",
    "Show recent deploys",
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 10 }}
      className="flex flex-wrap gap-2 mb-3"
    >
      {suggestions.map((text) => (
        <button
          key={text}
          onClick={() => onSelect(text)}
          className="px-3 py-1.5 rounded-full text-sm border hover:border-blue-500 hover:bg-blue-500/10 transition-all"
          style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
        >
          {text}
        </button>
      ))}
    </motion.div>
  );
}
