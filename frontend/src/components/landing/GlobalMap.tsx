import { useRef, useMemo, useState } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Sphere, Line, OrbitControls } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'
import * as THREE from 'three'

const CLOUD_REGIONS = [
  {
    name: 'AWS Mumbai',
    provider: 'aws',
    lat: 19.076,
    lng: 72.8777,
    color: '#FF9900',
    metrics: { latency: '12ms', traffic: '2.3k/s', services: 24, health: 99.9, cpu: 45, memory: 62 }
  },
  {
    name: 'AWS Virginia',
    provider: 'aws',
    lat: 37.4316,
    lng: -78.6569,
    color: '#FF9900',
    metrics: { latency: '8ms', traffic: '5.1k/s', services: 48, health: 99.99, cpu: 38, memory: 55 }
  },
  {
    name: 'Azure Singapore',
    provider: 'azure',
    lat: 1.3521,
    lng: 103.8198,
    color: '#0078D4',
    metrics: { latency: '18ms', traffic: '1.8k/s', services: 18, health: 99.8, cpu: 52, memory: 68 }
  },
  {
    name: 'Google Tokyo',
    provider: 'gcp',
    lat: 35.6762,
    lng: 139.6503,
    color: '#4285F4',
    metrics: { latency: '15ms', traffic: '3.2k/s', services: 32, health: 99.95, cpu: 41, memory: 58 }
  },
  {
    name: 'AWS Frankfurt',
    provider: 'aws',
    lat: 50.1109,
    lng: 8.6821,
    color: '#FF9900',
    metrics: { latency: '22ms', traffic: '4.5k/s', services: 42, health: 99.92, cpu: 48, memory: 64 }
  },
  {
    name: 'GCP London',
    provider: 'gcp',
    lat: 51.5074,
    lng: -0.1278,
    color: '#4285F4',
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
  const earthRef = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (earthRef.current) {
      earthRef.current.rotation.y = state.clock.elapsedTime * 0.05
    }
  })

  return (
    <group>
      {/* Earth sphere */}
      <Sphere ref={earthRef} args={[2, 64, 64]}>
        <meshStandardMaterial
          color="#0a1628"
          roughness={0.8}
          metalness={0.2}
          transparent
          opacity={0.9}
        />
      </Sphere>

      {/* Wireframe overlay */}
      <Sphere args={[2.01, 32, 32]}>
        <meshBasicMaterial
          color="#00E7FF"
          wireframe
          transparent
          opacity={0.1}
        />
      </Sphere>

      {/* Atmosphere glow */}
      <Sphere args={[2.1, 32, 32]}>
        <meshBasicMaterial
          color="#00E7FF"
          transparent
          opacity={0.05}
          side={THREE.BackSide}
        />
      </Sphere>
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
    () => latLngToVector3(region.lat, region.lng, 2.05),
    [region.lat, region.lng]
  )
  const markerRef = useRef<THREE.Group>(null)
  const [hovered, setHovered] = useState(false)

  useFrame((state) => {
    if (markerRef.current) {
      const scale = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.1
      markerRef.current.scale.setScalar(hovered ? 1.5 : scale)
    }
  })

  return (
    <group
      ref={markerRef}
      position={position}
      onPointerEnter={() => {
        setHovered(true)
        onHover(region)
      }}
      onPointerLeave={() => {
        setHovered(false)
        onLeave()
      }}
    >
      {/* Marker point */}
      <Sphere args={[0.05, 16, 16]}>
        <meshBasicMaterial color={region.color} />
      </Sphere>

      {/* Pulsing ring */}
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.08, 0.12, 32]} />
        <meshBasicMaterial
          color={region.color}
          transparent
          opacity={0.5}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  )
}

function ConnectionArcs() {
  const arcs = useMemo(() => {
    const connections: Array<{ start: THREE.Vector3; end: THREE.Vector3; color: string }> = []

    for (let i = 0; i < CLOUD_REGIONS.length; i++) {
      for (let j = i + 1; j < CLOUD_REGIONS.length; j++) {
        const start = latLngToVector3(CLOUD_REGIONS[i].lat, CLOUD_REGIONS[i].lng, 2.05)
        const end = latLngToVector3(CLOUD_REGIONS[j].lat, CLOUD_REGIONS[j].lng, 2.05)

        // Create arc points
        const midPoint = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5)
        const distance = start.distanceTo(end)
        midPoint.normalize().multiplyScalar(2.05 + distance * 0.3)

        connections.push({
          start,
          end,
          color: '#00E7FF'
        })
      }
    }
    return connections
  }, [])

  return (
    <group>
      {arcs.map((arc, i) => {
        const curve = new THREE.QuadraticBezierCurve3(
          arc.start,
          new THREE.Vector3()
            .addVectors(arc.start, arc.end)
            .multiplyScalar(0.5)
            .normalize()
            .multiplyScalar(2.5),
          arc.end
        )
        const points = curve.getPoints(50)

        return (
          <Line
            key={i}
            points={points}
            color={arc.color}
            lineWidth={1}
            transparent
            opacity={0.3}
          />
        )
      })}
    </group>
  )
}

function GlobeScene({
  onRegionHover,
  onRegionLeave
}: {
  onRegionHover: (region: typeof CLOUD_REGIONS[0]) => void
  onRegionLeave: () => void
}) {
  const { camera } = useThree()

  useFrame(() => {
    camera.lookAt(0, 0, 0)
  })

  return (
    <>
      <ambientLight intensity={0.3} />
      <pointLight position={[10, 10, 10]} intensity={1} color="#00E7FF" />
      <pointLight position={[-10, -10, -10]} intensity={0.5} color="#7C3AED" />

      <Earth />
      <ConnectionArcs />

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
        autoRotateSpeed={0.5}
        minPolarAngle={Math.PI / 3}
        maxPolarAngle={Math.PI / 1.5}
      />
    </>
  )
}

function RegionTooltip({ region }: { region: typeof CLOUD_REGIONS[0] | null }) {
  if (!region) return null

  return (
    <motion.div
      className="glass p-4 rounded-xl min-w-[250px]"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
    >
      <div className="flex items-center gap-3 mb-3">
        <div
          className="w-3 h-3 rounded-full"
          style={{ backgroundColor: region.color }}
        />
        <span className="font-display font-semibold text-white">
          {region.name}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <span className="text-white/50">Latency</span>
          <div className="text-primary font-semibold">{region.metrics.latency}</div>
        </div>
        <div>
          <span className="text-white/50">Traffic</span>
          <div className="text-white font-semibold">{region.metrics.traffic}</div>
        </div>
        <div>
          <span className="text-white/50">Services</span>
          <div className="text-white font-semibold">{region.metrics.services}</div>
        </div>
        <div>
          <span className="text-white/50">Health</span>
          <div className="text-success font-semibold">{region.metrics.health}%</div>
        </div>
        <div>
          <span className="text-white/50">CPU</span>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary rounded-full"
                style={{ width: `${region.metrics.cpu}%` }}
              />
            </div>
            <span className="text-white/70 text-xs">{region.metrics.cpu}%</span>
          </div>
        </div>
        <div>
          <span className="text-white/50">Memory</span>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-white/10 rounded-full overflow-hidden">
              <div
                className="h-full bg-secondary rounded-full"
                style={{ width: `${region.metrics.memory}%` }}
              />
            </div>
            <span className="text-white/70 text-xs">{region.metrics.memory}%</span>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

export function GlobalMap() {
  const [hoveredRegion, setHoveredRegion] = useState<typeof CLOUD_REGIONS[0] | null>(null)

  return (
    <section className="relative py-32 overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 bg-gradient-to-b from-background to-background-secondary" />

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

        {/* Globe and info panel */}
        <div className="flex flex-col lg:flex-row items-center gap-8">
          {/* 3D Globe */}
          <motion.div
            className="w-full lg:w-2/3 aspect-square max-w-[600px]"
            initial={{ opacity: 0, scale: 0.9 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
          >
            <Canvas camera={{ position: [0, 0, 5], fov: 50 }}>
              <GlobeScene
                onRegionHover={setHoveredRegion}
                onRegionLeave={() => setHoveredRegion(null)}
              />
            </Canvas>
          </motion.div>

          {/* Region info */}
          <div className="w-full lg:w-1/3 flex flex-col items-center lg:items-start gap-6">
            <AnimatePresence mode="wait">
              {hoveredRegion ? (
                <RegionTooltip key={hoveredRegion.name} region={hoveredRegion} />
              ) : (
                <motion.div
                  key="default"
                  className="glass p-6 rounded-xl"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                >
                  <p className="text-white/60 text-center lg:text-left">
                    Hover over a region to see real-time metrics
                  </p>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Region list */}
            <div className="grid grid-cols-2 gap-3 w-full">
              {CLOUD_REGIONS.map((region) => (
                <motion.div
                  key={region.name}
                  className={`
                    glass p-3 rounded-lg cursor-pointer transition-all duration-300
                    ${hoveredRegion?.name === region.name ? 'border-primary/50' : 'border-transparent'}
                  `}
                  whileHover={{ scale: 1.02 }}
                  onMouseEnter={() => setHoveredRegion(region)}
                  onMouseLeave={() => setHoveredRegion(null)}
                >
                  <div className="flex items-center gap-2">
                    <div
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: region.color }}
                    />
                    <span className="text-white/80 text-sm truncate">{region.name}</span>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
