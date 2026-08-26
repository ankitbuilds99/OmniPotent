import { useRef, useMemo, useState, useEffect } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { Sphere, Line, OrbitControls, Stars } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'
import * as THREE from 'three'

/** A public cloud region, and the round-trip time from *this browser* to it.
 *
 *  What used to be here: a `metrics` object carrying `traffic`, `services`,
 *  `health`, `cpu` and `memory`, every one of them manufactured in
 *  `fetchCloudStatus` from `Math.random()` and the visitor's clock —
 *  `busy ? 3000 + Math.random() * 4000 : ...` for traffic, a literal
 *  `[24, 48, 18, 32, 42, 36]` for service counts, `99.5 + Math.random() * 0.49`
 *  for health. The section above it read "Real-time cloud monitoring" and "Live
 *  metrics from AWS, Azure, and GCP regions worldwide". None of it was measured
 *  and none of it could be: this product does not monitor those clouds, and a
 *  browser could not read their CPU utilisation if it did.
 *
 *  What survives is the one number on this page that was ever real — the
 *  latency probe. It is a genuine measurement of a genuine thing, just not the
 *  thing the copy claimed: the network round trip from the visitor's own
 *  machine to that region's public API endpoint. That is what it is now
 *  labelled as. Regions whose probe does not come back read "not measured"
 *  rather than falling back to `baseLatencies[idx] + Math.random() * 10 - 5`,
 *  which is what produced the plausible little numbers before.
 */
interface CloudRegion {
  name: string
  provider: 'aws' | 'azure' | 'gcp'
  lat: number
  lng: number
  color: string
  icon: string
  /** The host to probe. Regional public endpoints — a HEAD request to one is
   *  refused, which is fine: DNS, TCP and TLS still complete, and that is the
   *  round trip being timed. */
  endpoint: string
  /** Round-trip milliseconds, or null when the probe has not run or failed. */
  rttMs: number | null
  /** True once a probe has completed for this region, successfully or not — so
   *  "not measured yet" and "could not be measured" render differently. */
  probed: boolean
}

const INITIAL_REGIONS: CloudRegion[] = [
  {
    name: 'AWS Mumbai',
    provider: 'aws',
    lat: 19.076,
    lng: 72.8777,
    color: '#FF9900',
    icon: 'amazonwebservices',
    endpoint: 'https://dynamodb.ap-south-1.amazonaws.com/',
    rttMs: null,
    probed: false,
  },
  {
    name: 'AWS Virginia',
    provider: 'aws',
    lat: 37.4316,
    lng: -78.6569,
    color: '#FF9900',
    icon: 'amazonwebservices',
    endpoint: 'https://dynamodb.us-east-1.amazonaws.com/',
    rttMs: null,
    probed: false,
  },
  {
    name: 'Azure Singapore',
    provider: 'azure',
    lat: 1.3521,
    lng: 103.8198,
    color: '#0078D4',
    icon: 'microsoftazure',
    endpoint: 'https://southeastasia.batch.azure.com/',
    rttMs: null,
    probed: false,
  },
  {
    name: 'Google Tokyo',
    provider: 'gcp',
    lat: 35.6762,
    lng: 139.6503,
    color: '#4285F4',
    icon: 'googlecloud',
    endpoint: 'https://asia-northeast1-run.googleapis.com/',
    rttMs: null,
    probed: false,
  },
  {
    name: 'AWS Frankfurt',
    provider: 'aws',
    lat: 50.1109,
    lng: 8.6821,
    color: '#FF9900',
    icon: 'amazonwebservices',
    endpoint: 'https://dynamodb.eu-central-1.amazonaws.com/',
    rttMs: null,
    probed: false,
  },
  {
    name: 'GCP London',
    provider: 'gcp',
    lat: 51.5074,
    lng: -0.1278,
    color: '#4285F4',
    icon: 'googlecloud',
    endpoint: 'https://europe-west2-run.googleapis.com/',
    rttMs: null,
    probed: false,
  },
]

/** How a round trip reads at a glance. Deliberately not "operational /
 *  degraded / outage": a slow round trip from one visitor's laptop says
 *  something about distance and the path taken, and nothing whatsoever about
 *  whether that region is healthy. The old code drew an outage badge off a
 *  random number. */
type Tone = 'near' | 'far' | 'distant' | 'unmeasured'

function tone(region: CloudRegion): Tone {
  if (region.rttMs === null) return 'unmeasured'
  if (region.rttMs < 100) return 'near'
  if (region.rttMs < 250) return 'far'
  return 'distant'
}

const TONE_COLOR: Record<Tone, string> = {
  near: '#00FFB3',
  far: '#FFC247',
  distant: '#FF4757',
  unmeasured: '#6B7280',
}

const TONE_LABEL: Record<Tone, string> = {
  near: 'Nearby',
  far: 'Cross-region',
  distant: 'Far side of the world',
  unmeasured: 'Not measured',
}

/** Time one round trip to `endpoint`.
 *
 *  `mode: 'no-cors'` means the response is opaque and its status unreadable —
 *  which is exactly what is wanted. The endpoint will refuse an unauthenticated
 *  HEAD, but DNS, the TCP handshake and the TLS handshake all complete first,
 *  and that is the network path being timed. A rejection means the host did not
 *  resolve or the connection failed; that returns null rather than a guess.
 *
 *  Two requests, timing the second: the first pays for DNS and the TLS
 *  handshake, so timing it would report several hundred milliseconds of setup
 *  as though it were the round trip. The second reuses the warm connection.
 */
async function probe(endpoint: string): Promise<number | null> {
  try {
    await fetch(endpoint, { method: 'HEAD', mode: 'no-cors', cache: 'no-store' })
    const start = performance.now()
    await fetch(endpoint, { method: 'HEAD', mode: 'no-cors', cache: 'no-store' })
    return Math.round(performance.now() - start)
  } catch {
    return null
  }
}

async function measureRegions(regions: CloudRegion[]): Promise<CloudRegion[]> {
  return Promise.all(
    regions.map(async (region) => ({
      ...region,
      rttMs: await probe(region.endpoint),
      probed: true,
    }))
  )
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

  const statusColor = TONE_COLOR[tone(region)]

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

function rttText(region: CloudRegion): string {
  if (!region.probed) return 'measuring…'
  return region.rttMs === null ? 'not measured' : `${region.rttMs} ms`
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
  const t = tone(region)

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
            {/* The dot no longer pulses. It used to animate on every card at all
                times, which reads as a live feed; the probe runs once every
                thirty seconds and the rest of the time this is a stored number. */}
            <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: TONE_COLOR[t] }} />
            <span>{TONE_LABEL[t]}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm font-semibold" style={{ color: region.color }}>
            {rttText(region)}
          </div>
          <div className="text-xs text-white/40">round trip</div>
        </div>
      </div>
    </motion.div>
  )
}

function MetricsPanel({ region, lastUpdated }: { region: CloudRegion; lastUpdated: Date | null }) {
  const t = tone(region)

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
              <span className="w-2 h-2 rounded-full" style={{ backgroundColor: TONE_COLOR[t] }} />
              <span style={{ color: TONE_COLOR[t] }}>{TONE_LABEL[t]}</span>
            </div>
          </div>
        </div>
        {lastUpdated && (
          <div className="text-xs text-white/40">
            Measured {lastUpdated.toLocaleTimeString()}
          </div>
        )}
      </div>

      {/* Four tiles used to sit here: Latency, Traffic, Services and Health.
          Three of them were invented and one of them — Health — was invented and
          then used to decide whether to render an outage badge. Only the round
          trip was ever measured, so only the round trip is shown. */}
      <div className="bg-white/5 rounded-lg p-4">
        <div className="text-white/50 text-xs uppercase tracking-wider mb-1">
          Round trip from your browser
        </div>
        <div className="text-3xl font-bold text-white">{rttText(region)}</div>
        <div className="text-xs text-white/40 mt-2 leading-relaxed">
          {region.probed && region.rttMs === null
            ? `${region.endpoint.replace('https://', '').replace(/\/$/, '')} did not respond — it may be blocked by your network or by an extension.`
            : `HEAD ${region.endpoint.replace('https://', '').replace(/\/$/, '')}, connection already warm.`}
        </div>
      </div>
    </motion.div>
  )
}

export function GlobalMap() {
  const [regions, setRegions] = useState<CloudRegion[]>(INITIAL_REGIONS)
  const [hoveredRegion, setHoveredRegion] = useState<string | null>(null)
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Re-probe on mount and every 30 seconds.
  useEffect(() => {
    let cancelled = false

    const run = async () => {
      setIsLoading(true)
      const data = await measureRegions(INITIAL_REGIONS)
      // The probes take as long as the slowest network path, and the visitor can
      // navigate away in the meantime. Without this the interval's late arrival
      // sets state on an unmounted component.
      if (cancelled) return
      setRegions(data)
      setLastUpdated(new Date())
      setIsLoading(false)
    }

    run()
    const interval = setInterval(run, 30000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // Keep the open panel pointed at the current numbers. Selection is held by
  // name rather than by object so a refresh does not leave the panel rendering
  // the previous round's measurement — the old code read `selectedRegion`
  // inside an effect that did not depend on it.
  const activeRegion = useMemo(() => {
    const name = hoveredRegion ?? selectedName
    const byName = name ? regions.find(r => r.name === name) : undefined
    return byName ?? regions[0] ?? null
  }, [hoveredRegion, selectedName, regions])

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
            Global Reach
          </motion.span>
          <motion.h2
            className="font-display text-3xl md:text-4xl lg:text-5xl font-bold text-white mb-4"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
          >
            {/* "Real-time cloud monitoring" over six columns of Math.random().
                The measurement is real; the claim was not. */}
            The round trip to <span className="gradient-text">every major region</span>
          </motion.h2>
          <motion.p
            className="text-white/60 text-lg max-w-2xl mx-auto"
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
          >
            Measured from your browser, right now — one HEAD request to each
            region's public endpoint on AWS, Azure and Google Cloud.
            {lastUpdated && !isLoading && (
              <span className="block text-sm text-white/40 mt-1">
                Re-measured every 30 seconds. These are network round trips from
                where you are sitting, not the health of anybody's infrastructure.
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
                onRegionHover={(region) => setHoveredRegion(region.name)}
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
                    setHoveredRegion(region.name)
                    setSelectedName(region.name)
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
