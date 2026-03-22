<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'

const props = defineProps<{
  size?: number
  showFull?: boolean
}>()

const canvasRef = ref<HTMLCanvasElement>()
const loading = ref(true)
const error = ref(false)
let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let vrm: any = null
let clock = new THREE.Clock()
let animFrame: number | null = null

onMounted(async () => {
  if (!canvasRef.value) return

  const s = props.size || 200
  renderer = new THREE.WebGLRenderer({ canvas: canvasRef.value, alpha: true, antialias: true })
  renderer.setSize(s, s)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(20, 1, 0.1, 20)
  camera.position.set(0, 1.4, 2.5)
  camera.lookAt(0, 1.3, 0)

  // Lighting
  scene.add(new THREE.AmbientLight(0x99bbdd, 0.6))
  const dir = new THREE.DirectionalLight(0xffffff, 0.8)
  dir.position.set(1, 2, 2)
  scene.add(dir)
  // Rim light (ice blue)
  const rim = new THREE.PointLight(0x66bbee, 0.5, 5)
  rim.position.set(-1, 1.5, -0.5)
  scene.add(rim)

  // Load VRM
  const loader = new GLTFLoader()
  loader.register((parser: any) => new VRMLoaderPlugin(parser))

  try {
    const gltf = await loader.loadAsync('/desktop/static/model/flare/Flare.vrm')
    vrm = gltf.userData.vrm
    if (vrm) {
      VRMUtils.rotateVRM0(vrm)
      scene.add(vrm.scene)
      loading.value = false
      animate()
    }
  } catch (e) {
    console.warn('VRM load failed:', e)
    error.value = true
    loading.value = false
  }
})

function animate() {
  animFrame = requestAnimationFrame(animate)
  const delta = clock.getDelta()

  if (vrm) {
    vrm.update(delta)

    // Breathing animation
    const t = clock.elapsedTime
    if (vrm.humanoid) {
      const chest = vrm.humanoid.getNormalizedBoneNode('chest')
      if (chest) {
        chest.rotation.x = Math.sin(t * 0.8) * 0.01
      }
    }

    // Blink every 3-5 seconds
    if (vrm.expressionManager) {
      const blinkCycle = t % 4
      if (blinkCycle > 3.8) {
        vrm.expressionManager.setValue('blink', 1)
      } else {
        vrm.expressionManager.setValue('blink', 0)
      }
    }
  }

  if (renderer && scene && camera) {
    renderer.render(scene, camera)
  }
}

onUnmounted(() => {
  if (animFrame) cancelAnimationFrame(animFrame)
  renderer?.dispose()
})
</script>

<template>
  <div class="eva-avatar-container" :style="{ width: `${size || 200}px`, height: `${size || 200}px` }">
    <canvas ref="canvasRef" v-show="!loading && !error" />
    <div v-if="loading" class="avatar-loading">
      <div class="loading-ring" />
    </div>
    <div v-if="error" class="avatar-fallback">
      <div class="fallback-orb" />
    </div>
  </div>
</template>

<style scoped>
.eva-avatar-container {
  position: relative;
  border-radius: 50%;
  overflow: hidden;
}

canvas {
  width: 100% !important;
  height: 100% !important;
  border-radius: 50%;
}

.avatar-loading, .avatar-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.loading-ring {
  width: 40%;
  height: 40%;
  border: 2px solid hsla(var(--eva-ice-hsl), 0.15);
  border-top-color: var(--eva-ice);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.fallback-orb {
  width: 60%;
  height: 60%;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, hsla(var(--eva-ice-hsl), 0.8), hsla(200, 50%, 25%, 0.9));
  box-shadow: 0 0 30px hsla(var(--eva-ice-hsl), 0.2);
  animation: orbBreathe 3s ease-in-out infinite;
}

@keyframes orbBreathe {
  0%, 100% { transform: scale(0.95); box-shadow: 0 0 20px hsla(var(--eva-ice-hsl), 0.15); }
  50% { transform: scale(1.05); box-shadow: 0 0 40px hsla(var(--eva-ice-hsl), 0.3); }
}
</style>
