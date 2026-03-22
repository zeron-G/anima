import {
  Scene, PerspectiveCamera, WebGLRenderer,
  BufferGeometry, Float32BufferAttribute, LineBasicMaterial,
  Line, SphereGeometry, MeshStandardMaterial, Mesh,
  AmbientLight, PointLight, Group,
} from 'three'

export interface EvolutionNode {
  title: string
  status: 'success' | 'failed' | 'rolled_back'
  files: string[]
  timestamp: number
}

export function createDNAHelixScene(
  renderer: WebGLRenderer,
  getNodes: () => EvolutionNode[],
): (delta: number) => void {
  const width = renderer.domElement.clientWidth || 400
  const height = renderer.domElement.clientHeight || 600
  renderer.setSize(width, height)

  const scene = new Scene()
  const camera = new PerspectiveCamera(45, width / height, 0.1, 100)
  camera.position.set(3, 0, 5)
  camera.lookAt(0, 0, 0)

  const ambient = new AmbientLight(0x334466, 0.6)
  scene.add(ambient)
  const pointLight = new PointLight(0x88ccff, 1.2, 20)
  pointLight.position.set(3, 3, 5)
  scene.add(pointLight)

  const helixGroup = new Group()
  scene.add(helixGroup)

  const statusColors: Record<string, number> = {
    success: 0x44cc66,
    failed: 0xcc4444,
    rolled_back: 0xcc8844,
  }

  function rebuildHelix() {
    // Clear previous
    while (helixGroup.children.length) helixGroup.remove(helixGroup.children[0])

    const nodes = getNodes()
    const count = Math.max(nodes.length, 10)
    const step = 0.6
    const radius = 1.2

    // Build two strands
    const strand1Points: number[] = []
    const strand2Points: number[] = []

    for (let i = 0; i < count; i++) {
      const angle = i * 0.6
      const y = (i - count / 2) * step

      const x1 = Math.cos(angle) * radius
      const z1 = Math.sin(angle) * radius
      const x2 = Math.cos(angle + Math.PI) * radius
      const z2 = Math.sin(angle + Math.PI) * radius

      strand1Points.push(x1, y, z1)
      strand2Points.push(x2, y, z2)

      // Base pair nodes (if we have data)
      if (i < nodes.length) {
        const node = nodes[i]
        const color = statusColors[node.status] || 0x666666

        // Left node
        const nodeGeo = new SphereGeometry(0.08, 16, 16)
        const nodeMat = new MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.3 })
        const leftNode = new Mesh(nodeGeo, nodeMat)
        leftNode.position.set(x1, y, z1)
        leftNode.userData = { evolution: node }
        helixGroup.add(leftNode)

        // Right node
        const rightNode = new Mesh(nodeGeo, nodeMat.clone())
        rightNode.position.set(x2, y, z2)
        helixGroup.add(rightNode)

        // Connector between pair
        const connGeo = new BufferGeometry()
        connGeo.setAttribute('position', new Float32BufferAttribute([x1, y, z1, x2, y, z2], 3))
        const connMat = new LineBasicMaterial({ color: 0x445566, transparent: true, opacity: 0.3 })
        helixGroup.add(new Line(connGeo, connMat))
      }
    }

    // Strand lines
    const strand1Geo = new BufferGeometry()
    strand1Geo.setAttribute('position', new Float32BufferAttribute(strand1Points, 3))
    const strandMat = new LineBasicMaterial({ color: 0x4488aa, transparent: true, opacity: 0.5 })
    helixGroup.add(new Line(strand1Geo, strandMat))

    const strand2Geo = new BufferGeometry()
    strand2Geo.setAttribute('position', new Float32BufferAttribute(strand2Points, 3))
    helixGroup.add(new Line(strand2Geo, strandMat.clone()))
  }

  rebuildHelix()
  let lastNodeCount = 0

  return function animate(delta: number) {
    const nodes = getNodes()
    if (nodes.length !== lastNodeCount) {
      rebuildHelix()
      lastNodeCount = nodes.length
    }

    helixGroup.rotation.y += delta * 0.15

    renderer.render(scene, camera)
  }
}
