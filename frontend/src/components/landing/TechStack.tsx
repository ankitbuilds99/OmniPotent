import { useRef, useMemo } from 'react'
import { Canvas, useFrame, useLoader } from '@react-three/fiber'
import { Float, Text, Billboard } from '@react-three/drei'
import { motion } from 'framer-motion'
import * as THREE from 'three'

const TECH_STACK = [
  { name: 'Docker', color: '#2496ED', icon: 'docker' },
  { name: 'Kubernetes', color: '#326CE5', icon: 'kubernetes' },
  { name: 'Terraform', color: '#844FBA', icon: 'terraform' },
  { name: 'AWS', color: '#FF9900', icon: 'amazonwebservices' },
  { name: 'Azure', color: '#0078D4', icon: 'microsoftazure' },
  { name: 'GCP', color: '#4285F4', icon: 'googlecloud' },
  { name: 'Linux', color: '#FCC624', icon: 'linux' },
  { name: 'GitHub', color: '#FFFFFF', icon: 'github' },
  { name: 'Prometheus', color: '#E6522C', icon: 'prometheus' },
  { name: 'Grafana', color: '#F46800', icon: 'grafana' },
  { name: 'Vault', color: '#FFEC6E', icon: 'vault' },
  { name: 'Redis', color: '#FF4438', icon: 'redis' },
  { name: 'PostgreSQL', color: '#4169E1', icon: 'postgresql' },
  { name: 'Helm', color: '#0F1689', icon: 'helm' },
  { name: 'Elastic', color: '#005571', icon: 'elastic' }
]

function LogoSprite({
  tech,
  position,
  scale = 1
}: {
  tech: typeof TECH_STACK[0]
  position: [number, number, number]
  scale?: number
}) {
  const meshRef = useRef<THREE.Group>(null)
  const initialY = position[1]

  useFrame((state) => {
    if (meshRef.current) {
      meshRef.current.position.y = initialY + Math.sin(state.clock.elapsedTime + position[0]) * 0.3
      meshRef.current.rotation.y = Math.sin(state.clock.elapsedTime * 0.5 + position[0]) * 0.2
    }
  })

  return (
    <Float speed={1.5} rotationIntensity={0.2} floatIntensity={0.3}>
      <group ref={meshRef} position={position}>
        <Billboard follow={true} lockX={false} lockY={false} lockZ={false}>
          {/* Glowing background circle */}
          <mesh>
            <circleGeometry args={[0.7 * scale, 32]} />
            <meshBasicMaterial
              color={tech.color}
              transparent
              opacity={0.15}
            />
          </mesh>
          {/* Inner circle */}
          <mesh position={[0, 0, 0.01]}>
            <circleGeometry args={[0.55 * scale, 32]} />
            <meshBasicMaterial
              color="#0a0f1a"
              transparent
              opacity={0.9}
            />
          </mesh>
          {/* Border ring */}
          <mesh position={[0, 0, 0.02]}>
            <ringGeometry args={[0.5 * scale, 0.55 * scale, 32]} />
            <meshBasicMaterial
              color={tech.color}
              transparent
              opacity={0.6}
            />
          </mesh>
          {/* Tech name label */}
          <Text
            position={[0, -0.9 * scale, 0.03]}
            fontSize={0.18 * scale}
            color={tech.color}
            anchorX="center"
            anchorY="middle"
            font="/fonts/inter-medium.woff"
          >
            {tech.name}
          </Text>
        </Billboard>
        {/* Glow effect */}
        <pointLight
          color={tech.color}
          intensity={0.5}
          distance={3}
        />
      </group>
    </Float>
  )
}

function OrbitingLogo({
  tech,
  radius,
  speed,
  offset,
  yOffset = 0
}: {
  tech: typeof TECH_STACK[0]
  radius: number
  speed: number
  offset: number
  yOffset?: number
}) {
  const groupRef = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (groupRef.current) {
      const angle = state.clock.elapsedTime * speed + offset
      groupRef.current.position.x = Math.cos(angle) * radius
      groupRef.current.position.z = Math.sin(angle) * radius
      groupRef.current.position.y = yOffset + Math.sin(state.clock.elapsedTime + offset) * 0.5
    }
  })

  return (
    <group ref={groupRef}>
      <Billboard follow={true}>
        {/* Glowing orb background */}
        <mesh>
          <circleGeometry args={[0.6, 32]} />
          <meshBasicMaterial
            color={tech.color}
            transparent
            opacity={0.2}
          />
        </mesh>
        {/* Inner dark circle */}
        <mesh position={[0, 0, 0.01]}>
          <circleGeometry args={[0.45, 32]} />
          <meshBasicMaterial
            color="#080c14"
            transparent
            opacity={0.95}
          />
        </mesh>
        {/* Colored ring */}
        <mesh position={[0, 0, 0.02]}>
          <ringGeometry args={[0.4, 0.45, 32]} />
          <meshBasicMaterial
            color={tech.color}
            transparent
            opacity={0.8}
          />
        </mesh>
        {/* Tech initial/symbol */}
        <Text
          position={[0, 0, 0.03]}
          fontSize={0.25}
          color={tech.color}
          anchorX="center"
          anchorY="middle"
          fontWeight="bold"
        >
          {tech.name.charAt(0)}
        </Text>
        {/* Tech name below */}
        <Text
          position={[0, -0.7, 0.03]}
          fontSize={0.12}
          color="white"
          anchorX="center"
          anchorY="middle"
        >
          {tech.name}
        </Text>
      </Billboard>
      <pointLight color={tech.color} intensity={0.3} distance={2} />
    </group>
  )
}

function TechScene() {
  const innerRing = TECH_STACK.slice(0, 5)
  const middleRing = TECH_STACK.slice(5, 10)
  const outerRing = TECH_STACK.slice(10, 15)

  return (
    <>
      <ambientLight intensity={0.3} />
      <pointLight position={[0, 0, 5]} intensity={1} color="#00E7FF" />

      {/* Center glow */}
      <mesh position={[0, 0, -2]}>
        <sphereGeometry args={[1.5, 32, 32]} />
        <meshBasicMaterial color="#00E7FF" transparent opacity={0.05} />
      </mesh>

      {/* Inner ring - slower, closer */}
      {innerRing.map((tech, i) => (
        <OrbitingLogo
          key={tech.name}
          tech={tech}
          radius={2.5}
          speed={0.3}
          offset={(i / innerRing.length) * Math.PI * 2}
          yOffset={0}
        />
      ))}

      {/* Middle ring */}
      {middleRing.map((tech, i) => (
        <OrbitingLogo
          key={tech.name}
          tech={tech}
          radius={4}
          speed={-0.2}
          offset={(i / middleRing.length) * Math.PI * 2}
          yOffset={0.5}
        />
      ))}

      {/* Outer ring - slowest, furthest */}
      {outerRing.map((tech, i) => (
        <OrbitingLogo
          key={tech.name}
          tech={tech}
          radius={5.5}
          speed={0.15}
          offset={(i / outerRing.length) * Math.PI * 2}
          yOffset={-0.5}
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
      className="floating-card p-4 flex items-center gap-3 cursor-pointer group"
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
      whileHover={{ scale: 1.05, y: -5 }}
    >
      <div
        className="w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-300 group-hover:scale-110"
        style={{ backgroundColor: `${tech.color}15`, boxShadow: `0 0 20px ${tech.color}20` }}
      >
        <img
          src={`https://cdn.simpleicons.org/${tech.icon}/${tech.color.replace('#', '')}`}
          alt={tech.name}
          className="w-6 h-6"
          loading="lazy"
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

        {/* 3D visualization - Orbiting logos */}
        <motion.div
          className="h-[500px] mb-16"
          initial={{ opacity: 0, scale: 0.9 }}
          whileInView={{ opacity: 1, scale: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8 }}
        >
          <Canvas camera={{ position: [0, 2, 12], fov: 45 }}>
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
