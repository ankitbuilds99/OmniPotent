import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Bot, User, Send, Sparkles, Copy, Check } from 'lucide-react'

const DEMO_CONVERSATION = [
  {
    role: 'user',
    content: 'Deploy Node.js App to production'
  },
  {
    role: 'assistant',
    content: 'I\'ll generate a complete deployment setup for your Node.js application. Here are all the files you\'ll need:',
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
      replicas: 3
      resources:
        limits:
          cpus: '0.5'
          memory: 512M`
      },
      {
        name: 'main.tf',
        language: 'hcl',
        code: `resource "aws_ecs_cluster" "main" {
  name = "nodejs-cluster"
}

resource "aws_ecs_service" "app" {
  name            = "nodejs-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 3
  launch_type     = "FARGATE"
}`
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
        image: your-registry/nodejs-app:latest
        ports:
        - containerPort: 3000
        resources:
          limits:
            cpu: "500m"
            memory: "512Mi"`
      },
      {
        name: 'ci.yml',
        language: 'yaml',
        code: `name: CI/CD Pipeline
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build & Push Docker
        run: |
          docker build -t app .
          docker push your-registry/app:latest
      - name: Deploy to K8s
        run: kubectl apply -f k8s/`
      }
    ]
  }
]

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 py-2">
      <motion.div
        className="w-2 h-2 bg-primary rounded-full"
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1, repeat: Infinity, delay: 0 }}
      />
      <motion.div
        className="w-2 h-2 bg-primary rounded-full"
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1, repeat: Infinity, delay: 0.2 }}
      />
      <motion.div
        className="w-2 h-2 bg-primary rounded-full"
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 1, repeat: Infinity, delay: 0.4 }}
      />
    </div>
  )
}

function CodeBlock({
  file,
  delay
}: {
  file: { name: string; language: string; code: string }
  delay: number
}) {
  const [copied, setCopied] = useState(false)
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setVisible(true), delay)
    return () => clearTimeout(timer)
  }, [delay])

  const handleCopy = () => {
    navigator.clipboard.writeText(file.code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (!visible) return null

  return (
    <motion.div
      className="mt-3 rounded-lg overflow-hidden border border-glass-border"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center justify-between px-4 py-2 bg-white/5 border-b border-glass-border">
        <span className="text-sm text-white/70 font-mono">{file.name}</span>
        <button
          onClick={handleCopy}
          className="p-1.5 rounded hover:bg-white/10 transition-colors"
        >
          {copied ? (
            <Check className="w-4 h-4 text-success" />
          ) : (
            <Copy className="w-4 h-4 text-white/50" />
          )}
        </button>
      </div>
      <pre className="p-4 text-sm font-mono text-white/80 overflow-x-auto bg-black/30">
        <code>{file.code}</code>
      </pre>
    </motion.div>
  )
}

function Message({
  message,
  isTyping,
  onTypingComplete
}: {
  message: typeof DEMO_CONVERSATION[0]
  isTyping?: boolean
  onTypingComplete?: () => void
}) {
  const [displayText, setDisplayText] = useState('')
  const isUser = message.role === 'user'

  useEffect(() => {
    if (!isTyping || isUser) {
      setDisplayText(message.content)
      onTypingComplete?.()
      return
    }

    let index = 0
    const text = message.content
    const interval = setInterval(() => {
      if (index < text.length) {
        setDisplayText(text.slice(0, index + 1))
        index++
      } else {
        clearInterval(interval)
        onTypingComplete?.()
      }
    }, 20)

    return () => clearInterval(interval)
  }, [message.content, isTyping, isUser, onTypingComplete])

  return (
    <motion.div
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Avatar */}
      <div
        className={`
          flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center
          ${isUser ? 'bg-secondary/20' : 'bg-primary/20'}
        `}
      >
        {isUser ? (
          <User className="w-5 h-5 text-secondary" />
        ) : (
          <Bot className="w-5 h-5 text-primary" />
        )}
      </div>

      {/* Content */}
      <div className={`flex-1 ${isUser ? 'text-right' : ''}`}>
        <div
          className={`
            inline-block p-4 rounded-2xl max-w-full
            ${isUser
              ? 'bg-secondary/20 text-white rounded-tr-none'
              : 'bg-glass text-white/90 rounded-tl-none'
            }
          `}
        >
          <p className="text-sm leading-relaxed">{displayText}</p>
        </div>

        {/* Code blocks for assistant */}
        {'files' in message && message.files && !isTyping && (
          <div className="mt-2 text-left">
            {message.files.map((file, i) => (
              <CodeBlock key={file.name} file={file} delay={i * 300} />
            ))}
          </div>
        )}
      </div>
    </motion.div>
  )
}

export function AIChat() {
  const [messages, setMessages] = useState<typeof DEMO_CONVERSATION>([])
  const [isTyping, setIsTyping] = useState(false)
  const [showInput, setShowInput] = useState(true)
  const [inputValue, setInputValue] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const hasStarted = useRef(false)

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasStarted.current) {
          hasStarted.current = true
          startDemo()
        }
      },
      { threshold: 0.3 }
    )

    if (containerRef.current) {
      observer.observe(containerRef.current)
    }

    return () => observer.disconnect()
  }, [])

  const startDemo = async () => {
    // User message
    setInputValue(DEMO_CONVERSATION[0].content)
    await new Promise((r) => setTimeout(r, 1000))

    setShowInput(false)
    setMessages([DEMO_CONVERSATION[0]])
    setInputValue('')

    await new Promise((r) => setTimeout(r, 500))

    // AI typing
    setIsTyping(true)
    setMessages((prev) => [...prev, DEMO_CONVERSATION[1]])
  }

  return (
    <section ref={containerRef} className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background to-background-secondary" />

      {/* Decorative elements */}
      <div className="absolute top-1/4 -left-40 w-80 h-80 bg-primary/5 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 -right-40 w-80 h-80 bg-secondary/5 rounded-full blur-3xl" />

      <div className="container-wide relative z-10">
        {/* Section header */}
        <div className="text-center mb-16">
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
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Natural language commands generate production-ready infrastructure code
          </motion.p>
        </div>

        {/* Chat interface */}
        <motion.div
          className="max-w-4xl mx-auto"
          initial={{ opacity: 0, y: 40 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8 }}
        >
          <div className="floating-card overflow-hidden">
            {/* Chat header */}
            <div className="flex items-center gap-3 px-6 py-4 border-b border-glass-border">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-accent flex items-center justify-center">
                <Sparkles className="w-5 h-5 text-background" />
              </div>
              <div>
                <div className="font-display font-semibold text-white">DevOps Copilot</div>
                <div className="text-sm text-success flex items-center gap-1">
                  <span className="w-2 h-2 bg-success rounded-full" />
                  Online
                </div>
              </div>
            </div>

            {/* Messages */}
            <div className="p-6 space-y-6 min-h-[400px] max-h-[600px] overflow-y-auto">
              <AnimatePresence>
                {messages.map((msg, i) => (
                  <Message
                    key={i}
                    message={msg}
                    isTyping={isTyping && i === messages.length - 1 && msg.role === 'assistant'}
                    onTypingComplete={() => setIsTyping(false)}
                  />
                ))}
              </AnimatePresence>

              {isTyping && messages.length > 0 && messages[messages.length - 1].role === 'user' && (
                <div className="flex gap-3">
                  <div className="w-10 h-10 rounded-xl bg-primary/20 flex items-center justify-center">
                    <Bot className="w-5 h-5 text-primary" />
                  </div>
                  <TypingIndicator />
                </div>
              )}
            </div>

            {/* Input */}
            <div className="px-6 py-4 border-t border-glass-border">
              <div className="flex items-center gap-3">
                <input
                  type="text"
                  placeholder="Ask anything about DevOps..."
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  readOnly={!showInput}
                  className="
                    flex-1 bg-white/5 border border-glass-border rounded-xl px-4 py-3
                    text-white placeholder-white/40 focus:outline-none focus:border-primary
                    transition-colors
                  "
                />
                <button
                  className="
                    p-3 rounded-xl bg-gradient-to-r from-primary to-accent
                    text-background transition-all hover:shadow-glow-primary
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
