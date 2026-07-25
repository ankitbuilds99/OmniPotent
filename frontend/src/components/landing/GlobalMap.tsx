import { useRef, useMemo, useState } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Sphere, Line, OrbitControls, Stars } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'
import * as THREE from 'three'

const CLOUD_REGIONS = [
  {
    name: 'AWS Mumbai',
    provider: 'aws',
    lat: 19.076,
    lng: 72.8777,
    color: '#FF9900',
    icon: 'amazonwebservices',
    metrics: { latency: '12ms', traffic: '2.3k/s', services: 24, health: 99.9, cpu: 45, memory: 62 }
  },
  {
    name: 'AWS Virginia',
    provider: 'aws',
    lat: 37.4316,
    lng: -78.6569,
    color: '#FF9900',
    icon: 'amazonwebservices',
    metrics: { latency: '8ms', traffic: '5.1k/s', services: 48, health: 99.99, cpu: 38, memory: 55 }
  },
  {
    name: 'Azure Singapore',
    provider: 'azure',
    lat: 1.3521,
    lng: 103.8198,
    color: '#0078D4',
    icon: 'microsoftazure',
    metrics: { latency: '18ms', traffic: '1.8k/s', services: 18, health: 99.8, cpu: 52, memory: 68 }
  },
  {
    name: 'Google Tokyo',
    provider: 'gcp',
    lat: 35.6762,
    lng: 139.6503,
    color: '#4285F4',
    icon: 'googlecloud',
    metrics: { latency: '15ms', traffic: '3.2k/s', services: 32, health: 99.95, cpu: 41, memory: 58 }
  },
  {
    name: 'AWS Frankfurt',
    provider: 'aws',
    lat: 50.1109,
    lng: 8.6821,
    color: '#FF9900',
    icon: 'amazonwebservices',
    metrics: { latency: '22ms', traffic: '4.5k/s', services: 42, health: 99.92, cpu: 48, memory: 64 }
  },
  {
    name: 'GCP London',
    provider: 'gcp',
    lat: 51.5074,
    lng: -0.1278,
    color: '#4285F4',
    icon: 'googlecloud',
    metrics: { latency: '25ms', traffic: '3.8k/s', services: 36, health: 99.88, cpu: 55, memory: 71 }
  }
]

function latLngToVector3(lat: number, lng: number, radius: number): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180)
  const theta = (lng + 180) * (Math.PI / 180)

  const x = -(radius * Math.sin(phi) * Math.cos(theta))
  const y = radius * Math.cos(phi)
  const z = radius * Math.sin(phi) * Math.sin(theta)

  return new THREE.Vector3(x, y, z)
}

function Earth() {
  const earthRef = useRef<THREE.Group>(null)
  const atmosphereRef = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (earthRef.current) {
      earthRef.current.rotation.y = state.clock.elapsedTime * 0.03
    }
    if (atmosphereRef.current) {
      atmosphereRef.current.rotation.y = state.clock.elapsedTime * 0.02
      atmosphereRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.1) * 0.05
    }
  })

  return (
    <group ref={earthRef}>
      {/* Core sphere - dark with subtle gradient */}
      <Sphere args={[2, 64, 64]}>
        <meshStandardMaterial
          color="#050a15"
          roughness={1}
          metalness={0}
        />
      </Sphere>

      {/* Main wireframe grid - latitude/longitude lines */}
      <Sphere args={[2.01, 48, 24]}>
        <meshBasicMaterial
          color="#00E7FF"
          wireframe
          transparent
          opacity={0.08}
        />
      </Sphere>

      {/* Secondary finer grid */}
      <Sphere args={[2.02, 96, 48]}>
        <meshBasicMaterial
          color="#7C3AED"
          wireframe
          transparent
          opacity={0.03}
        />
      </Sphere>

      {/* Atmosphere glow - inner */}
      <Sphere args={[2.15, 64, 64]} ref={atmosphereRef}>
        <meshBasicMaterial
          color="#00E7FF"
          transparent
          opacity={0.04}
          side={THREE.BackSide}
        />
      </Sphere>

      {/* Atmosphere glow - outer */}
      <Sphere args={[2.3, 64, 64]}>
        <meshBasicMaterial
          color="#00E7FF"
          transparent
          opacity={0.02}
          side={THREE.BackSide}
        />
      </Sphere>

      {/* Equator ring */}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[2.05, 0.005, 16, 100]} />
        <meshBasicMaterial color="#00E7FF" transparent opacity={0.3} />
      </mesh>
    </group>
  )
}

function RegionMarker({
  region,
  onHover,
  onLeave
}: {
  region: typeof CLOUD_REGIONS[0]
  onHover: (region: typeof CLOUD_REGIONS[0]) => void
  onLeave: () => void
}) {
  const position = useMemo(
    () => latLngToVector3(region.lat, region.lng, 2.08),
    [region.lat, region.lng]
  )
  const markerRef = useRef<THREE.Group>(null)
  const ringRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

  useFrame((state) => {
    if (markerRef.current) {
      const scale = hovered ? 1.8 : 1 + Math.sin(state.clock.elapsedTime * 2 + position.x) * 0.15
      markerRef.current.scale.setScalar(scale)
    }
    if (ringRef.current) {
      ringRef.current.rotation.z = state.clock.elapsedTime * 2
      const ringScale = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.2
      ringRef.current.scale.setScalar(ringScale)
    }
  })

  return (
    <group
      ref={markerRef}
      position={position}
      onPointerEnter={() => {
        setHovered(true)
        onHover(region)
        document.body.style.cursor = 'pointer'
      }}
      onPointerLeave={() => {
        setHovered(false)
        onLeave()
        document.body.style.cursor = 'auto'
      }}
    >
      {/* Outer glow */}
      <Sphere args={[0.12, 16, 16]}>
        <meshBasicMaterial
          color={region.color}
          transparent
          opacity={0.15}
        />
      </Sphere>

      {/* Main marker */}
      <Sphere args={[0.06, 16, 16]}>
        <meshBasicMaterial color={region.color} />
      </Sphere>

      {/* Inner bright core */}
      <Sphere args={[0.03, 16, 16]}>
        <meshBasicMaterial color="#ffffff" />
      </Sphere>

      {/* Pulsing ring */}
      <mesh ref={ringRef} rotation={[Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.1, 0.14, 32]} />
        <meshBasicMaterial
          color={region.color}
          transparent
          opacity={hovered ? 0.8 : 0.4}
          side={THREE.DoubleSide}
        />
      </mesh>

      {/* Point light for glow effect */}
      <pointLight
        color={region.color}
        intensity={hovered ? 2 : 0.5}
        distance={1}
      />
    </group>
  )
}

function ConnectionArcs() {
  const arcsRef = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (arcsRef.current) {
      arcsRef.current.rotation.y = state.clock.elapsedTime * 0.03
    }
  })

  const arcs = useMemo(() => {
    const connections: Array<{ start: THREE.Vector3; end: THREE.Vector3; color: string }> = []
    const pairs = [
      [0, 1], [0, 2], [1, 4], [2, 3], [3, 5], [4, 5], [0, 3], [1, 5]
    ]

    pairs.forEach(([i, j]) => {
      const start = latLngToVector3(CLOUD_REGIONS[i].lat, CLOUD_REGIONS[i].lng, 2.08)
      const end = latLngToVector3(CLOUD_REGIONS[j].lat, CLOUD_REGIONS[j].lng, 2.08)
      connections.push({ start, end, color: '#00E7FF' })
    })

    return connections
  }, [])

  return (
    <group ref={arcsRef}>
      {arcs.map((arc, i) => {
        const midPoint = new THREE.Vector3().addVectors(arc.start, arc.end).multiplyScalar(0.5)
        const distance = arc.start.distanceTo(arc.end)
        midPoint.normalize().multiplyScalar(2.08 + distance * 0.4)

        const curve = new THREE.QuadraticBezierCurve3(arc.start, midPoint, arc.end)
        const points = curve.getPoints(50)

        return (
          <Line
            key={i}
            points={points}
            color={arc.color}
            lineWidth={1.5}
            transparent
            opacity={0.4}
          />
        )
      })}
    </group>
  )
}

function DataPackets() {
  const packetsRef = useRef<THREE.Group>(null)
  const packetPositions = useRef<number[]>([0, 0.33, 0.66])

  useFrame((state) => {
    packetPositions.current = packetPositions.current.map(
      (p) => (p + 0.005) % 1
    )
  })

  const pairs = [[0, 1], [2, 3], [4, 5]]

  return (
    <group ref={packetsRef}>
      {pairs.map(([i, j], idx) => {
        const start = latLngToVector3(CLOUD_REGIONS[i].lat, CLOUD_REGIONS[i].lng, 2.08)
        const end = latLngToVector3(CLOUD_REGIONS[j].lat, CLOUD_REGIONS[j].lng, 2.08)
        const midPoint = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5)
        const distance = start.distanceTo(end)
        midPoint.normalize().multiplyScalar(2.08 + distance * 0.4)

        const curve = new THREE.QuadraticBezierCurve3(start, midPoint, end)

        return (
          <DataPacket
            key={idx}
            curve={curve}
            color={CLOUD_REGIONS[i].color}
            offset={idx * 0.33}
          />
        )
      })}
    </group>
  )
}

function DataPacket({
  curve,
  color,
  offset
}: {
  curve: THREE.QuadraticBezierCurve3
  color: string
  offset: number
}) {
  const packetRef = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (packetRef.current) {
      const t = ((state.clock.elapsedTime * 0.15 + offset) % 1)
      const pos = curve.getPoint(t)
      packetRef.current.position.copy(pos)
    }
  })

  return (
    <mesh ref={packetRef}>
      <sphereGeometry args={[0.025, 8, 8]} />
      <meshBasicMaterial color={color} />
    </mesh>
  )
}

function GlobeScene({
  onRegionHover,
  onRegionLeave
}: {
  onRegionHover: (region: typeof CLOUD_REGIONS[0]) => void
  onRegionLeave: () => void
}) {
  return (
    <>
      <ambientLight intensity={0.2} />
      <pointLight position={[10, 10, 10]} intensity={0.8} color="#00E7FF" />
      <pointLight position={[-10, -10, -10]} intensity={0.4} color="#7C3AED" />
      <pointLight position={[0, 0, 8]} intensity={0.3} color="#ffffff" />

      <Stars
        radius={50}
        depth={50}
        count={2000}
        factor={4}
        saturation={0}
        fade
        speed={0.5}
      />

      <Earth />
      <ConnectionArcs />
      <DataPackets />

      {CLOUD_REGIONS.map((region) => (
        <RegionMarker
          key={region.name}
          region={region}
          onHover={onRegionHover}
          onLeave={onRegionLeave}
        />
      ))}

      <OrbitControls
        enableZoom={false}
        enablePan={false}
        autoRotate
        autoRotateSpeed={0.3}
        minPolarAngle={Math.PI / 3}
        maxPolarAngle={Math.PI / 1.5}
      />
    </>
  )
}

function RegionCard({
  region,
  isActive,
  onHover,
  onLeave
}: {
  region: typeof CLOUD_REGIONS[0]
  isActive: boolean
  onHover: () => void
  onLeave: () => void
}) {
  return (
    <motion.div
      className={`
        relative overflow-hidden rounded-xl p-4 cursor-pointer transition-all duration-300
        ${isActive
          ? 'bg-gradient-to-br from-white/10 to-white/5 border border-white/20 shadow-lg'
          : 'bg-white/5 border border-white/5 hover:bg-white/8 hover:border-white/10'
        }
      `}
      whileHover={{ scale: 1.02, y: -2 }}
      onMouseEnter={onHover}
      onMouseLeave={onLeave}
    >
      {isActive && (
        <div
          className="absolute inset-0 opacity-10"
          style={{
            background: `radial-gradient(circle at 30% 30%, ${region.color}, transparent 70%)`
          }}
        />
      )}
      <div className="relative flex items-center gap-3">
        <div
          className="w-10 h-10 rounded-lg flex items-center justify-center"
          style={{ backgroundColor: `${region.color}20` }}
        >
          <img
            src={`https://cdn.simpleicons.org/${region.icon}/${region.color.replace('#', '')}`}
            alt={region.provider}
            className="w-5 h-5"
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="font-medium text-white truncate">{region.name}</div>
          <div className="text-xs text-white/50 flex items-center gap-2">
            <span className="flex items-center gap-1">
              <span
                className="w-1.5 h-1.5 rounded-full animate-pulse"
                style={{ backgroundColor: '#00FFB3' }}
              />
              {region.metrics.health}%
            </span>
            <span>•</span>
            <span>{region.metrics.latency}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm font-semibold" style={{ color: region.color }}>
            {region.metrics.traffic}
          </div>
          <div className="text-xs text-white/40">{region.metrics.services} services</div>
        </div>
      </div>
    </motion.div>
  )
}

function MetricsPanel({ region }: { region: typeof CLOUD_REGIONS[0] }) {
  return (
    <motion.div
      className="bg-gradient-to-br from-white/10 to-white/5 backdrop-blur-xl rounded-2xl p-6 border border-white/10"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -20 }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center gap-3 mb-5">
        <div
          className="w-12 h-12 rounded-xl flex items-center justify-center"
          style={{ backgroundColor: `${region.color}20` }}
        >
          <img
            src={`https://cdn.simpleicons.org/${region.icon}/${region.color.replace('#', '')}`}
            alt={region.provider}
            className="w-7 h-7"
          />
        </div>
        <div>
          <h3 className="font-semibold text-white text-lg">{region.name}</h3>
          <div className="flex items-center gap-1.5 text-sm">
            <span
              className="w-2 h-2 rounded-full animate-pulse"
              style={{ backgroundColor: '#00FFB3' }}
            />
            <span className="text-green-400">Operational</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white/5 rounded-lg p-3">
          <div className="text-white/50 text-xs uppercase tracking-wider mb-1">Latency</div>
          <div className="text-2xl font-bold text-white">{region.metrics.latency}</div>
        </div>
        <div className="bg-white/5 rounded-lg p-3">
          <div className="text-white/50 text-xs uppercase tracking-wider mb-1">Traffic</div>
          <div className="text-2xl font-bold" style={{ color: region.color }}>{region.metrics.traffic}</div>
        </div>
        <div className="bg-white/5 rounded-lg p-3">
          <div className="text-white/50 text-xs uppercase tracking-wider mb-1">Services</div>
          <div className="text-2xl font-bold text-white">{region.metrics.services}</div>
        </div>
        <div className="bg-white/5 rounded-lg p-3">
          <div className="text-white/50 text-xs uppercase tracking-wider mb-1">Health</div>
          <div className="text-2xl font-bold text-green-400">{region.metrics.health}%</div>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        <div>
          <div className="flex justify-between text-sm mb-1.5">
            <span className="text-white/60">CPU Usage</span>
            <span className="text-white font-medium">{region.metrics.cpu}%</span>
          </div>
          <div className="h-2 bg-white/10 rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full"
              style={{ backgroundColor: region.color }}
              initial={{ width: 0 }}
              animate={{ width: `${region.metrics.cpu}%` }}
              transition={{ duration: 0.8, ease: 'easeOut' }}
            />
          </div>
        </div>
        <div>
          <div className="flex justify-between text-sm mb-1.5">
            <span className="text-white/60">Memory</span>
            <span className="text-white font-medium">{region.metrics.memory}%</span>
          </div>
          <div className="h-2 bg-white/10 rounded-full overflow-hidden">
            <motion.div
              className="h-full rounded-full bg-purple-500"
              initial={{ width: 0 }}
              animate={{ width: `${region.metrics.memory}%` }}
              transition={{ duration: 0.8, ease: 'easeOut', delay: 0.1 }}
            />
          </div>
        </div>
      </div>
    </motion.div>
  )
}

export function GlobalMap() {
  const [hoveredRegion, setHoveredRegion] = useState<typeof CLOUD_REGIONS[0] | null>(null)
  const [selectedRegion, setSelectedRegion] = useState<typeof CLOUD_REGIONS[0] | null>(CLOUD_REGIONS[0])

  const activeRegion = hoveredRegion || selectedRegion

  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background gradient */}
      <div className="absolute inset-0 bg-gradient-to-b from-background to-[#030508]" />
      <div
        className="absolute inset-0 opacity-30"
        style={{
          background: 'radial-gradient(ellipse at 30% 50%, rgba(0, 231, 255, 0.1), transparent 50%)'
        }}
      />

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
            Global Infrastructure
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            Deploy <span className="gradient-text">anywhere</span> in the world
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Multi-cloud infrastructure across AWS, Azure, and GCP with real-time monitoring
          </motion.p>
        </div>

        {/* Main content */}
        <div className="grid lg:grid-cols-2 gap-8 items-center">
          {/* 3D Globe */}
          <motion.div
            className="aspect-square max-w-[600px] mx-auto w-full"
            initial={{ opacity: 0, scale: 0.9 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
          >
            <Canvas camera={{ position: [0, 0, 5.5], fov: 45 }}>
              <GlobeScene
                onRegionHover={setHoveredRegion}
                onRegionLeave={() => setHoveredRegion(null)}
              />
            </Canvas>
          </motion.div>

          {/* Info Panel */}
          <motion.div
            className="space-y-4"
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, delay: 0.3 }}
          >
            <AnimatePresence mode="wait">
              {activeRegion && (
                <MetricsPanel key={activeRegion.name} region={activeRegion} />
              )}
            </AnimatePresence>

            {/* Region cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {CLOUD_REGIONS.map((region) => (
                <RegionCard
                  key={region.name}
                  region={region}
                  isActive={activeRegion?.name === region.name}
                  onHover={() => {
                    setHoveredRegion(region)
                    setSelectedRegion(region)
                  }}
                  onLeave={() => setHoveredRegion(null)}
                />
              ))}
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  )
}
