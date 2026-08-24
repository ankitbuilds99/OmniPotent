import { useState, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  CheckCircle,
  ArrowRight,
  ArrowLeft,
  Play,
  Github,
  MessageSquare,
  Database,
  Bell,
  BookOpen,
  Zap,
  Shield,
  Users,
} from "lucide-react";

import { apiFetch } from "../lib/apiClient";

interface OnboardingStep {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  component: React.ReactNode;
}

interface OnboardingWizardProps {
  onComplete: () => void;
  onSkip: () => void;
}

// Step 1: Welcome
function WelcomeStep({ onTryPlayground }: { onTryPlayground: () => void }) {
  return (
    <div className="text-center">
      <motion.div
        className="w-20 h-20 mx-auto mb-6 rounded-2xl flex items-center justify-center"
        style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ type: "spring", duration: 0.6 }}
      >
        <Sparkles className="w-10 h-10 text-white" />
      </motion.div>

      <h2 className="text-2xl font-bold mb-3">Welcome to OmniPotent</h2>
      <p className="text-gray-400 mb-8 max-w-md mx-auto">
        AI-powered incident investigation that helps you resolve issues 5x faster.
        Let's get you set up in just a few minutes.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        {[
          { icon: <Zap className="w-5 h-5" />, title: "5x Faster MTTR", desc: "AI investigates while you sleep" },
          { icon: <Shield className="w-5 h-5" />, title: "Safety Gates", desc: "Human approval for risky actions" },
          { icon: <Users className="w-5 h-5" />, title: "6-Agent System", desc: "Parallel AI investigation" },
        ].map((feature, i) => (
          <motion.div
            key={i}
            className="p-4 rounded-xl border"
            style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 + i * 0.1 }}
          >
            <div className="w-10 h-10 rounded-lg flex items-center justify-center mb-3 mx-auto" style={{ backgroundColor: "var(--accent)", color: "white" }}>
              {feature.icon}
            </div>
            <div className="font-medium">{feature.title}</div>
            <div className="text-sm text-gray-500">{feature.desc}</div>
          </motion.div>
        ))}
      </div>

      <button
        onClick={onTryPlayground}
        className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
        style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
      >
        <Play className="w-5 h-5" />
        Try the Playground First
      </button>
      <p className="text-sm text-gray-500 mt-3">
        See AI investigate a sample incident before connecting your tools
      </p>
    </div>
  );
}

// Step 2: Playground Demo
function PlaygroundStep({ onNext }: { onNext: () => void }) {
  const [investigating, setInvestigating] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const runDemo = async () => {
    setInvestigating(true);
    setSteps([]);
    setError(null);

    abortRef.current = new AbortController();

    try {
      // Try to run a real investigation via the streaming API
      const demoPrompt = "Investigate: High error rate detected in checkout-service. 5xx errors increased 300% in the last 10 minutes.";

      const response = await apiFetch(`/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: demoPrompt }),
        signal: abortRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(`API returned ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === "tool_start" && data.tool) {
                setSteps(prev => [...prev, `Running: ${data.tool}...`]);
              } else if (data.type === "tool_end" && data.tool) {
                setSteps(prev => {
                  const updated = [...prev];
                  const idx = updated.findIndex(s => s === `Running: ${data.tool}...`);
                  if (idx >= 0) updated[idx] = `Completed: ${data.tool}`;
                  return updated;
                });
              } else if (data.type === "agent" && data.agent) {
                setSteps(prev => [...prev, `Agent: ${data.agent} analyzing...`]);
              } else if (data.type === "message" && data.content) {
                const preview = data.content.slice(0, 80);
                if (preview.toLowerCase().includes("root cause") || preview.toLowerCase().includes("recommend")) {
                  setSteps(prev => [...prev, preview + (data.content.length > 80 ? "..." : "")]);
                }
              } else if (data.type === "done") {
                setSteps(prev => [...prev, "Investigation complete!"]);
              }
            } catch {
              // Skip malformed JSON
            }
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setSteps(prev => [...prev, "Investigation cancelled"]);
      } else {
        // Fallback to demo mode if API is unavailable
        console.log("API unavailable, running demo mode:", err);
        setSteps([]);

        const demoSteps = [
          "Connecting to observability stack...",
          "Querying Prometheus for error metrics...",
          "Fetching recent deployments from GitHub...",
          "Analyzing logs from checkout-service...",
          "Correlating with database metrics...",
          "Running root cause analysis...",
          "Root cause: Database connection pool exhaustion after v2.4.1 deploy",
          "Confidence: 87% - Recommended action: Increase pool size or rollback",
        ];

        for (const step of demoSteps) {
          await new Promise(r => setTimeout(r, 700));
          setSteps(prev => [...prev, step]);
        }
      }
    } finally {
      setInvestigating(false);
      abortRef.current = null;
    }
  };

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">See AI Investigation in Action</h2>
      <p className="text-gray-400 mb-6 text-center">
        Watch how OmniPotent investigates a sample incident
      </p>

      <div
        className="rounded-xl border p-4 mb-6 min-h-[300px]"
        style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2 mb-4 pb-4 border-b" style={{ borderColor: "var(--border)" }}>
          <div className="w-3 h-3 rounded-full bg-red-500" />
          <div className="w-3 h-3 rounded-full bg-yellow-500" />
          <div className="w-3 h-3 rounded-full bg-green-500" />
          <span className="ml-2 text-sm text-gray-500">AI Investigation Console</span>
        </div>

        {!investigating && steps.length === 0 && (
          <div className="text-center py-12">
            <p className="text-gray-500 mb-4">Click below to run a real AI investigation</p>
            <button
              onClick={runDemo}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg font-medium"
              style={{ backgroundColor: "var(--accent)", color: "white" }}
            >
              <Play className="w-4 h-4" />
              Run Investigation
            </button>
            <p className="text-xs text-gray-500 mt-2">Connects to your backend API</p>
          </div>
        )}

        {error && (
          <div className="text-center py-4 text-red-400 text-sm">
            {error}
          </div>
        )}

        <div className="space-y-2 font-mono text-sm">
          {steps.map((step, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-start gap-2"
            >
              <CheckCircle className="w-4 h-4 mt-0.5 text-green-500 flex-shrink-0" />
              <span style={{ color: step.includes("Root cause") || step.includes("Confidence") ? "var(--accent)" : "var(--text)" }}>
                {step}
              </span>
            </motion.div>
          ))}
          {investigating && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center gap-2 text-gray-500"
            >
              <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
              Investigating...
            </motion.div>
          )}
        </div>

        {steps.length > 0 && !investigating && steps.some(s => s.includes("complete") || s.includes("Root cause") || s.includes("Confidence")) && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6 p-4 rounded-lg border"
            style={{ backgroundColor: "rgba(34, 197, 94, 0.1)", borderColor: "rgba(34, 197, 94, 0.3)" }}
          >
            <div className="font-semibold text-green-500 mb-2">Investigation Complete</div>
            <div className="text-sm text-gray-300">
              {steps.filter(s => s.includes("Root cause") || s.includes("Confidence") || s.includes("Recommend")).map((s, i) => (
                <div key={i}>{s}</div>
              ))}
              {steps.filter(s => s.includes("Root cause") || s.includes("Recommend")).length === 0 && (
                <div>Analysis completed. Check the console output above for details.</div>
              )}
            </div>
          </motion.div>
        )}
      </div>

      {steps.length === 8 && (
        <div className="text-center">
          <button
            onClick={onNext}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
            style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          >
            Connect Your Tools
            <ArrowRight className="w-5 h-5" />
          </button>
        </div>
      )}
    </div>
  );
}

// Step 3: Connect Integrations
function IntegrationsStep({ onNext }: { onNext: () => void }) {
  const [connected, setConnected] = useState<string[]>([]);

  const integrations = [
    { id: "github", name: "GitHub", icon: <Github className="w-6 h-6" />, desc: "Code changes & deployments", category: "Code" },
    { id: "slack", name: "Slack", icon: <MessageSquare className="w-6 h-6" />, desc: "Alerts & notifications", category: "Chat" },
    { id: "prometheus", name: "Prometheus", icon: <Database className="w-6 h-6" />, desc: "Metrics & monitoring", category: "Observability" },
    { id: "pagerduty", name: "PagerDuty", icon: <Bell className="w-6 h-6" />, desc: "Incident alerts", category: "Alerting" },
  ];

  const toggleConnect = (id: string) => {
    setConnected(prev =>
      prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
    );
  };

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">Connect Your Tools</h2>
      <p className="text-gray-400 mb-6 text-center">
        Link your observability and code tools for AI-powered investigation
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {integrations.map((integration) => (
          <motion.button
            key={integration.id}
            onClick={() => toggleConnect(integration.id)}
            className={`p-4 rounded-xl border text-left transition-all ${
              connected.includes(integration.id) ? "ring-2" : ""
            }`}
            style={{
              backgroundColor: "var(--shell)",
              borderColor: connected.includes(integration.id) ? "var(--accent)" : "var(--border)"
            }}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            <div className="flex items-start gap-3">
              <div
                className="w-12 h-12 rounded-lg flex items-center justify-center"
                style={{ backgroundColor: connected.includes(integration.id) ? "var(--accent)" : "var(--panel)" }}
              >
                {integration.icon}
              </div>
              <div className="flex-1">
                <div className="flex items-center justify-between">
                  <span className="font-semibold">{integration.name}</span>
                  <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "var(--panel)" }}>
                    {integration.category}
                  </span>
                </div>
                <div className="text-sm text-gray-500">{integration.desc}</div>
              </div>
              {connected.includes(integration.id) && (
                <CheckCircle className="w-5 h-5 text-green-500" />
              )}
            </div>
          </motion.button>
        ))}
      </div>

      <div className="text-center">
        <p className="text-sm text-gray-500 mb-4">
          {connected.length === 0
            ? "Select at least one integration or skip for now"
            : `${connected.length} integration${connected.length > 1 ? 's' : ''} selected`
          }
        </p>
        <button
          onClick={onNext}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        >
          {connected.length > 0 ? "Continue" : "Skip for Now"}
          <ArrowRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}

// Step 4: Configure Alerts
function AlertsStep({ onNext }: { onNext: () => void }) {
  const [autoInvestigate, setAutoInvestigate] = useState(true);
  const [slackChannel, setSlackChannel] = useState("#alerts");

  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">Configure Auto-Investigation</h2>
      <p className="text-gray-400 mb-6 text-center">
        Let AI automatically investigate alerts when they fire
      </p>

      <div
        className="rounded-xl border p-6 mb-6"
        style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
      >
        <div className="flex items-center justify-between mb-6">
          <div>
            <div className="font-semibold">Auto-Investigate Alerts</div>
            <div className="text-sm text-gray-500">AI starts investigating as soon as an alert fires</div>
          </div>
          <button
            onClick={() => setAutoInvestigate(!autoInvestigate)}
            className={`w-12 h-6 rounded-full transition-colors ${autoInvestigate ? "bg-green-500" : "bg-gray-600"}`}
          >
            <div
              className={`w-5 h-5 rounded-full bg-white transition-transform ${autoInvestigate ? "translate-x-6" : "translate-x-1"}`}
            />
          </button>
        </div>

        <div className="mb-4">
          <label className="block text-sm font-medium mb-2">Slack Alert Channel</label>
          <input
            type="text"
            value={slackChannel}
            onChange={(e) => setSlackChannel(e.target.value)}
            className="w-full px-4 py-2 rounded-lg border"
            style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
            placeholder="#alerts"
          />
          <p className="text-xs text-gray-500 mt-1">Channel where alerts are posted</p>
        </div>

        <div className="p-4 rounded-lg" style={{ backgroundColor: "var(--panel)" }}>
          <div className="text-sm font-medium mb-2">How it works:</div>
          <ol className="text-sm text-gray-400 space-y-1">
            <li>1. Alert appears in Slack channel</li>
            <li>2. AI detects and starts investigation</li>
            <li>3. Findings posted in thread within 2-5 minutes</li>
            <li>4. You review and approve any actions</li>
          </ol>
        </div>
      </div>

      <div className="text-center">
        <button
          onClick={onNext}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl font-semibold text-white"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
        >
          Continue
          <ArrowRight className="w-5 h-5" />
        </button>
      </div>
    </div>
  );
}

// Step 5: Add Knowledge
function KnowledgeStep({ onComplete }: { onComplete: () => void }) {
  return (
    <div>
      <h2 className="text-2xl font-bold mb-3 text-center">Add Team Knowledge</h2>
      <p className="text-gray-400 mb-6 text-center">
        Help the AI understand your systems better (optional)
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
        {[
          { icon: <BookOpen className="w-6 h-6" />, title: "Runbooks", desc: "Import existing runbooks and playbooks", action: "Import" },
          { icon: <Database className="w-6 h-6" />, title: "Service Catalog", desc: "Define your services and owners", action: "Configure" },
          { icon: <MessageSquare className="w-6 h-6" />, title: "Terminology", desc: "Add team-specific terms and acronyms", action: "Add" },
          { icon: <Zap className="w-6 h-6" />, title: "Best Practices", desc: "Document investigation best practices", action: "Add" },
        ].map((item, i) => (
          <div
            key={i}
            className="p-4 rounded-xl border"
            style={{ backgroundColor: "var(--shell)", borderColor: "var(--border)" }}
          >
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ backgroundColor: "var(--panel)" }}>
                {item.icon}
              </div>
              <div className="flex-1">
                <div className="font-semibold">{item.title}</div>
                <div className="text-sm text-gray-500 mb-2">{item.desc}</div>
                <button
                  className="text-sm px-3 py-1 rounded-lg"
                  style={{ backgroundColor: "var(--panel)", color: "var(--accent)" }}
                >
                  {item.action}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="text-center">
        <motion.button
          onClick={onComplete}
          className="inline-flex items-center gap-2 px-8 py-4 rounded-xl font-semibold text-white text-lg"
          style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          <Sparkles className="w-6 h-6" />
          Start Investigating
        </motion.button>
        <p className="text-sm text-gray-500 mt-3">
          You can always add more knowledge later in Settings
        </p>
      </div>
    </div>
  );
}

export function OnboardingWizard({ onComplete, onSkip }: OnboardingWizardProps) {
  const [currentStep, setCurrentStep] = useState(0);

  const steps: OnboardingStep[] = [
    {
      id: "welcome",
      title: "Welcome",
      description: "Get started",
      icon: <Sparkles className="w-5 h-5" />,
      component: <WelcomeStep onTryPlayground={() => setCurrentStep(1)} />,
    },
    {
      id: "playground",
      title: "Playground",
      description: "Try demo",
      icon: <Play className="w-5 h-5" />,
      component: <PlaygroundStep onNext={() => setCurrentStep(2)} />,
    },
    {
      id: "integrations",
      title: "Integrations",
      description: "Connect tools",
      icon: <Database className="w-5 h-5" />,
      component: <IntegrationsStep onNext={() => setCurrentStep(3)} />,
    },
    {
      id: "alerts",
      title: "Alerts",
      description: "Auto-investigate",
      icon: <Bell className="w-5 h-5" />,
      component: <AlertsStep onNext={() => setCurrentStep(4)} />,
    },
    {
      id: "knowledge",
      title: "Knowledge",
      description: "Team context",
      icon: <BookOpen className="w-5 h-5" />,
      component: <KnowledgeStep onComplete={onComplete} />,
    },
  ];

  return (
    <div className="min-h-screen flex items-center justify-center p-4" style={{ backgroundColor: "var(--bg)" }}>
      <div className="w-full max-w-3xl">
        {/* Progress indicator */}
        <div className="flex items-center justify-between mb-8">
          {steps.map((step, i) => (
            <div key={step.id} className="flex items-center">
              <button
                onClick={() => i < currentStep && setCurrentStep(i)}
                disabled={i > currentStep}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg transition-colors ${
                  i === currentStep
                    ? "bg-white/10"
                    : i < currentStep
                    ? "hover:bg-white/5 cursor-pointer"
                    : "opacity-50 cursor-not-allowed"
                }`}
              >
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center ${
                    i < currentStep
                      ? "bg-green-500"
                      : i === currentStep
                      ? "bg-white/20"
                      : "bg-white/10"
                  }`}
                >
                  {i < currentStep ? <CheckCircle className="w-5 h-5" /> : step.icon}
                </div>
                <div className="hidden md:block text-left">
                  <div className="text-sm font-medium">{step.title}</div>
                  <div className="text-xs text-gray-500">{step.description}</div>
                </div>
              </button>
              {i < steps.length - 1 && (
                <div
                  className="w-8 h-0.5 mx-2"
                  style={{ backgroundColor: i < currentStep ? "var(--accent)" : "var(--border)" }}
                />
              )}
            </div>
          ))}
        </div>

        {/* Step content */}
        <div
          className="rounded-2xl border p-8"
          style={{ backgroundColor: "var(--panel)", borderColor: "var(--border)" }}
        >
          <AnimatePresence mode="wait">
            <motion.div
              key={currentStep}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.3 }}
            >
              {steps[currentStep].component}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between mt-6">
          <button
            onClick={() => currentStep > 0 ? setCurrentStep(currentStep - 1) : onSkip()}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-gray-400 hover:text-white transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            {currentStep > 0 ? "Back" : "Skip Setup"}
          </button>

          <div className="text-sm text-gray-500">
            Step {currentStep + 1} of {steps.length}
          </div>
        </div>
      </div>
    </div>
  );
}
