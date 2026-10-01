import { useRef, useState, useEffect, Suspense } from 'react'
import { motion, useScroll, useTransform } from 'framer-motion'
import { ArrowRight, Sparkles, ChevronDown } from 'lucide-react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Float, Stars } from '@react-three/drei'
import * as THREE from 'three'
import { useThemeColors } from '../../hooks/useThemeColors'
import { useCatalog } from '../../hooks/useCatalog'

const ROTATING_WORDS = ['incidents', 'outages', 'alerts', 'failures', 'pages']

function RotatingText({ words }: { words: string[] }) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const interval = setInterval(() => {
      setIndex((prev) => (prev + 1) % words.length)
    }, 2500)
    return () => clearInterval(interval)
  }, [words.length])

  return (
    <span className="inline-block relative omni-landing__rotating-word" aria-live="polite">
      {words.map((word, i) => (
        <motion.span
          key={word}
          className="absolute left-0 whitespace-nowrap gradient-text"
          initial={{ y: 40, opacity: 0, filter: 'blur(8px)' }}
          animate={{
            y: i === index ? 0 : i === (index - 1 + words.length) % words.length ? -40 : 40,
            opacity: i === index ? 1 : 0,
            filter: i === index ? 'blur(0px)' : 'blur(8px)'
          }}
          transition={{ duration: 0.5, ease: [0.4, 0, 0.2, 1] }}
        >
          {word}
        </motion.span>
      ))}
      <span className="invisible">{words[0]}</span>
    </span>
  )
}

// Floating orb with glow effect
function FloatingOrb({ position, color, size = 1, speed = 1 }: {
  position: [number, number, number]
  color: string
  size?: number
  speed?: number
}) {
  const meshRef = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.position.y = position[1] + Math.sin(state.clock.elapsedTime * speed) * 0.3
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.5} floatIntensity={0.5}>
      <mesh ref={meshRef} position={position}>
        <sphereGeometry args={[size, 32, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.6} />
      </mesh>
      <pointLight position={position} color={color} intensity={2} distance={8} />
    </Float>
  )
}

// Connection lines between points
function ConnectionLines({ colors }: { colors: { accent: string; accent2: string; ok: string } }) {
  const linesRef = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (linesRef.current) {
      linesRef.current.rotation.y = state.clock.elapsedTime * 0.05
    }
  })

  const points = [
    [-3, 2, -2], [3, 1.5, -1], [-2, -1.5, 1], [2.5, -2, 2],
    [-4, 0, 0], [4, 0.5, -1], [0, 3, 1], [0, -3, -1]
  ]

  const colorArray = [colors.accent, colors.accent2, colors.ok]

  return (
    <group ref={linesRef}>
      {points.map((point, i) => (
        <FloatingOrb
          key={i}
          position={point as [number, number, number]}
          color={colorArray[i % 3]}
          size={0.08 + (i % 3) * 0.03}
          speed={0.5 + (i % 4) * 0.2}
        />
      ))}
    </group>
  )
}

function Scene3D({ colors }: { colors: { accent: string; accent2: string; ok: string } }) {
  return (
    <>
      <ambientLight intensity={0.2} />
      <pointLight position={[10, 10, 10]} intensity={0.5} />

      <Stars
        radius={50}
        depth={50}
        count={1500}
        factor={4}
        saturation={0}
        fade
        speed={0.3}
      />

      <ConnectionLines colors={colors} />
    </>
  )
}

const INTEGRATIONS = [
  { name: 'Kubernetes', icon: 'kubernetes', color: '326CE5' },
  { name: 'Docker', icon: 'docker', color: '2496ED' },
  { name: 'Google Cloud', icon: 'googlecloud', color: '4285F4' },
  { name: 'Terraform', icon: 'terraform', color: '844FBA' },
  { name: 'GitHub', icon: 'github', color: 'FFFFFF' },
  { name: 'Datadog', icon: 'datadog', color: '632CA6' },
  { name: 'Prometheus', icon: 'prometheus', color: 'E6522C' },
  { name: 'Sentry', icon: 'sentry', color: '362D59' }
]

export function HeroSection({
  onLaunch,
  onTryDemo,
  ambientPaused = false,
  onAmbientPauseChange,
}: {
  onLaunch: () => void
  onTryDemo?: () => void
  ambientPaused?: boolean
  onAmbientPauseChange?: (paused: boolean) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const themeColors = useThemeColors()
  const colors = {
    ...themeColors,
    accent: '#c8cdd4',
    accent2: '#8f98a5',
    ok: '#b8c0ca'
  }
  const catalog = useCatalog()

  /*
   * Counted from GET /config (hooks/useCatalog.ts), the same way Metrics.tsx
   * already does. The literals this replaces said "9 MCP Servers" and
   * "48 AI Tools" — the build actually ships 11 and 64 — and a fourth card
   * claimed a "99.9% Uptime SLA" that nobody has ever measured, offered or
   * signed. The SLA card is gone for good; the counted cards wait for /config
   * and simply don't render if it never answers. Only the LLM provider count
   * is a literal, because no endpoint reports the providers that *could* be
   * configured — it is verified against `_LLM_KEY_ENV` (app/tenancy/auth.py).
   */
  const stats = [
    ...(catalog
      ? [
          { value: String(catalog.servers), label: 'MCP Servers' },
          { value: String(catalog.tools), label: 'AI Tools' }
        ]
      : []),
    { value: '5', label: 'LLM Providers' }
  ]
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ['start start', 'end start']
  })

  const y = useTransform(scrollYProgress, [0, 1], ['0%', '20%'])
  const opacity = useTransform(scrollYProgress, [0, 0.5], [1, 0])

  return (
    <section
      ref={containerRef}
      className="relative min-h-screen flex items-center justify-center overflow-hidden omni-landing__hero"
      style={{ backgroundColor: colors.bg }}
    >
      {/* 3D Canvas Background */}
      <div className="absolute inset-0 z-[1] omni-landing__hero-scene" aria-hidden="true">
        <Canvas
          camera={{ position: [0, 0, 10], fov: 45 }}
          dpr={[1, 2]}
        >
          <Suspense fallback={null}>
            <Scene3D colors={colors} />
          </Suspense>
        </Canvas>
      </div>

      <div className="absolute inset-0 z-[2] pointer-events-none omni-landing__hero-scrim" />

      {/* Content */}
      <motion.div
        className="relative z-10 max-w-5xl mx-auto px-6 text-center omni-landing__hero-content"
        style={{ y, opacity }}
      >
        {/* Badge */}
        <motion.div
          className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-white/[0.05] border border-white/[0.1] backdrop-blur-sm mb-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
        >
          <span className="relative flex h-2 w-2">
            <span
              className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75"
              style={{ backgroundColor: colors.ok }}
            />
            <span
              className="relative inline-flex rounded-full h-2 w-2"
              style={{ backgroundColor: colors.ok }}
            />
          </span>
          <span className="text-white/70 text-sm font-medium">
            Now Generally Available
          </span>
        </motion.div>

        {/* Main headline */}
        <motion.h1
          className="omni-landing__hero-heading text-4xl sm:text-5xl md:text-6xl lg:text-7xl font-bold tracking-tight mb-6 leading-[1.05]"
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.1 }}
        >
          <span className="text-white">AI agents that</span>
          <br />
          <span className="text-white">resolve </span>
          <RotatingText words={ROTATING_WORDS} />
        </motion.h1>

        {/* Description */}
        <motion.p
          className="text-white/75 text-lg md:text-xl max-w-2xl mx-auto mb-10 leading-relaxed"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
        >
          Your on-call engineers get back to building. AI investigates incidents,
          identifies root causes, and proposes fixes — with human approval before
          any production change.
        </motion.p>

        {/* CTA Buttons */}
        <motion.div
          className="flex flex-col sm:flex-row items-center justify-center gap-4 mb-16"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.3 }}
        >
          <button
            onClick={onLaunch}
            className="group relative inline-flex items-center gap-2 px-8 py-4 bg-white text-black font-semibold rounded-xl hover:bg-white/90 transition-all duration-200 shadow-[0_0_40px_rgba(255,255,255,0.15)]"
          >
            <Sparkles className="w-5 h-5" />
            Start Free Trial
            <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
          </button>
          <button
            onClick={onTryDemo || onLaunch}
            className="inline-flex items-center gap-2 px-8 py-4 text-white font-semibold rounded-xl border border-white/20 bg-white/[0.03] backdrop-blur-sm hover:bg-white/[0.08] hover:border-white/30 transition-all duration-200"
          >
            Try Demo - No Login
          </button>
        </motion.div>

        {/* Stats — see the comment on `stats` above; row narrows until /config answers */}
        <motion.div
          className="flex flex-wrap items-start justify-center gap-8 md:gap-12 max-w-3xl mx-auto mb-16"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.4 }}
        >
          {stats.map((stat) => (
            <div key={stat.label} className="text-center">
              <div className="text-3xl md:text-4xl font-bold text-white mb-1">
                {stat.value}
              </div>
              <div className="text-white/65 text-sm">{stat.label}</div>
            </div>
          ))}
        </motion.div>

        {/* Integrations */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.5 }}
        >
          <p className="text-white/60 text-sm uppercase tracking-wider mb-6">
            Integrates with tools you use
          </p>
          <div className="flex items-center justify-center flex-wrap gap-6 md:gap-8">
            {INTEGRATIONS.map((tool, i) => (
              <motion.div
                key={tool.name}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-white/[0.03] border border-white/[0.06] hover:bg-white/[0.06] hover:border-white/[0.12] transition-all duration-200 cursor-pointer"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: 0.5 + i * 0.05 }}
                whileHover={{ scale: 1.05 }}
              >
                <img
                  src={`https://cdn.simpleicons.org/${tool.icon}/${tool.color}`}
                  alt={tool.name}
                  className="w-5 h-5"
                  loading="lazy"
                />
                <span className="text-white/75 text-sm font-medium">{tool.name}</span>
              </motion.div>
            ))}
          </div>
        </motion.div>
      </motion.div>

      <button
        type="button"
        className="absolute bottom-8 right-6 z-20 rounded-full border border-white/15 bg-black/30 px-3 py-2 text-xs text-white/70 backdrop-blur-md transition hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
        onClick={() => onAmbientPauseChange?.(!ambientPaused)}
        aria-pressed={ambientPaused}
        aria-label={ambientPaused ? 'Play background video' : 'Pause background video'}
      >
        {ambientPaused ? 'Play ambience' : 'Pause ambience'}
      </button>

      {/* Scroll indicator */}
      <motion.div
        className="absolute bottom-8 left-1/2 -translate-x-1/2 z-10 flex flex-col items-center gap-2"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1, delay: 1 }}
      >
        <motion.div
          animate={{ y: [0, 8, 0] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
        >
          <ChevronDown className="w-6 h-6 text-white/30" />
        </motion.div>
      </motion.div>
    </section>
  )
}
