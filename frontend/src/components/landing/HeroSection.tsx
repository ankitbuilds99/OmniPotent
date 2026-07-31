import { useRef, useState, useEffect, Suspense } from 'react'
import { motion, useScroll, useTransform } from 'framer-motion'
import { ArrowRight, Sparkles, ChevronDown } from 'lucide-react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Float, Sphere, MeshDistortMaterial, Stars } from '@react-three/drei'
import * as THREE from 'three'
import { useThemeColors } from '../../hooks/useThemeColors'

const ROTATING_WORDS = ['incidents', 'outages', 'alerts', 'failures', 'pages']

function RotatingText({ words }: { words: string[] }) {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setIndex((prev) => (prev + 1) % words.length)
    }, 2500)
    return () => clearInterval(interval)
  }, [words.length])

  return (
    <span className="inline-block relative">
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

// Central AI core visualization
function AICore({ colors }: { colors: { accent: string; accent2: string; ok: string } }) {
  const coreRef = useRef<THREE.Group>(null)
  const innerRef = useRef<THREE.Mesh>(null)
  const ring1Ref = useRef<THREE.Mesh>(null)
  const ring2Ref = useRef<THREE.Mesh>(null)
  const ring3Ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    const t = state.clock.elapsedTime

    if (coreRef.current) {
      coreRef.current.rotation.y = t * 0.1
    }
    if (innerRef.current) {
      innerRef.current.rotation.x = t * 0.2
      innerRef.current.rotation.z = t * 0.15
    }
    if (ring1Ref.current) {
      ring1Ref.current.rotation.z = t * 0.3
    }
    if (ring2Ref.current) {
      ring2Ref.current.rotation.z = -t * 0.2
      ring2Ref.current.rotation.x = Math.PI / 4
    }
    if (ring3Ref.current) {
      ring3Ref.current.rotation.z = t * 0.15
      ring3Ref.current.rotation.y = Math.PI / 3
    }
  })

  return (
    <group ref={coreRef}>
      {/* Inner glowing sphere */}
      <Float speed={1.5} rotationIntensity={0.3} floatIntensity={0.3}>
        <Sphere ref={innerRef} args={[1.2, 64, 64]}>
          <MeshDistortMaterial
            color={colors.accent}
            emissive={colors.accent}
            emissiveIntensity={0.4}
            roughness={0.2}
            metalness={0.8}
            distort={0.3}
            speed={2}
            transparent
            opacity={0.8}
          />
        </Sphere>
      </Float>

      {/* Orbital ring 1 */}
      <mesh ref={ring1Ref} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[2, 0.02, 16, 100]} />
        <meshBasicMaterial color={colors.accent} transparent opacity={0.4} />
      </mesh>

      {/* Orbital ring 2 */}
      <mesh ref={ring2Ref}>
        <torusGeometry args={[2.5, 0.015, 16, 100]} />
        <meshBasicMaterial color={colors.accent2} transparent opacity={0.3} />
      </mesh>

      {/* Orbital ring 3 */}
      <mesh ref={ring3Ref}>
        <torusGeometry args={[3, 0.01, 16, 100]} />
        <meshBasicMaterial color={colors.ok} transparent opacity={0.2} />
      </mesh>

      {/* Orbiting particles */}
      {[...Array(8)].map((_, i) => {
        const angle = (i / 8) * Math.PI * 2
        const radius = 2 + (i % 3) * 0.5
        return (
          <OrbitingParticle
            key={i}
            radius={radius}
            angle={angle}
            speed={0.3 + (i % 3) * 0.1}
            color={i % 2 === 0 ? colors.accent : colors.accent2}
          />
        )
      })}

      {/* Central glow */}
      <pointLight color={colors.accent} intensity={3} distance={15} />
    </group>
  )
}

function OrbitingParticle({ radius, angle, speed, color }: {
  radius: number
  angle: number
  speed: number
  color: string
}) {
  const meshRef = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (meshRef.current) {
      const t = state.clock.elapsedTime * speed + angle
      meshRef.current.position.x = Math.cos(t) * radius
      meshRef.current.position.z = Math.sin(t) * radius
      meshRef.current.position.y = Math.sin(t * 2) * 0.5
    }
  })

  return (
    <mesh ref={meshRef}>
      <sphereGeometry args={[0.06, 16, 16]} />
      <meshBasicMaterial color={color} />
    </mesh>
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

      <AICore colors={colors} />
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

export function HeroSection({ onLaunch }: { onLaunch: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const colors = useThemeColors()
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ['start start', 'end start']
  })

  const y = useTransform(scrollYProgress, [0, 1], ['0%', '20%'])
  const opacity = useTransform(scrollYProgress, [0, 0.5], [1, 0])

  return (
    <section
      ref={containerRef}
      className="relative min-h-screen flex items-center justify-center overflow-hidden"
      style={{ backgroundColor: colors.bg }}
    >
      {/* 3D Canvas Background */}
      <div className="absolute inset-0 z-0">
        <Canvas
          camera={{ position: [0, 0, 10], fov: 45 }}
          dpr={[1, 2]}
        >
          <Suspense fallback={null}>
            <Scene3D colors={colors} />
          </Suspense>
        </Canvas>
      </div>

      {/* Gradient overlays for depth */}
      <div className="absolute inset-0 z-[1] pointer-events-none">
        {/* Top fade */}
        <div
          className="absolute top-0 left-0 right-0 h-32"
          style={{ background: `linear-gradient(to bottom, ${colors.bg}, transparent)` }}
        />
        {/* Bottom fade */}
        <div
          className="absolute bottom-0 left-0 right-0 h-48"
          style={{ background: `linear-gradient(to top, ${colors.bg}, transparent)` }}
        />
        {/* Center radial for text readability */}
        <div
          className="absolute inset-0"
          style={{
            background: `radial-gradient(ellipse at center, ${colors.bg}66 0%, transparent 60%)`
          }}
        />
      </div>

      {/* Content */}
      <motion.div
        className="relative z-10 max-w-5xl mx-auto px-6 text-center"
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
          className="text-4xl sm:text-5xl md:text-6xl lg:text-7xl font-bold tracking-tight mb-6 leading-[1.05]"
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
          className="text-white/50 text-lg md:text-xl max-w-2xl mx-auto mb-10 leading-relaxed"
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
            onClick={onLaunch}
            className="inline-flex items-center gap-2 px-8 py-4 text-white font-semibold rounded-xl border border-white/20 bg-white/[0.03] backdrop-blur-sm hover:bg-white/[0.08] hover:border-white/30 transition-all duration-200"
          >
            Request Demo
          </button>
        </motion.div>

        {/* Stats */}
        <motion.div
          className="grid grid-cols-2 md:grid-cols-4 gap-8 md:gap-12 max-w-3xl mx-auto mb-16"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.4 }}
        >
          {[
            { value: '9', label: 'MCP Servers' },
            { value: '48', label: 'AI Tools' },
            { value: '5', label: 'LLM Providers' },
            { value: '99.9%', label: 'Uptime SLA' }
          ].map((stat, i) => (
            <div key={i} className="text-center">
              <div className="text-3xl md:text-4xl font-bold text-white mb-1">
                {stat.value}
              </div>
              <div className="text-white/40 text-sm">{stat.label}</div>
            </div>
          ))}
        </motion.div>

        {/* Integrations */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.5 }}
        >
          <p className="text-white/30 text-sm uppercase tracking-wider mb-6">
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
                <span className="text-white/60 text-sm font-medium">{tool.name}</span>
              </motion.div>
            ))}
          </div>
        </motion.div>
      </motion.div>

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
