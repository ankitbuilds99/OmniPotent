import { useEffect, useRef, useState } from 'react'
import { motion, useScroll, useTransform } from 'framer-motion'
import { ArrowRight, Play, Sparkles } from 'lucide-react'
import { Scene3D } from './Scene3D'

const WORDS = ['Build', 'Deploy', 'Monitor', 'Scale', 'Secure', 'Automate']

function AnimatedWord({ words }: { words: string[] }) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setIndex((prev) => (prev + 1) % words.length)
    }, 2000)
    return () => clearInterval(interval)
  }, [words.length])

  return (
    <span className="inline-block relative h-[1.2em] overflow-hidden">
      {words.map((word, i) => (
        <motion.span
          key={word}
          className="absolute left-0 gradient-text"
          initial={{ y: '100%', opacity: 0 }}
          animate={{
            y: i === index ? '0%' : i === (index - 1 + words.length) % words.length ? '-100%' : '100%',
            opacity: i === index ? 1 : 0
          }}
          transition={{ duration: 0.5, ease: [0.4, 0, 0.2, 1] }}
        >
          {word}.
        </motion.span>
      ))}
    </span>
  )
}

function GlowButton({
  children,
  variant = 'primary',
  onClick,
  className = ''
}: {
  children: React.ReactNode
  variant?: 'primary' | 'secondary' | 'ghost'
  onClick?: () => void
  className?: string
}) {
  const buttonRef = useRef<HTMLButtonElement>(null)

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const x = e.clientX - rect.left - rect.width / 2
    const y = e.clientY - rect.top - rect.height / 2

    buttonRef.current.style.transform = `translate(${x * 0.1}px, ${y * 0.1}px)`
  }

  const handleMouseLeave = () => {
    if (buttonRef.current) {
      buttonRef.current.style.transform = 'translate(0, 0)'
    }
  }

  const baseClasses = 'relative inline-flex items-center justify-center gap-2 px-8 py-4 font-display font-semibold text-base rounded-xl cursor-pointer transition-all duration-300 overflow-hidden'

  const variantClasses = {
    primary: 'bg-gradient-to-r from-primary to-accent text-background shadow-glow-primary hover:shadow-[0_0_60px_rgba(0,231,255,0.6)] hover:-translate-y-1',
    secondary: 'bg-glass-border text-white border border-glass-border hover:border-primary hover:shadow-glow-primary hover:-translate-y-1',
    ghost: 'bg-transparent text-white/80 hover:text-white hover:bg-glass'
  }

  return (
    <motion.button
      ref={buttonRef}
      className={`${baseClasses} ${variantClasses[variant]} ${className}`}
      onClick={onClick}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      whileTap={{ scale: 0.98 }}
    >
      {children}
    </motion.button>
  )
}

export function HeroSection({ onLaunch }: { onLaunch: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ['start start', 'end start']
  })

  const y = useTransform(scrollYProgress, [0, 1], ['0%', '30%'])
  const opacity = useTransform(scrollYProgress, [0, 0.5], [1, 0])

  return (
    <section
      ref={containerRef}
      className="relative min-h-screen flex items-center justify-center overflow-hidden"
    >
      {/* 3D Scene Background */}
      <Scene3D />

      {/* Nebula overlay */}
      <div className="nebula-bg" />

      {/* Grid overlay */}
      <div
        className="absolute inset-0 opacity-30 pointer-events-none"
        style={{
          backgroundImage: `
            linear-gradient(rgba(0, 231, 255, 0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 231, 255, 0.03) 1px, transparent 1px)
          `,
          backgroundSize: '60px 60px'
        }}
      />

      {/* Content */}
      <motion.div
        className="relative z-10 container-wide text-center"
        style={{ y, opacity }}
      >
        {/* Eyebrow */}
        <motion.div
          className="flex items-center justify-center gap-3 mb-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.2 }}
        >
          <span className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
            <span className="relative inline-flex rounded-full h-3 w-3 bg-primary" />
          </span>
          <span className="text-white/60 font-medium tracking-wide uppercase text-sm">
            Autonomous AI Operations • LangGraph • MCP
          </span>
        </motion.div>

        {/* Main headline */}
        <motion.h1
          className="font-display text-5xl md:text-7xl lg:text-8xl font-bold tracking-tight mb-6"
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.4 }}
        >
          <span className="text-white">Your AI</span>
          <br />
          <span className="gradient-text">DevOps Engineer</span>
        </motion.h1>

        {/* Animated subheading */}
        <motion.div
          className="font-display text-2xl md:text-3xl lg:text-4xl font-semibold mb-8 h-[1.5em]"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.6 }}
        >
          <AnimatedWord words={WORDS} />
        </motion.div>

        {/* Description */}
        <motion.p
          className="text-white/60 text-lg md:text-xl max-w-3xl mx-auto mb-12 leading-relaxed"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 0.8 }}
        >
          Automate your entire infrastructure using AI-powered DevOps workflows,
          intelligent deployments, cloud orchestration, Kubernetes management,
          monitoring and infrastructure as code.
        </motion.p>

        {/* CTA Buttons */}
        <motion.div
          className="flex flex-col sm:flex-row items-center justify-center gap-4"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 1 }}
        >
          <GlowButton variant="primary" onClick={onLaunch}>
            <Sparkles className="w-5 h-5" />
            Start Free
          </GlowButton>
          <GlowButton variant="secondary" onClick={onLaunch}>
            Live Demo
            <ArrowRight className="w-5 h-5" />
          </GlowButton>
          <GlowButton variant="ghost">
            <Play className="w-5 h-5" />
            Watch 3D Demo
          </GlowButton>
        </motion.div>

        {/* Stats row */}
        <motion.div
          className="flex flex-wrap items-center justify-center gap-8 md:gap-16 mt-16"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, delay: 1.2 }}
        >
          {[
            { value: '99.99%', label: 'Uptime' },
            { value: '<18ms', label: 'Response' },
            { value: '1532+', label: 'Deployments' },
            { value: '12+', label: 'Cloud Providers' }
          ].map((stat, i) => (
            <div key={i} className="text-center">
              <div className="font-display text-2xl md:text-3xl font-bold gradient-text-primary">
                {stat.value}
              </div>
              <div className="text-white/50 text-sm mt-1">{stat.label}</div>
            </div>
          ))}
        </motion.div>
      </motion.div>

      {/* Scroll indicator */}
      <motion.div
        className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1, delay: 1.5 }}
      >
        <span className="text-white/40 text-sm font-medium">Scroll to explore</span>
        <motion.div
          className="w-6 h-10 border-2 border-white/30 rounded-full flex justify-center pt-2"
          animate={{ opacity: [0.5, 1, 0.5] }}
          transition={{ duration: 2, repeat: Infinity }}
        >
          <motion.div
            className="w-1.5 h-1.5 bg-primary rounded-full"
            animate={{ y: [0, 12, 0] }}
            transition={{ duration: 1.5, repeat: Infinity }}
          />
        </motion.div>
      </motion.div>
    </section>
  )
}
