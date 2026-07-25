import { useRef, useState } from 'react'
import { motion, useMotionValue, useSpring, useTransform } from 'framer-motion'
import {
  Bot,
  Cloud,
  Container,
  FileCode,
  Server,
  GitBranch,
  Workflow,
  Activity,
  FileText,
  KeyRound,
  Shield,
  DollarSign
} from 'lucide-react'

const FEATURES = [
  {
    icon: Bot,
    title: 'AI Assistant',
    description: 'Intelligent DevOps assistant powered by advanced LLMs',
    color: '#00E7FF',
    gradient: 'from-[#00E7FF] to-[#00FFB3]'
  },
  {
    icon: Cloud,
    title: 'Cloud Automation',
    description: 'Multi-cloud orchestration across AWS, Azure, GCP',
    color: '#7C3AED',
    gradient: 'from-[#7C3AED] to-[#A855F7]'
  },
  {
    icon: Container,
    title: 'Docker',
    description: 'Automated container builds and registry management',
    color: '#0DB7ED',
    gradient: 'from-[#0DB7ED] to-[#00C4FF]'
  },
  {
    icon: FileCode,
    title: 'Terraform',
    description: 'Infrastructure as Code generation and management',
    color: '#7B42BC',
    gradient: 'from-[#7B42BC] to-[#9D5CD9]'
  },
  {
    icon: Server,
    title: 'Kubernetes',
    description: 'Intelligent K8s cluster management and scaling',
    color: '#326CE5',
    gradient: 'from-[#326CE5] to-[#5B8FF9]'
  },
  {
    icon: GitBranch,
    title: 'GitHub Actions',
    description: 'CI/CD pipeline generation and optimization',
    color: '#FFFFFF',
    gradient: 'from-[#FFFFFF] to-[#E5E5E5]'
  },
  {
    icon: Workflow,
    title: 'CI/CD',
    description: 'End-to-end deployment pipeline automation',
    color: '#00FFB3',
    gradient: 'from-[#00FFB3] to-[#3CF281]'
  },
  {
    icon: Activity,
    title: 'Monitoring',
    description: 'Real-time metrics, alerts, and observability',
    color: '#E6522C',
    gradient: 'from-[#E6522C] to-[#FF7043]'
  },
  {
    icon: FileText,
    title: 'Logging',
    description: 'Centralized logging with intelligent analysis',
    color: '#F46800',
    gradient: 'from-[#F46800] to-[#FF9800]'
  },
  {
    icon: KeyRound,
    title: 'Secrets Management',
    description: 'Secure vault integration and rotation',
    color: '#FFD700',
    gradient: 'from-[#FFD700] to-[#FFC107]'
  },
  {
    icon: Shield,
    title: 'Security',
    description: 'Automated security scanning and compliance',
    color: '#FF4D6D',
    gradient: 'from-[#FF4D6D] to-[#FF6B8A]'
  },
  {
    icon: DollarSign,
    title: 'Cost Optimization',
    description: 'AI-driven cloud cost analysis and savings',
    color: '#3CF281',
    gradient: 'from-[#3CF281] to-[#00FFB3]'
  }
]

function FeatureCard({
  feature,
  index
}: {
  feature: typeof FEATURES[0]
  index: number
}) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [isHovered, setIsHovered] = useState(false)

  const mouseX = useMotionValue(0)
  const mouseY = useMotionValue(0)

  const rotateX = useSpring(useTransform(mouseY, [-0.5, 0.5], [10, -10]), {
    stiffness: 300,
    damping: 30
  })
  const rotateY = useSpring(useTransform(mouseX, [-0.5, 0.5], [-10, 10]), {
    stiffness: 300,
    damping: 30
  })

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!cardRef.current) return
    const rect = cardRef.current.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width - 0.5
    const y = (e.clientY - rect.top) / rect.height - 0.5
    mouseX.set(x)
    mouseY.set(y)

    // Update CSS custom properties for glow effect
    cardRef.current.style.setProperty('--mouse-x', `${(x + 0.5) * 100}%`)
    cardRef.current.style.setProperty('--mouse-y', `${(y + 0.5) * 100}%`)
  }

  const handleMouseLeave = () => {
    mouseX.set(0)
    mouseY.set(0)
    setIsHovered(false)
  }

  const Icon = feature.icon

  return (
    <motion.div
      ref={cardRef}
      className="floating-card p-6 cursor-pointer"
      style={{
        rotateX: isHovered ? rotateX : 0,
        rotateY: isHovered ? rotateY : 0,
        transformStyle: 'preserve-3d'
      }}
      initial={{ opacity: 0, y: 50 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{ duration: 0.5, delay: index * 0.1 }}
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={handleMouseLeave}
    >
      {/* Icon */}
      <motion.div
        className={`w-14 h-14 rounded-xl bg-gradient-to-br ${feature.gradient} p-3 mb-4`}
        style={{ transform: 'translateZ(20px)' }}
        whileHover={{ scale: 1.1 }}
        transition={{ type: 'spring', stiffness: 400 }}
      >
        <Icon className="w-full h-full text-background" />
      </motion.div>

      {/* Content */}
      <motion.h3
        className="font-display text-xl font-semibold text-white mb-2"
        style={{ transform: 'translateZ(15px)' }}
      >
        {feature.title}
      </motion.h3>
      <motion.p
        className="text-white/60 text-sm leading-relaxed"
        style={{ transform: 'translateZ(10px)' }}
      >
        {feature.description}
      </motion.p>

      {/* Animated border on hover */}
      {isHovered && (
        <motion.div
          className="absolute inset-0 rounded-[20px] pointer-events-none"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          style={{
            background: `linear-gradient(135deg, ${feature.color}20, transparent)`,
            boxShadow: `0 0 40px ${feature.color}20`
          }}
        />
      )}
    </motion.div>
  )
}

export function FeatureCards() {
  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background to-background-secondary" />

      {/* Decorative orbs */}
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
            Capabilities
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Everything you need to <span className="gradient-text">automate DevOps</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Comprehensive tooling for modern cloud infrastructure management
          </motion.p>
        </div>

        {/* Feature grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {FEATURES.map((feature, index) => (
            <FeatureCard key={feature.title} feature={feature} index={index} />
          ))}
        </div>
      </div>
    </section>
  )
}
