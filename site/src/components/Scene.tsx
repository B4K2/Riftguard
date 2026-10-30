import { useMemo, useRef } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import * as THREE from 'three'

// Scroll progress (0..1) is written here by App; the scene reads it every frame.
export const scroll = { progress: 0 }

function Dust({ count = 1800 }) {
  const ref = useRef<THREE.Points>(null!)
  const positions = useMemo(() => {
    const p = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      const r = 6 + Math.random() * 18
      const t = Math.random() * Math.PI * 2
      const f = Math.acos(2 * Math.random() - 1)
      p[i * 3] = r * Math.sin(f) * Math.cos(t)
      p[i * 3 + 1] = r * Math.sin(f) * Math.sin(t)
      p[i * 3 + 2] = r * Math.cos(f)
    }
    return p
  }, [count])
  useFrame((_, dt) => {
    ref.current.rotation.y += dt * 0.02
    ref.current.rotation.x = scroll.progress * 0.8
  })
  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial size={0.035} color="#9a9a9a" transparent opacity={0.7} sizeAttenuation />
    </points>
  )
}

// One agent: a node orbiting the shared store, with a link line back to the center.
function Agent({ phase, radius, tilt, shade }: { phase: number; radius: number; tilt: number; shade: string }) {
  const group = useRef<THREE.Group>(null!)
  const node = useRef<THREE.Mesh>(null!)
  const line = useMemo(() => {
    const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()])
    return new THREE.Line(g, new THREE.LineBasicMaterial({ color: shade, transparent: true, opacity: 0.35 }))
  }, [shade])
  useFrame(({ clock }) => {
    const t = clock.elapsedTime * 0.35 + phase
    // Agents drift apart in the middle of the page (the "rift") and reconverge at the end.
    const rift = Math.sin(Math.min(scroll.progress * 1.25, 1) * Math.PI)
    const r = radius + rift * 2.2
    node.current.position.set(Math.cos(t) * r, Math.sin(t * 1.3) * 0.6, Math.sin(t) * r)
    const pos = line.geometry.attributes.position as THREE.BufferAttribute
    pos.setXYZ(1, node.current.position.x, node.current.position.y, node.current.position.z)
    pos.needsUpdate = true
    group.current.rotation.z = tilt
  })
  return (
    <group ref={group}>
      <primitive object={line} />
      <mesh ref={node}>
        <octahedronGeometry args={[0.18, 0]} />
        <meshStandardMaterial color={shade} emissive={shade} emissiveIntensity={0.4} flatShading />
      </mesh>
    </group>
  )
}

function Core() {
  const outer = useRef<THREE.Mesh>(null!)
  const inner = useRef<THREE.Mesh>(null!)
  const ring = useRef<THREE.Mesh>(null!)
  useFrame((_, dt) => {
    const p = scroll.progress
    outer.current.rotation.x += dt * 0.15
    outer.current.rotation.y += dt * 0.2
    inner.current.rotation.y -= dt * 0.3
    ring.current.rotation.z += dt * 0.1
    ring.current.rotation.x = Math.PI / 2.4 + p * 1.2
    const s = 1 + Math.sin(p * Math.PI) * 0.35
    outer.current.scale.setScalar(s)
  })
  return (
    <group>
      <mesh ref={outer}>
        <icosahedronGeometry args={[1.6, 1]} />
        <meshBasicMaterial color="#ffffff" wireframe transparent opacity={0.45} />
      </mesh>
      <mesh ref={inner}>
        <icosahedronGeometry args={[0.95, 0]} />
        <meshStandardMaterial color="#1a1a1a" metalness={0.9} roughness={0.25} flatShading />
      </mesh>
      <mesh ref={ring}>
        <torusGeometry args={[2.6, 0.006, 8, 160]} />
        <meshBasicMaterial color="#bbbbbb" transparent opacity={0.5} />
      </mesh>
    </group>
  )
}

function Rig() {
  useFrame(({ camera, pointer }) => {
    const p = scroll.progress
    // Hero sits right of the text; mid-page the camera pulls back; at the end it centers.
    const targetX = THREE.MathUtils.lerp(-2.2, 0, Math.min(p * 3, 1)) + pointer.x * 0.4
    const targetZ = 9 + Math.sin(p * Math.PI) * 4
    camera.position.x += (targetX - camera.position.x) * 0.05
    camera.position.y += (pointer.y * 0.4 + 0.6 - camera.position.y) * 0.05
    camera.position.z += (targetZ - camera.position.z) * 0.05
    camera.lookAt(camera.position.x, 0, 0)
  })
  return null
}

export default function Scene() {
  return (
    <div className="scene" aria-hidden>
      <Canvas camera={{ position: [-2.2, 0.6, 9], fov: 45 }} dpr={[1, 2]} gl={{ antialias: true }}>
        <color attach="background" args={['#050505']} />
        <fog attach="fog" args={['#050505', 8, 26]} />
        <ambientLight intensity={0.4} />
        <directionalLight position={[5, 5, 5]} intensity={2} />
        <pointLight position={[-4, -2, 3]} intensity={20} color="#ffffff" />
        <Core />
        <Agent phase={0} radius={3.2} tilt={0.2} shade="#ffffff" />
        <Agent phase={Math.PI} radius={3.2} tilt={-0.2} shade="#8a8a8a" />
        <Agent phase={Math.PI / 2} radius={4.1} tilt={0.5} shade="#cfcfcf" />
        <Dust />
        <Rig />
      </Canvas>
    </div>
  )
}
