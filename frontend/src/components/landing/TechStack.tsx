import { useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Float, MeshTransmissionMaterial } from '@react-three/drei'
import { motion } from 'framer-motion'
import * as THREE from 'three'

const TECH_STACK = [
  { name: 'Docker', color: '#0DB7ED', shape: 'box' },
  { name: 'Kubernetes', color: '#326CE5', shape: 'icosahedron' },
  { name: 'Terraform', color: '#7B42BC', shape: 'icosahedron' },
  { name: 'AWS', color: '#FF9900', shape: 'box' },
  { name: 'Azure', color: '#0078D4', shape: 'sphere' },
  { name: 'GCP', color: '#4285F4', shape: 'sphere' },
  { name: 'Linux', color: '#FCC624', shape: 'sphere' },
  { name: 'GitHub', color: '#FFFFFF', shape: 'icosahedron' },
  { name: 'Prometheus', color: '#E6522C', shape: 'icosahedron' },
  { name: 'Grafana', color: '#F46800', shape: 'box' },
  { name: 'Vault', color: '#000000', shape: 'box' },
  { name: 'Redis', color: '#DC382D', shape: 'sphere' },
  { name: 'PostgreSQL', color: '#336791', shape: 'box' },
  { name: 'Helm', color: '#0F1689', shape: 'sphere' },
  { name: 'ELK', color: '#00BFB3', shape: 'icosahedron' }
]

function Tech3DObject({
  shape,
  color,
  position,
  rotationSpeed = 0.5
}: {
  shape: string
  color: string
  position: [number, number, number]
  rotationSpeed?: number
}) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.y = state.clock.elapsedTime * rotationSpeed
      ref.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.5) * 0.2
    }
  })

  const Geometry = () => {
    switch (shape) {
      case 'box':
        return <boxGeometry args={[0.8, 0.8, 0.8]} />
      case 'icosahedron':
        return <icosahedronGeometry args={[0.5, 0]} />
      case 'sphere':
      default:
        return <sphereGeometry args={[0.5, 32, 32]} />
    }
  }

  return (
    <Float speed={2} rotationIntensity={0.3} floatIntensity={0.5}>
      <mesh ref={ref} position={position}>
        <Geometry />
        <MeshTransmissionMaterial
          color={color}
          transmission={0.6}
          thickness={0.5}
          roughness={0.2}
          chromaticAberration={0.05}
        />
      </mesh>
    </Float>
  )
}

function TechScene() {
  const positions: [number, number, number][] = [
    [-3, 2, 0], [-1, 2.5, -1], [1, 2, 1], [3, 2.5, 0],
    [-4, 0, 1], [-2, 0.5, -1], [0, 0, 0], [2, 0.5, 1], [4, 0, -1],
    [-3, -2, -1], [-1, -2.5, 1], [1, -2, -1], [3, -2.5, 0],
    [0, 3, 0], [0, -3, 0]
  ]

  return (
    <>
      <ambientLight intensity={0.5} />
      <pointLight position={[10, 10, 10]} intensity={1} color="#00E7FF" />
      <pointLight position={[-10, -10, -10]} intensity={0.5} color="#7C3AED" />

      {TECH_STACK.map((tech, i) => (
        <Tech3DObject
          key={tech.name}
          shape={tech.shape}
          color={tech.color}
          position={positions[i] || [0, 0, 0]}
          rotationSpeed={0.3 + (i % 3) * 0.2}
        />
      ))}
    </>
  )
}

function TechCard({
  tech,
  index
}: {
  tech: typeof TECH_STACK[0]
  index: number
}) {
  return (
    <motion.div
      className="floating-card p-4 flex items-center gap-3 cursor-pointer"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
      whileHover={{ scale: 1.05, y: -5 }}
    >
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center"
        style={{ backgroundColor: `${tech.color}20` }}
      >
        <div
          className="w-4 h-4 rounded"
          style={{ backgroundColor: tech.color }}
        />
      </div>
      <span className="font-medium text-white">{tech.name}</span>
    </motion.div>
  )
}

export function TechStack() {
  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background-secondary to-background" />

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
            Technology
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Powered by <span className="gradient-text">industry leaders</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Seamless integration with the tools you already use
          </motion.p>
        </div>

        {/* 3D visualization */}
        <motion.div
          className="h-[400px] mb-16"
          initial={{ opacity: 0, scale: 0.9 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8 }}
        >
          <Canvas camera={{ position: [0, 0, 10], fov: 50 }}>
            <TechScene />
          </Canvas>
        </motion.div>

        {/* Tech grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {TECH_STACK.map((tech, i) => (
            <TechCard key={tech.name} tech={tech} index={i} />
          ))}
        </div>
      </div>
    </section>
  )
}
