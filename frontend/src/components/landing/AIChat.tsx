import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Bot, User, Send, Sparkles, Copy, Check, RotateCcw } from 'lucide-react'

const DEMO_PROMPTS = [
  'Deploy Node.js App to production',
  'Why is checkout API throwing 500 errors?',
  'Scale Kubernetes pods to handle traffic spike',
  'Set up CI/CD pipeline for React app'
]

const DEMO_RESPONSES: Record<string, { content: string; files: Array<{ name: string; language: string; code: string }> }> = {
  'Deploy Node.js App to production': {
    content: "I'll generate a complete deployment setup for your Node.js application. Here are all the files you'll need:",
    files: [
      {
        name: 'Dockerfile',
        language: 'dockerfile',
        code: `FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production

FROM node:20-alpine
WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
COPY . .
EXPOSE 3000
CMD ["node", "server.js"]`
      },
      {
        name: 'docker-compose.yml',
        language: 'yaml',
        code: `version: '3.8'
services:
  app:
    build: .
    ports:
      - "3000:3000"
    environment:
      - NODE_ENV=production
    deploy:
      replicas: 3`
      },
      {
        name: 'deployment.yaml',
        language: 'yaml',
        code: `apiVersion: apps/v1
kind: Deployment
metadata:
  name: nodejs-app
spec:
  replicas: 3
  selector:
    matchLabels:
      app: nodejs
  template:
    spec:
      containers:
      - name: nodejs
        image: your-registry/app:latest
        ports:
        - containerPort: 3000`
      }
    ]
  },
  'Why is checkout API throwing 500 errors?': {
    content: "I've analyzed the checkout API and found the root cause. Here's my investigation:",
    files: [
      {
        name: 'root-cause-analysis.md',
        language: 'markdown',
        code: `## Root Cause Analysis

**Issue:** Checkout API returning 500 errors
**Impact:** 23% of transactions failing

### Timeline
- 14:32 UTC - Deploy #847 pushed to production
- 14:35 UTC - Error rate spike detected
- 14:38 UTC - PagerDuty alert triggered

### Root Cause
Null pointer exception in discount calculation
when coupon code is empty string vs null.

### Fix
\`\`\`javascript
// Before (buggy)
const discount = coupon.code.length > 0

// After (fixed)
const discount = coupon?.code?.length > 0
\`\`\``
      },
      {
        name: 'fix.patch',
        language: 'diff',
        code: `--- a/checkout.js
+++ b/checkout.js
@@ -42,7 +42,7 @@ async function applyDiscount(cart, coupon) {
-  if (coupon.code.length > 0) {
+  if (coupon?.code?.length > 0) {
     const discount = await validateCoupon(coupon.code);
     return applyDiscountToCart(cart, discount);
   }`
      }
    ]
  },
  'Scale Kubernetes pods to handle traffic spike': {
    content: "I'll set up autoscaling for your Kubernetes deployment to handle traffic spikes automatically:",
    files: [
      {
        name: 'hpa.yaml',
        language: 'yaml',
        code: `apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: app-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: app
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70`
      },
      {
        name: 'scale-command.sh',
        language: 'bash',
        code: `# Immediate scale (if needed)
kubectl scale deployment/app --replicas=10

# Apply HPA for automatic scaling
kubectl apply -f hpa.yaml

# Monitor scaling
kubectl get hpa -w`
      }
    ]
  },
  'Set up CI/CD pipeline for React app': {
    content: "Here's a complete CI/CD pipeline with GitHub Actions for your React application:",
    files: [
      {
        name: '.github/workflows/ci.yml',
        language: 'yaml',
        code: `name: CI/CD Pipeline
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
      - run: npm ci
      - run: npm test -- --coverage
      - run: npm run build

  deploy:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci && npm run build
      - uses: peaceiris/actions-gh-pages@v3
        with:
          github_token: \${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./build`
      }
    ]
  }
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 py-2 px-1">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="w-2 h-2 bg-primary rounded-full"
          animate={{ scale: [1, 1.2, 1], opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 0.8, repeat: Infinity, delay: i * 0.15 }}
        />
      ))}
    </div>
  )
}

function CodeBlock({ file, index }: { file: { name: string; language: string; code: string }; index: number }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(file.code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <motion.div
      className="mt-3 rounded-xl overflow-hidden border border-white/10 bg-[#0d1117]"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.15 }}
    >
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#161b22] border-b border-white/10">
        <span className="text-sm text-white/70 font-mono">{file.name}</span>
        <button
          onClick={handleCopy}
          className="p-1.5 rounded-md hover:bg-white/10 transition-colors"
        >
          {copied ? (
            <Check className="w-4 h-4 text-green-400" />
          ) : (
            <Copy className="w-4 h-4 text-white/40 hover:text-white/70" />
          )}
        </button>
      </div>
      <pre className="p-4 text-sm font-mono text-white/80 overflow-x-auto leading-relaxed">
        <code>{file.code}</code>
      </pre>
    </motion.div>
  )
}

function ChatMessage({
  role,
  content,
  files,
  isTyping,
  showFiles
}: {
  role: 'user' | 'assistant'
  content: string
  files?: Array<{ name: string; language: string; code: string }>
  isTyping?: boolean
  showFiles?: boolean
}) {
  const isUser = role === 'user'

  return (
    <motion.div
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Avatar */}
      <div
        className={`
          flex-shrink-0 w-9 h-9 rounded-lg flex items-center justify-center
          ${isUser ? 'bg-purple-500/20' : 'bg-cyan-500/20'}
        `}
      >
        {isUser ? (
          <User className="w-4 h-4 text-purple-400" />
        ) : (
          <Bot className="w-4 h-4 text-cyan-400" />
        )}
      </div>

      {/* Content */}
      <div className={`flex-1 max-w-[85%] ${isUser ? 'flex flex-col items-end' : ''}`}>
        <div
          className={`
            inline-block px-4 py-3 rounded-2xl
            ${isUser
              ? 'bg-purple-500/20 text-white rounded-tr-sm'
              : 'bg-white/5 text-white/90 rounded-tl-sm border border-white/5'
            }
          `}
        >
          <p className="text-sm leading-relaxed">{content}</p>
          {isTyping && <TypingIndicator />}
        </div>

        {/* Code blocks */}
        {!isUser && files && showFiles && (
          <div className="w-full mt-2">
            {files.map((file, i) => (
              <CodeBlock key={file.name} file={file} index={i} />
            ))}
          </div>
        )}
      </div>
    </motion.div>
  )
}

export function AIChat() {
  const [messages, setMessages] = useState<Array<{
    role: 'user' | 'assistant'
    content: string
    files?: Array<{ name: string; language: string; code: string }>
  }>>([])
  const [isTyping, setIsTyping] = useState(false)
  const [showFiles, setShowFiles] = useState(false)
  const [inputValue, setInputValue] = useState('')
  const [currentPromptIndex, setCurrentPromptIndex] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const hasStarted = useRef(false)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, showFiles])

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasStarted.current) {
          hasStarted.current = true
          runDemo(DEMO_PROMPTS[0])
        }
      },
      { threshold: 0.3 }
    )

    if (containerRef.current) {
      observer.observe(containerRef.current)
    }

    return () => observer.disconnect()
  }, [])

  const runDemo = async (prompt: string) => {
    setMessages([])
    setShowFiles(false)
    setInputValue('')

    // Type out the prompt
    for (let i = 0; i <= prompt.length; i++) {
      setInputValue(prompt.slice(0, i))
      await new Promise(r => setTimeout(r, 30))
    }

    await new Promise(r => setTimeout(r, 500))

    // Add user message
    setMessages([{ role: 'user', content: prompt }])
    setInputValue('')

    await new Promise(r => setTimeout(r, 300))

    // Show typing indicator
    setIsTyping(true)

    await new Promise(r => setTimeout(r, 1500))

    // Add AI response
    const response = DEMO_RESPONSES[prompt]
    if (response) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: response.content,
        files: response.files
      }])
    }

    setIsTyping(false)

    await new Promise(r => setTimeout(r, 300))

    // Show files
    setShowFiles(true)
  }

  const handleReplay = () => {
    const nextIndex = (currentPromptIndex + 1) % DEMO_PROMPTS.length
    setCurrentPromptIndex(nextIndex)
    runDemo(DEMO_PROMPTS[nextIndex])
  }

  const handlePromptClick = (prompt: string) => {
    const index = DEMO_PROMPTS.indexOf(prompt)
    if (index !== -1) {
      setCurrentPromptIndex(index)
      runDemo(prompt)
    }
  }

  return (
    <section ref={containerRef} className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-[#030508] via-background to-[#030508]" />

      {/* Decorative glows */}
      <div className="absolute top-1/3 left-0 w-96 h-96 bg-cyan-500/10 rounded-full blur-3xl" />
      <div className="absolute bottom-1/3 right-0 w-96 h-96 bg-purple-500/10 rounded-full blur-3xl" />

      <div className="container-wide relative z-10">
        {/* Section header */}
        <div className="text-center mb-12">
          <motion.span
            className="inline-block text-primary text-sm font-semibold tracking-wider uppercase mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5 }}
          >
            AI Assistant
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Talk to your <span className="gradient-text">AI DevOps Engineer</span>
          </motion.h2>
          <motion.p
            className="text-white/50 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Natural language commands generate production-ready infrastructure code
          </motion.p>
        </div>

        {/* Quick prompts */}
        <motion.div
          className="flex flex-wrap justify-center gap-2 mb-8"
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }}
        >
          {DEMO_PROMPTS.map((prompt, i) => (
            <button
              key={prompt}
              onClick={() => handlePromptClick(prompt)}
              className={`
                px-4 py-2 rounded-full text-sm font-medium transition-all duration-200
                ${i === currentPromptIndex
                  ? 'bg-primary/20 text-primary border border-primary/30'
                  : 'bg-white/5 text-white/60 border border-white/10 hover:bg-white/10 hover:text-white/80'
                }
              `}
            >
              {prompt}
            </button>
          ))}
        </motion.div>

        {/* Chat interface */}
        <motion.div
          className="max-w-3xl mx-auto"
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.2 }}
        >
          <div className="rounded-2xl overflow-hidden border border-white/10 bg-[#0a0d12] shadow-2xl">
            {/* Chat header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 bg-white/[0.02]">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
                  <Sparkles className="w-5 h-5 text-white" />
                </div>
                <div>
                  <div className="font-semibold text-white">DevOps Copilot</div>
                  <div className="text-xs text-green-400 flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
                    Online
                  </div>
                </div>
              </div>
              <button
                onClick={handleReplay}
                className="p-2 rounded-lg hover:bg-white/5 text-white/40 hover:text-white/70 transition-colors"
                title="Try another example"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
            </div>

            {/* Messages */}
            <div className="p-5 space-y-5 min-h-[350px] max-h-[500px] overflow-y-auto">
              <AnimatePresence mode="wait">
                {messages.map((msg, i) => (
                  <ChatMessage
                    key={`${currentPromptIndex}-${i}`}
                    role={msg.role}
                    content={msg.content}
                    files={msg.files}
                    showFiles={showFiles && msg.role === 'assistant'}
                  />
                ))}
              </AnimatePresence>

              {isTyping && (
                <motion.div
                  className="flex gap-3"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                >
                  <div className="w-9 h-9 rounded-lg bg-cyan-500/20 flex items-center justify-center">
                    <Bot className="w-4 h-4 text-cyan-400" />
                  </div>
                  <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-white/5 border border-white/5">
                    <TypingIndicator />
                  </div>
                </motion.div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="px-5 py-4 border-t border-white/10 bg-white/[0.02]">
              <div className="flex items-center gap-3">
                <input
                  type="text"
                  placeholder="Ask anything about DevOps..."
                  value={inputValue}
                  readOnly
                  className="
                    flex-1 bg-white/5 border border-white/10 rounded-xl px-4 py-3
                    text-white placeholder-white/30 text-sm
                    focus:outline-none focus:border-cyan-500/50
                    transition-colors cursor-default
                  "
                />
                <button
                  className="
                    p-3 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600
                    text-white transition-all hover:shadow-lg hover:shadow-cyan-500/25
                    active:scale-95
                  "
                >
                  <Send className="w-5 h-5" />
                </button>
              </div>
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
