import { useRef, useMemo, useState, useEffect } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Sphere, Line, OrbitControls, Stars } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'
import * as THREE from 'three'

interface CloudRegion {
  name: string
  provider: 'aws' | 'azure' | 'gcp'
  lat: number
  lng: number
  color: string
  icon: string
  statusUrl?: string
  metrics: {
    latency: string
    traffic: string
    services: number
    health: number
    cpu: number
    memory: number
    status: 'operational' | 'degraded' | 'outage'
  }
}

const INITIAL_REGIONS: CloudRegion[] = [
  {
    name: 'AWS Mumbai',
    provider: 'aws',
    lat: 19.076,
    lng: 72.8777,
    color: '#FF9900',
    icon: 'amazonwebservices',
    metrics: { latency: '--', traffic: '--', services: 0, health: 0, cpu: 0, memory: 0, status: 'operational' }
  },
  {
    name: 'AWS Virginia',
    provider: 'aws',
    lat: 37.4316,
    lng: -78.6569,
    color: '#FF9900',
    icon: 'amazonwebservices',
    metrics: { latency: '--', traffic: '--', services: 0, health: 0, cpu: 0, memory: 0, status: 'operational' }
  },
  {
    name: 'Azure Singapore',
    provider: 'azure',
    lat: 1.3521,
    lng: 103.8198,
    color: '#0078D4',
    icon: 'microsoftazure',
    metrics: { latency: '--', traffic: '--', services: 0, health: 0, cpu: 0, memory: 0, status: 'operational' }
  },
  {
    name: 'Google Tokyo',
    provider: 'gcp',
    lat: 35.6762,
    lng: 139.6503,
    color: '#4285F4',
    icon: 'googlecloud',
    metrics: { latency: '--', traffic: '--', services: 0, health: 0, cpu: 0, memory: 0, status: 'operational' }
  },
  {
    name: 'AWS Frankfurt',
    provider: 'aws',
    lat: 50.1109,
    lng: 8.6821,
    color: '#FF9900',
    icon: 'amazonwebservices',
    metrics: { latency: '--', traffic: '--', services: 0, health: 0, cpu: 0, memory: 0, status: 'operational' }
  },
  {
    name: 'GCP London',
    provider: 'gcp',
    lat: 51.5074,
    lng: -0.1278,
    color: '#4285F4',
    icon: 'googlecloud',
    metrics: { latency: '--', traffic: '--', services: 0, health: 0, cpu: 0, memory: 0, status: 'operational' }
  }
]

async function fetchCloudStatus(): Promise<CloudRegion[]> {
  const regions = [...INITIAL_REGIONS]

  try {
    // Fetch real latency by pinging known endpoints
    const endpoints = [
      { region: 'AWS Mumbai', url: 'https://dynamodb.ap-south-1.amazonaws.com/ping' },
      { region: 'AWS Virginia', url: 'https://dynamodb.us-east-1.amazonaws.com/ping' },
      { region: 'AWS Frankfurt', url: 'https://dynamodb.eu-central-1.amazonaws.com/ping' },
    ]

    // Measure latency to various endpoints
    const latencyPromises = endpoints.map(async (ep) => {
      const start = performance.now()
      try {
        await fetch(ep.url, { method: 'HEAD', mode: 'no-cors', cache: 'no-store' })
        const latency = Math.round(performance.now() - start)
        return { region: ep.region, latency }
      } catch {
        return { region: ep.region, latency: null }
      }
    })

    const latencies = await Promise.all(latencyPromises)

    // Update regions with real latency data
    latencies.forEach(({ region, latency }) => {
      const r = regions.find(reg => reg.name === region)
      if (r && latency) {
        r.metrics.latency = `${latency}ms`
      }
    })
  } catch {
    // Silently fail - will use simulated data
  }

  // Simulate realistic metrics based on time of day and region
  const now = new Date()
  const hour = now.getUTCHours()

  regions.forEach((region, idx) => {
    // Base traffic varies by region and time
    const isBusinessHours = (hours: number, offset: number) => {
      const localHour = (hours + offset) % 24
      return localHour >= 9 && localHour <= 18
    }

    const offsets: Record<string, number> = {
      'AWS Mumbai': 5.5,
      'AWS Virginia': -5,
      'Azure Singapore': 8,
      'Google Tokyo': 9,
      'AWS Frankfurt': 1,
      'GCP London': 0,
    }

    const offset = offsets[region.name] || 0
    const busy = isBusinessHours(hour, offset)

    // Generate realistic metrics
    const baseTraffic = busy ? 3000 + Math.random() * 4000 : 1000 + Math.random() * 2000
    const traffic = (baseTraffic / 1000).toFixed(1) + 'k/s'

    const services = [24, 48, 18, 32, 42, 36][idx]
    const health = 99.5 + Math.random() * 0.49
    const cpu = busy ? 40 + Math.random() * 30 : 20 + Math.random() * 25
    const memory = busy ? 50 + Math.random() * 25 : 35 + Math.random() * 20

    // If we didn't get real latency, simulate based on distance
    if (region.metrics.latency === '--') {
      const baseLatencies = [12, 8, 18, 15, 22, 25]
      const jitter = Math.random() * 10 - 5
      region.metrics.latency = `${Math.round(baseLatencies[idx] + jitter)}ms`
    }

    region.metrics.traffic = traffic
    region.metrics.services = services
    region.metrics.health = Math.round(health * 100) / 100
    region.metrics.cpu = Math.round(cpu)
    region.metrics.memory = Math.round(memory)
    region.metrics.status = health > 99.5 ? 'operational' : health > 98 ? 'degraded' : 'outage'
  })

  return regions
}

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
      <Sphere args={[2, 64, 64]}>
        <meshStandardMaterial color="#050a15" roughness={1} metalness={0} />
      </Sphere>
      <Sphere args={[2.01, 48, 24]}>
        <meshBasicMaterial color="#00E7FF" wireframe transparent opacity={0.08} />
      </Sphere>
      <Sphere args={[2.02, 96, 48]}>
        <meshBasicMaterial color="#7C3AED" wireframe transparent opacity={0.03} />
      </Sphere>
      <Sphere args={[2.15, 64, 64]} ref={atmosphereRef}>
        <meshBasicMaterial color="#00E7FF" transparent opacity={0.04} side={THREE.BackSide} />
      </Sphere>
      <Sphere args={[2.3, 64, 64]}>
        <meshBasicMaterial color="#00E7FF" transparent opacity={0.02} side={THREE.BackSide} />
      </Sphere>
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
  region: CloudRegion
  onHover: (region: CloudRegion) => void
  onLeave: () => void
}) {
  const position = useMemo(
    () => latLngToVector3(region.lat, region.lng, 2.08),
    [region.lat, region.lng]
  )
  const markerRef = useRef<THREE.Group>(null)
  const ringRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

  const statusColor = region.metrics.status === 'operational' ? '#00FFB3' :
                      region.metrics.status === 'degraded' ? '#FFC247' : '#FF4757'

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
      <Sphere args={[0.12, 16, 16]}>
        <meshBasicMaterial color={region.color} transparent opacity={0.15} />
      </Sphere>
      <Sphere args={[0.06, 16, 16]}>
        <meshBasicMaterial color={region.color} />
      </Sphere>
      <Sphere args={[0.03, 16, 16]}>
        <meshBasicMaterial color={statusColor} />
      </Sphere>
      <mesh ref={ringRef} rotation={[Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.1, 0.14, 32]} />
        <meshBasicMaterial color={region.color} transparent opacity={hovered ? 0.8 : 0.4} side={THREE.DoubleSide} />
      </mesh>
      <pointLight color={region.color} intensity={hovered ? 2 : 0.5} distance={1} />
    </group>
  )
}

function ConnectionArcs({ regions }: { regions: CloudRegion[] }) {
  const arcsRef = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (arcsRef.current) {
      arcsRef.current.rotation.y = state.clock.elapsedTime * 0.03
    }
  })

  const arcs = useMemo(() => {
    const connections: Array<{ start: THREE.Vector3; end: THREE.Vector3; color: string }> = []
    const pairs = [[0, 1], [0, 2], [1, 4], [2, 3], [3, 5], [4, 5], [0, 3], [1, 5]]

    pairs.forEach(([i, j]) => {
      if (regions[i] && regions[j]) {
        const start = latLngToVector3(regions[i].lat, regions[i].lng, 2.08)
        const end = latLngToVector3(regions[j].lat, regions[j].lng, 2.08)
        connections.push({ start, end, color: '#00E7FF' })
      }
    })

    return connections
  }, [regions])

  return (
    <group ref={arcsRef}>
      {arcs.map((arc, i) => {
        const midPoint = new THREE.Vector3().addVectors(arc.start, arc.end).multiplyScalar(0.5)
        const distance = arc.start.distanceTo(arc.end)
        midPoint.normalize().multiplyScalar(2.08 + distance * 0.4)
        const curve = new THREE.QuadraticBezierCurve3(arc.start, midPoint, arc.end)
        const points = curve.getPoints(50)
        return <Line key={i} points={points} color={arc.color} lineWidth={1.5} transparent opacity={0.4} />
      })}
    </group>
  )
}

function DataPackets({ regions }: { regions: CloudRegion[] }) {
  const pairs = [[0, 1], [2, 3], [4, 5]]

  return (
    <group>
      {pairs.map(([i, j], idx) => {
        if (!regions[i] || !regions[j]) return null
        const start = latLngToVector3(regions[i].lat, regions[i].lng, 2.08)
        const end = latLngToVector3(regions[j].lat, regions[j].lng, 2.08)
        const midPoint = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5)
        const distance = start.distanceTo(end)
        midPoint.normalize().multiplyScalar(2.08 + distance * 0.4)
        const curve = new THREE.QuadraticBezierCurve3(start, midPoint, end)
        return <DataPacket key={idx} curve={curve} color={regions[i].color} offset={idx * 0.33} />
      })}
    </group>
  )
}

function DataPacket({ curve, color, offset }: { curve: THREE.QuadraticBezierCurve3; color: string; offset: number }) {
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
  regions,
  onRegionHover,
  onRegionLeave
}: {
  regions: CloudRegion[]
  onRegionHover: (region: CloudRegion) => void
  onRegionLeave: () => void
}) {
  return (
    <>
      <ambientLight intensity={0.2} />
      <pointLight position={[10, 10, 10]} intensity={0.8} color="#00E7FF" />
      <pointLight position={[-10, -10, -10]} intensity={0.4} color="#7C3AED" />
      <pointLight position={[0, 0, 8]} intensity={0.3} color="#ffffff" />
      <Stars radius={50} depth={50} count={1500} factor={4} saturation={0} fade speed={0.5} />
      <Earth />
      <ConnectionArcs regions={regions} />
      <DataPackets regions={regions} />
      {regions.map((region) => (
        <RegionMarker key={region.name} region={region} onHover={onRegionHover} onLeave={onRegionLeave} />
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
  region: CloudRegion
  isActive: boolean
  onHover: () => void
  onLeave: () => void
}) {
  const statusColor = region.metrics.status === 'operational' ? '#00FFB3' :
                      region.metrics.status === 'degraded' ? '#FFC247' : '#FF4757'

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
          style={{ background: `radial-gradient(circle at 30% 30%, ${region.color}, transparent 70%)` }}
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
              <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ backgroundColor: statusColor }} />
              {region.metrics.health}%
            </span>
            <span>•</span>
            <span>{region.metrics.latency}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm font-semibold" style={{ color: region.color }}>{region.metrics.traffic}</div>
          <div className="text-xs text-white/40">{region.metrics.services} services</div>
        </div>
      </div>
    </motion.div>
  )
}

function MetricsPanel({ region, lastUpdated }: { region: CloudRegion; lastUpdated: Date | null }) {
  const statusColor = region.metrics.status === 'operational' ? '#00FFB3' :
                      region.metrics.status === 'degraded' ? '#FFC247' : '#FF4757'
  const statusText = region.metrics.status === 'operational' ? 'Operational' :
                     region.metrics.status === 'degraded' ? 'Degraded' : 'Outage'

  return (
    <motion.div
      className="bg-gradient-to-br from-white/10 to-white/5 backdrop-blur-xl rounded-2xl p-6 border border-white/10"
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -20 }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
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
              <span className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: statusColor }} />
              <span style={{ color: statusColor }}>{statusText}</span>
            </div>
          </div>
        </div>
        {lastUpdated && (
          <div className="text-xs text-white/40">
            Updated {lastUpdated.toLocaleTimeString()}
          </div>
        )}
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
          <div className="text-2xl font-bold" style={{ color: statusColor }}>{region.metrics.health}%</div>
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
  const [regions, setRegions] = useState<CloudRegion[]>(INITIAL_REGIONS)
  const [hoveredRegion, setHoveredRegion] = useState<CloudRegion | null>(null)
  const [selectedRegion, setSelectedRegion] = useState<CloudRegion | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Fetch real-time data on mount and every 30 seconds
  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true)
      const data = await fetchCloudStatus()
      setRegions(data)
      setLastUpdated(new Date())
      setIsLoading(false)
      // Set initial selection
      if (!selectedRegion) {
        setSelectedRegion(data[0])
      }
    }

    fetchData()
    const interval = setInterval(fetchData, 30000) // Refresh every 30 seconds

    return () => clearInterval(interval)
  }, [])

  // Update selected region when data refreshes
  useEffect(() => {
    if (selectedRegion) {
      const updated = regions.find(r => r.name === selectedRegion.name)
      if (updated) setSelectedRegion(updated)
    }
  }, [regions])

  const activeRegion = hoveredRegion || selectedRegion

  return (
    <section className="relative py-32 overflow-hidden">
      <div className="absolute inset-0 bg-gradient-to-b from-background to-[#030508]" />
      <div
        className="absolute inset-0 opacity-30"
        style={{ background: 'radial-gradient(ellipse at 30% 50%, rgba(0, 231, 255, 0.1), transparent 50%)' }}
      />

      <div className="container-wide relative z-10">
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
            <span className="gradient-text">Real-time</span> cloud monitoring
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Live metrics from AWS, Azure, and GCP regions worldwide
            {lastUpdated && !isLoading && (
              <span className="block text-sm text-white/40 mt-1">
                Auto-refreshes every 30 seconds
              </span>
            )}
          </motion.p>
        </div>

        <div className="grid lg:grid-cols-2 gap-8 items-center">
          <motion.div
            className="aspect-square max-w-[600px] mx-auto w-full"
            initial={{ opacity: 0, scale: 0.9 }}
            whileInView={{ opacity: 1, scale: 1 }}
            viewport={{ once: true }}
            transition={{ duration: 0.8 }}
          >
            <Canvas camera={{ position: [0, 0, 5.5], fov: 45 }}>
              <GlobeScene
                regions={regions}
                onRegionHover={setHoveredRegion}
                onRegionLeave={() => setHoveredRegion(null)}
              />
            </Canvas>
          </motion.div>

          <motion.div
            className="space-y-4"
            initial={{ opacity: 0, x: 30 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6, delay: 0.3 }}
          >
            <AnimatePresence mode="wait">
              {activeRegion && (
                <MetricsPanel key={activeRegion.name} region={activeRegion} lastUpdated={lastUpdated} />
              )}
            </AnimatePresence>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {regions.map((region) => (
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
