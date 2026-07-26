import { useRef, Suspense } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Stars, Sphere, Box, Float } from '@react-three/drei'
import { motion } from 'framer-motion'
import {
  Github,
  Mail,
  ArrowUpRight,
  Sparkles
} from 'lucide-react'
import * as THREE from 'three'

// Cyber city buildings
function CityBuilding({ position, height, width }: {
  position: [number, number, number]
  height: number
  width: number
}) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      const material = ref.current.material as THREE.MeshStandardMaterial
      material.emissiveIntensity = 0.2 + Math.sin(state.clock.elapsedTime * 2 + position[0]) * 0.1
    }
  })

  return (
    <Box ref={ref} args={[width, height, width]} position={position}>
      <meshStandardMaterial
        color="#0a1628"
        emissive="#00E7FF"
        emissiveIntensity={0.2}
        metalness={0.8}
        roughness={0.2}
      />
    </Box>
  )
}

// Data center
function DataCenter() {
  const buildings: Array<{ pos: [number, number, number]; h: number; w: number }> = [
    { pos: [-4, 0.5, -2], h: 1, w: 0.8 },
    { pos: [-3, 0.75, -1], h: 1.5, w: 0.6 },
    { pos: [-2, 1, -2], h: 2, w: 0.7 },
    { pos: [-1, 0.5, -1.5], h: 1, w: 0.5 },
    { pos: [0, 1.25, -2], h: 2.5, w: 0.8 },
    { pos: [1, 0.75, -1], h: 1.5, w: 0.6 },
    { pos: [2, 1, -2], h: 2, w: 0.7 },
    { pos: [3, 0.5, -1.5], h: 1, w: 0.5 },
    { pos: [4, 0.75, -2], h: 1.5, w: 0.6 },
  ]

  return (
    <group position={[0, -2, -5]}>
      {buildings.map((b, i) => (
        <CityBuilding key={i} position={b.pos} height={b.h} width={b.w} />
      ))}
      {/* Ground plane */}
      <Box args={[15, 0.1, 10]} position={[0, -0.05, 0]}>
        <meshStandardMaterial color="#040814" metalness={0.5} roughness={0.8} />
      </Box>
    </group>
  )
}

// Floating drone
function Drone({ position, delay = 0 }: { position: [number, number, number]; delay?: number }) {
  const ref = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.position.x = position[0] + Math.sin(state.clock.elapsedTime * 0.5 + delay) * 2
      ref.current.position.y = position[1] + Math.sin(state.clock.elapsedTime * 0.8 + delay) * 0.5
      ref.current.rotation.y = state.clock.elapsedTime * 0.3
    }
  })

  return (
    <Float speed={3} rotationIntensity={0.2} floatIntensity={0.3}>
      <group ref={ref} position={position}>
        <Box args={[0.3, 0.1, 0.3]}>
          <meshStandardMaterial
            color="#00E7FF"
            emissive="#00E7FF"
            emissiveIntensity={0.5}
          />
        </Box>
      </group>
    </Float>
  )
}

// Satellite
function Satellite({ position }: { position: [number, number, number] }) {
  const ref = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.y = state.clock.elapsedTime * 0.2
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.3} floatIntensity={0.4}>
      <group ref={ref} position={position}>
        <Box args={[0.2, 0.1, 0.2]}>
          <meshStandardMaterial color="#7C3AED" emissive="#7C3AED" emissiveIntensity={0.3} />
        </Box>
        <Box args={[0.8, 0.02, 0.2]} position={[0, 0, 0]}>
          <meshStandardMaterial color="#00FFB3" emissive="#00FFB3" emissiveIntensity={0.3} />
        </Box>
      </group>
    </Float>
  )
}

// AI Robot looking over servers
function AIRobotSilhouette() {
  const ref = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.position.y = -1 + Math.sin(state.clock.elapsedTime * 0.5) * 0.1
    }
  })

  return (
    <group ref={ref} position={[0, -1, -3]}>
      {/* Head */}
      <Sphere args={[0.4, 32, 32]} position={[0, 1.2, 0]}>
        <meshStandardMaterial color="#00E7FF" emissive="#00E7FF" emissiveIntensity={0.3} />
      </Sphere>
      {/* Body */}
      <Box args={[0.6, 0.8, 0.4]} position={[0, 0.5, 0]}>
        <meshStandardMaterial color="#0a1628" emissive="#00E7FF" emissiveIntensity={0.1} />
      </Box>
      {/* Eyes glow */}
      <Sphere args={[0.05, 16, 16]} position={[-0.12, 1.25, 0.35]}>
        <meshBasicMaterial color="#00E7FF" />
      </Sphere>
      <Sphere args={[0.05, 16, 16]} position={[0.12, 1.25, 0.35]}>
        <meshBasicMaterial color="#00E7FF" />
      </Sphere>
    </group>
  )
}

function FooterScene() {
  return (
    <>
      <ambientLight intensity={0.1} />
      <pointLight position={[0, 5, 5]} intensity={1} color="#00E7FF" />
      <pointLight position={[-5, 3, -5]} intensity={0.5} color="#7C3AED" />

      <Stars radius={50} depth={30} count={2000} factor={3} saturation={0} fade speed={1} />

      <DataCenter />
      <AIRobotSilhouette />
      <Drone position={[-3, 1, -2]} delay={0} />
      <Drone position={[3, 1.5, -3]} delay={2} />
      <Satellite position={[4, 3, -4]} />
      <Satellite position={[-4, 2.5, -5]} />
    </>
  )
}

const FOOTER_LINKS = {
  Product: [
    { label: 'Features', href: '#' },
    { label: 'Pricing', href: '#' },
    { label: 'Documentation', href: '#' },
    { label: 'Changelog', href: '#' }
  ],
  Company: [
    { label: 'About', href: '#' },
    { label: 'Blog', href: '#' },
    { label: 'Careers', href: '#' },
    { label: 'Contact', href: 'mailto:ankit25bcs10610@gmail.com' }
  ],
  Resources: [
    { label: 'GitHub', href: 'https://github.com/ankit25bcs10610/DevOps-Copilot' },
    { label: 'Community', href: '#' },
    { label: 'Support', href: 'mailto:ankit25bcs10610@gmail.com' },
    { label: 'Status', href: '#' }
  ],
  Legal: [
    { label: 'Privacy', href: '#' },
    { label: 'Terms', href: '#' },
    { label: 'Security', href: '#' }
  ]
}

const SOCIAL_LINKS = [
  { icon: Github, href: 'https://github.com/ankit25bcs10610', label: 'GitHub' },
  { icon: Mail, href: 'mailto:ankit25bcs10610@gmail.com', label: 'Email' }
]

export function Footer({ onLaunch }: { onLaunch: () => void }) {
  return (
    <footer className="relative overflow-hidden">
      {/* 3D Scene Background */}
      <div className="absolute inset-0 h-[400px]">
        <Canvas camera={{ position: [0, 0, 5], fov: 60 }}>
          <Suspense fallback={null}>
            <FooterScene />
          </Suspense>
        </Canvas>
        {/* Gradient overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-background via-background/80 to-transparent" />
      </div>

      {/* Content */}
      <div className="relative z-10 pt-64 pb-12">
        <div className="container-wide">
          {/* CTA Section */}
          <motion.div
            className="floating-card p-12 text-center mb-16"
            initial={{ opacity: 0, y: 40 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
          >
            <h2 className="font-display text-3xl md:text-4xl font-bold text-white mb-4">
              Ready to automate your <span className="gradient-text">DevOps</span>?
            </h2>
            <p className="text-white/60 text-lg mb-8 max-w-xl mx-auto">
              Join thousands of engineering teams using AI to ship faster
            </p>
            <motion.button
              className="
                inline-flex items-center gap-2 px-8 py-4
                bg-gradient-to-r from-primary to-accent
                text-background font-display font-semibold rounded-xl
                shadow-glow-primary transition-all
              "
              onClick={onLaunch}
              whileHover={{ scale: 1.05, boxShadow: '0 0 60px rgba(0, 231, 255, 0.6)' }}
              whileTap={{ scale: 0.98 }}
            >
              <Sparkles className="w-5 h-5" />
              Start Free Today
              <ArrowUpRight className="w-5 h-5" />
            </motion.button>
          </motion.div>

          {/* Footer links */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-8 mb-12">
            {/* Brand */}
            <div className="col-span-2">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-accent flex items-center justify-center">
                  <Sparkles className="w-5 h-5 text-background" />
                </div>
                <span className="font-display text-xl font-bold text-white">
                  DevOps Copilot
                </span>
              </div>
              <p className="text-white/60 text-sm mb-4 max-w-xs">
                AI-powered DevOps automation for modern engineering teams.
              </p>
              <a
                href="mailto:ankit25bcs10610@gmail.com"
                className="flex items-center gap-2 text-white/50 text-sm hover:text-primary transition-colors"
              >
                <Mail className="w-4 h-4" />
                <span>ankit25bcs10610@gmail.com</span>
              </a>
            </div>

            {/* Links */}
            {Object.entries(FOOTER_LINKS).map(([category, links]) => (
              <div key={category}>
                <h4 className="font-display font-semibold text-white mb-4">
                  {category}
                </h4>
                <ul className="space-y-2">
                  {links.map((link) => (
                    <li key={link.label}>
                      <a
                        href={link.href}
                        className="text-white/60 text-sm hover:text-primary transition-colors"
                      >
                        {link.label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          {/* Bottom bar */}
          <div className="flex flex-col md:flex-row items-center justify-between pt-8 border-t border-glass-border">
            <p className="text-white/50 text-sm mb-4 md:mb-0">
              © 2024 DevOps Copilot. Built by{' '}
              <a
                href="https://github.com/ankit25bcs10610"
                target="_blank"
                rel="noreferrer"
                className="text-primary hover:underline"
              >
                Ankit Pandey
              </a>
            </p>

            {/* Social links */}
            <div className="flex items-center gap-4">
              {SOCIAL_LINKS.map((social) => {
                const Icon = social.icon
                return (
                  <a
                    key={social.label}
                    href={social.href}
                    target="_blank"
                    rel="noreferrer"
                    className="p-2 rounded-lg glass hover:bg-white/10 transition-colors"
                    aria-label={social.label}
                  >
                    <Icon className="w-5 h-5 text-white/70" />
                  </a>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </footer>
  )
}
