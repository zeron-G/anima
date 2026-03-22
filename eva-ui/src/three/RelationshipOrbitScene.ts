import {
  Scene, PerspectiveCamera, WebGLRenderer,
  SphereGeometry, MeshStandardMaterial, Mesh,
  PointLight, AmbientLight,
  RingGeometry, MeshBasicMaterial, DoubleSide,
} from 'three'

export function createRelationshipOrbitScene(
  renderer: WebGLRenderer,
): (delta: number) => void {
  const width = renderer.domElement.clientWidth || 400
  const height = renderer.domElement.clientHeight || 300
  renderer.setSize(width, height)

  const scene = new Scene()
  const camera = new PerspectiveCamera(40, width / height, 0.1, 50)
  camera.position.set(0, 2, 6)
  camera.lookAt(0, 0, 0)

  const ambient = new AmbientLight(0x223344, 0.5)
  scene.add(ambient)

  // Eva point — ice blue
  const evaGeo = new SphereGeometry(0.15, 32, 32)
  const evaMat = new MeshStandardMaterial({
    color: 0x66bbee, emissive: 0x4499cc, emissiveIntensity: 0.8,
  })
  const eva = new Mesh(evaGeo, evaMat)
  scene.add(eva)

  const evaLight = new PointLight(0x66bbee, 0.8, 5)
  eva.add(evaLight)

  // Master point — warm gold
  const masterGeo = new SphereGeometry(0.15, 32, 32)
  const masterMat = new MeshStandardMaterial({
    color: 0xddaa44, emissive: 0xcc8833, emissiveIntensity: 0.8,
  })
  const master = new Mesh(masterGeo, masterMat)
  scene.add(master)

  const masterLight = new PointLight(0xddaa44, 0.8, 5)
  master.add(masterLight)

  // Orbit path (ring)
  const orbitGeo = new RingGeometry(1.8, 1.82, 64)
  const orbitMat = new MeshBasicMaterial({ color: 0x334455, transparent: true, opacity: 0.2, side: DoubleSide })
  const orbitRing = new Mesh(orbitGeo, orbitMat)
  orbitRing.rotation.x = -Math.PI / 2
  scene.add(orbitRing)

  let time = 0
  const orbitRadius = 1.8

  return function animate(delta: number) {
    time += delta * 0.5

    eva.position.x = Math.cos(time) * orbitRadius
    eva.position.z = Math.sin(time) * orbitRadius
    eva.position.y = Math.sin(time * 2) * 0.1

    master.position.x = Math.cos(time + Math.PI) * orbitRadius
    master.position.z = Math.sin(time + Math.PI) * orbitRadius
    master.position.y = Math.sin(time * 2 + Math.PI) * 0.1

    renderer.render(scene, camera)
  }
}
