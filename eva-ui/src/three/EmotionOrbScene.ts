import {
  Scene, PerspectiveCamera, SphereGeometry, MeshPhysicalMaterial,
  Mesh, PointLight, AmbientLight, WebGLRenderer, Color,
  BufferGeometry, Float32BufferAttribute, Points, PointsMaterial,
  Vector3,
} from 'three'

export interface EmotionData {
  engagement: number
  confidence: number
  curiosity: number
  concern: number
  mood_label: string
}

export function createEmotionOrbScene(
  renderer: WebGLRenderer,
  getEmotion: () => EmotionData,
): (delta: number) => void {
  const width = renderer.domElement.clientWidth || 400
  const height = renderer.domElement.clientHeight || 400
  renderer.setSize(width, height)

  const scene = new Scene()
  const camera = new PerspectiveCamera(50, width / height, 0.1, 100)
  camera.position.set(0, 0, 4)

  // Lighting
  const ambient = new AmbientLight(0x334466, 0.5)
  scene.add(ambient)
  const pointLight = new PointLight(0x88ccff, 1.5, 10)
  pointLight.position.set(2, 2, 3)
  scene.add(pointLight)

  // Main orb — glass-like sphere
  const orbGeo = new SphereGeometry(1, 64, 64)
  const orbMat = new MeshPhysicalMaterial({
    color: 0x4488cc,
    transparent: true,
    opacity: 0.25,
    roughness: 0.1,
    metalness: 0.0,
    transmission: 0.8,
    thickness: 0.5,
    clearcoat: 1.0,
    clearcoatRoughness: 0.1,
  })
  const orb = new Mesh(orbGeo, orbMat)
  scene.add(orb)

  // Inner particles
  const particleCount = 200
  const positions = new Float32Array(particleCount * 3)
  const velocities: Vector3[] = []
  for (let i = 0; i < particleCount; i++) {
    const theta = Math.random() * Math.PI * 2
    const phi = Math.acos(2 * Math.random() - 1)
    const r = Math.random() * 0.8
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta)
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta)
    positions[i * 3 + 2] = r * Math.cos(phi)
    velocities.push(new Vector3(
      (Math.random() - 0.5) * 0.02,
      (Math.random() - 0.5) * 0.02,
      (Math.random() - 0.5) * 0.02,
    ))
  }
  const particleGeo = new BufferGeometry()
  particleGeo.setAttribute('position', new Float32BufferAttribute(positions, 3))
  const particleMat = new PointsMaterial({
    color: 0x88ddff,
    size: 0.03,
    transparent: true,
    opacity: 0.7,
    sizeAttenuation: true,
  })
  const particles = new Points(particleGeo, particleMat)
  scene.add(particles)

  // Outer glow ring
  const ringGeo = new SphereGeometry(1.15, 32, 32)
  const ringMat = new MeshPhysicalMaterial({
    color: 0x88aaff,
    transparent: true,
    opacity: 0.06,
    side: 2, // DoubleSide
  })
  const ring = new Mesh(ringGeo, ringMat)
  scene.add(ring)

  let time = 0

  return function animate(delta: number) {
    time += delta
    const emotion = getEmotion()

    // Color mapping
    const hue = 0.55 - emotion.engagement * 0.1 // blue -> slightly warmer
    const sat = 0.5 + emotion.curiosity * 0.3
    const light = 0.4 + emotion.confidence * 0.2
    const orbColor = new Color().setHSL(hue, sat, light)
    orbMat.color.lerp(orbColor, 0.05)

    // Concern -> pulse
    const pulse = 1 + Math.sin(time * 3) * emotion.concern * 0.05
    orb.scale.setScalar(pulse)

    // Engagement -> particle speed
    const speed = 0.5 + emotion.engagement * 2
    const posArr = particleGeo.attributes.position.array as Float32Array
    for (let i = 0; i < particleCount; i++) {
      posArr[i * 3] += velocities[i].x * speed * delta * 10
      posArr[i * 3 + 1] += velocities[i].y * speed * delta * 10
      posArr[i * 3 + 2] += velocities[i].z * speed * delta * 10

      // Constrain to sphere
      const dx = posArr[i * 3], dy = posArr[i * 3 + 1], dz = posArr[i * 3 + 2]
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz)
      if (dist > 0.85) {
        velocities[i].multiplyScalar(-1)
        const scale = 0.85 / dist
        posArr[i * 3] *= scale
        posArr[i * 3 + 1] *= scale
        posArr[i * 3 + 2] *= scale
      }
    }
    particleGeo.attributes.position.needsUpdate = true

    // Particle color follows orb
    particleMat.color.lerp(orbColor, 0.02)

    // Outer ring opacity follows engagement
    ringMat.opacity = 0.03 + emotion.engagement * 0.06

    // Slow rotation
    orb.rotation.y += delta * 0.2
    particles.rotation.y -= delta * 0.1

    // Point light color
    pointLight.color.lerp(orbColor, 0.05)

    renderer.render(scene, camera)
  }
}
