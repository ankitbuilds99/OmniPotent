import { useRef, useMemo, Suspense } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import {
  Float,
  Stars,
  Sphere,
  Box,
  Torus,
  Icosahedron,
  MeshDistortMaterial,
  MeshTransmissionMaterial,
  Sparkles
} from '@react-three/drei'
import { EffectComposer, Bloom, Vignette } from '@react-three/postprocessing'
import * as THREE from 'three'

// AI Robot Core - The centerpiece holographic AI
function AIRobotCore() {
  const coreRef = useRef<THREE.Group>(null)
  const innerRef = useRef<THREE.Mesh>(null)
  const ringRef = useRef<THREE.Mesh>(null)
  const ring2Ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (coreRef.current) {
      coreRef.current.rotation.y = state.clock.elapsedTime * 0.2
    }
    if (innerRef.current) {
      innerRef.current.rotation.x = state.clock.elapsedTime * 0.5
      innerRef.current.rotation.z = state.clock.elapsedTime * 0.3
    }
    if (ringRef.current) {
      ringRef.current.rotation.x = Math.PI / 2 + Math.sin(state.clock.elapsedTime * 0.5) * 0.1
      ringRef.current.rotation.z = state.clock.elapsedTime * 0.3
    }
    if (ring2Ref.current) {
      ring2Ref.current.rotation.x = Math.PI / 3 + Math.cos(state.clock.elapsedTime * 0.4) * 0.1
      ring2Ref.current.rotation.z = -state.clock.elapsedTime * 0.2
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.2} floatIntensity={0.5}>
      <group ref={coreRef} position={[0, 0, 0]}>
        {/* Core sphere with distortion */}
        <Sphere ref={innerRef} args={[1.2, 64, 64]}>
          <MeshDistortMaterial
            color="#00E7FF"
            emissive="#00E7FF"
            emissiveIntensity={0.5}
            roughness={0.1}
            metalness={0.9}
            distort={0.3}
            speed={2}
            transparent
            opacity={0.8}
          />
        </Sphere>

        {/* Inner glow sphere */}
        <Sphere args={[0.8, 32, 32]}>
          <meshBasicMaterial color="#00FFB3" transparent opacity={0.6} />
        </Sphere>

        {/* Holographic ring 1 */}
        <Torus ref={ringRef} args={[2, 0.05, 16, 64]} position={[0, 0, 0]}>
          <meshBasicMaterial color="#00E7FF" transparent opacity={0.8} />
        </Torus>

        {/* Holographic ring 2 */}
        <Torus ref={ring2Ref} args={[2.5, 0.03, 16, 64]} position={[0, 0, 0]}>
          <meshBasicMaterial color="#7C3AED" transparent opacity={0.6} />
        </Torus>

        {/* Energy particles around core */}
        <Sparkles
          count={100}
          scale={5}
          size={2}
          speed={0.5}
          color="#00E7FF"
        />
      </group>
    </Float>
  )
}

// Docker Container - 3D Cube
function DockerCube({ position, delay = 0 }: { position: [number, number, number]; delay?: number }) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.5 + delay) * 0.2
      ref.current.rotation.y = state.clock.elapsedTime * 0.3 + delay
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.3} floatIntensity={0.5}>
      <Box ref={ref} args={[0.6, 0.6, 0.6]} position={position}>
        <meshStandardMaterial
          color="#0DB7ED"
          emissive="#0DB7ED"
          emissiveIntensity={0.3}
          metalness={0.9}
          roughness={0.1}
        />
      </Box>
    </Float>
  )
}

// Kubernetes Wheel
function KubernetesWheel({ position }: { position: [number, number, number] }) {
  const groupRef = useRef<THREE.Group>(null)

  useFrame((state) => {
    if (groupRef.current) {
      groupRef.current.rotation.z = state.clock.elapsedTime * 0.5
    }
  })

  return (
    <Float speed={1.5} rotationIntensity={0.2} floatIntensity={0.4}>
      <group ref={groupRef} position={position}>
        {/* Central hub */}
        <Icosahedron args={[0.25, 0]}>
          <meshStandardMaterial
            color="#326CE5"
            emissive="#326CE5"
            emissiveIntensity={0.4}
            metalness={0.8}
            roughness={0.2}
          />
        </Icosahedron>
        {/* Wheel spokes */}
        {[0, 60, 120, 180, 240, 300].map((angle, i) => (
          <group key={i} rotation={[0, 0, (angle * Math.PI) / 180]}>
            <Box args={[0.08, 0.5, 0.08]} position={[0, 0.35, 0]}>
              <meshStandardMaterial
                color="#326CE5"
                emissive="#326CE5"
                emissiveIntensity={0.3}
              />
            </Box>
          </group>
        ))}
      </group>
    </Float>
  )
}

// Terraform Crystal
function TerraformCrystal({ position }: { position: [number, number, number] }) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.y = state.clock.elapsedTime * 0.4
      ref.current.position.y = position[1] + Math.sin(state.clock.elapsedTime) * 0.2
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.4} floatIntensity={0.3}>
      <Icosahedron ref={ref} args={[0.4, 0]} position={position}>
        <MeshTransmissionMaterial
          color="#7B42BC"
          transmission={0.9}
          thickness={0.5}
          roughness={0.1}
          chromaticAberration={0.1}
        />
      </Icosahedron>
    </Float>
  )
}

// AWS Cube
function AWSCube({ position }: { position: [number, number, number] }) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.x = state.clock.elapsedTime * 0.3
      ref.current.rotation.y = state.clock.elapsedTime * 0.4
    }
  })

  return (
    <Float speed={1.8} rotationIntensity={0.3} floatIntensity={0.5}>
      <Box ref={ref} args={[0.5, 0.5, 0.5]} position={position}>
        <meshStandardMaterial
          color="#FF9900"
          emissive="#FF9900"
          emissiveIntensity={0.4}
          metalness={0.7}
          roughness={0.2}
        />
      </Box>
    </Float>
  )
}

// GitHub Octohedron
function GitHubOcto({ position }: { position: [number, number, number] }) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.y = state.clock.elapsedTime * 0.3
    }
  })

  return (
    <Float speed={2.2} rotationIntensity={0.25} floatIntensity={0.4}>
      <Icosahedron ref={ref} args={[0.35, 0]} position={position}>
        <meshStandardMaterial
          color="#FFFFFF"
          emissive="#FFFFFF"
          emissiveIntensity={0.2}
          metalness={0.5}
          roughness={0.3}
        />
      </Icosahedron>
    </Float>
  )
}

// Prometheus Fire
function PrometheusFire({ position }: { position: [number, number, number] }) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.y = state.clock.elapsedTime * 0.5
      const scale = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.1
      ref.current.scale.setScalar(scale)
    }
  })

  return (
    <Float speed={2} rotationIntensity={0.3} floatIntensity={0.4}>
      <Icosahedron ref={ref} args={[0.3, 1]} position={position}>
        <meshStandardMaterial
          color="#E6522C"
          emissive="#E6522C"
          emissiveIntensity={0.5}
          metalness={0.6}
          roughness={0.3}
        />
      </Icosahedron>
    </Float>
  )
}

// Grafana Dashboard
function GrafanaDash({ position }: { position: [number, number, number] }) {
  const ref = useRef<THREE.Mesh>(null)

  useFrame((state) => {
    if (ref.current) {
      ref.current.rotation.z = Math.sin(state.clock.elapsedTime) * 0.1
    }
  })

  return (
    <Float speed={1.5} rotationIntensity={0.2} floatIntensity={0.5}>
      <Box ref={ref} args={[0.6, 0.4, 0.1]} position={position}>
        <meshStandardMaterial
          color="#F46800"
          emissive="#F46800"
          emissiveIntensity={0.4}
          metalness={0.7}
          roughness={0.2}
        />
      </Box>
    </Float>
  )
}

// Neural network lines connecting elements
function NeuralLines() {
  const linesRef = useRef<THREE.Group>(null)

  const lines = useMemo(() => {
    const points: THREE.Vector3[][] = []
    const centerPoints = [
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(3, 1.5, -1),
      new THREE.Vector3(-3, 1, 1),
      new THREE.Vector3(2.5, -1.5, 1),
      new THREE.Vector3(-2.5, -1, -1),
      new THREE.Vector3(0, 2.5, 0),
    ]

    for (let i = 0; i < centerPoints.length; i++) {
      for (let j = i + 1; j < centerPoints.length; j++) {
        if (Math.random() > 0.5) {
          points.push([centerPoints[i], centerPoints[j]])
        }
      }
    }
    return points
  }, [])

  useFrame((state) => {
    if (linesRef.current) {
      linesRef.current.children.forEach((line, i) => {
        const material = (line as THREE.Line).material as THREE.LineBasicMaterial
        material.opacity = 0.2 + Math.sin(state.clock.elapsedTime * 2 + i) * 0.1
      })
    }
  })

  return (
    <group ref={linesRef}>
      {lines.map((points, i) => (
        <line key={i}>
          <bufferGeometry>
            <bufferAttribute
              attach="attributes-position"
              count={2}
              array={new Float32Array([
                points[0].x, points[0].y, points[0].z,
                points[1].x, points[1].y, points[1].z
              ])}
              itemSize={3}
            />
          </bufferGeometry>
          <lineBasicMaterial color="#00E7FF" transparent opacity={0.3} />
        </line>
      ))}
    </group>
  )
}

// Mouse-reactive camera
function CameraController() {
  const { camera } = useThree()

  useFrame((state) => {
    const mouseX = (state.mouse.x * 0.5)
    const mouseY = (state.mouse.y * 0.3)

    camera.position.x = THREE.MathUtils.lerp(camera.position.x, mouseX, 0.05)
    camera.position.y = THREE.MathUtils.lerp(camera.position.y, mouseY + 0.5, 0.05)
    camera.lookAt(0, 0, 0)
  })

  return null
}

// Main Scene
function Scene() {
  return (
    <>
      {/* Environment and lighting */}
      <ambientLight intensity={0.2} />
      <pointLight position={[10, 10, 10]} intensity={1} color="#00E7FF" />
      <pointLight position={[-10, -10, -10]} intensity={0.5} color="#7C3AED" />
      <spotLight position={[0, 10, 0]} intensity={0.8} color="#00FFB3" angle={0.3} />

      {/* Starfield background */}
      <Stars
        radius={100}
        depth={50}
        count={5000}
        factor={4}
        saturation={0}
        fade
        speed={1}
      />

      {/* Central AI Core */}
      <AIRobotCore />

      {/* Orbiting DevOps tools */}
      <DockerCube position={[3, 1.5, -1]} delay={0} />
      <KubernetesWheel position={[-3, 1, 1]} />
      <TerraformCrystal position={[2.5, -1.5, 1]} />
      <AWSCube position={[-2.5, -1, -1]} />
      <GitHubOcto position={[0, 2.5, 2]} />
      <PrometheusFire position={[-1.5, 2, -2]} />
      <GrafanaDash position={[1.5, -2, -1.5]} />

      {/* Neural network connections */}
      <NeuralLines />

      {/* Ambient particles */}
      <Sparkles
        count={200}
        scale={15}
        size={1.5}
        speed={0.3}
        color="#00E7FF"
        opacity={0.5}
      />

      {/* Camera controller */}
      <CameraController />

      {/* Post-processing effects */}
      <EffectComposer>
        <Bloom
          luminanceThreshold={0.2}
          luminanceSmoothing={0.9}
          intensity={1.5}
        />
{/* ChromaticAberration removed due to type issues */}
        <Vignette darkness={0.5} offset={0.3} />
      </EffectComposer>
    </>
  )
}

// Loading fallback
function LoadingFallback() {
  return (
    <mesh>
      <sphereGeometry args={[1, 32, 32]} />
      <meshBasicMaterial color="#00E7FF" wireframe />
    </mesh>
  )
}

// Main exported component
export function Scene3D() {
  return (
    <div className="absolute inset-0 z-0">
      <Canvas
        camera={{ position: [0, 0, 8], fov: 60 }}
        dpr={[1, 2]}
        gl={{
          antialias: true,
          alpha: true,
          powerPreference: 'high-performance'
        }}
      >
        <Suspense fallback={<LoadingFallback />}>
          <Scene />
        </Suspense>
      </Canvas>
    </div>
  )
}
