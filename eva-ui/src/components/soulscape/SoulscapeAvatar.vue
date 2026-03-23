<script setup lang="ts">
/**
 * SoulscapeAvatar — Ported from the old working desktop frontend.
 * VRM: Three.js + @pixiv/three-vrm (Flare.vrm) with OrbitControls, lookAt, blink, emotion, lip sync
 * Live2D: Pixi.js v7 (CDN) + pixi-live2d-display (CDN) + cubismcore (backend static)
 * Both canvases always in DOM, stacked via z-index. Toggle with 3D/2D buttons.
 */
import { ref, onMounted, onUnmounted } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'
import { useEmotionStore } from '@/stores/emotionStore'

type Mode = 'vrm' | 'live2d'

// Backend base URL — needed for Tauri where frontend origin != backend origin
const API_BASE = import.meta.env.VITE_API_BASE || ''

const emotion = useEmotionStore()

const containerRef = ref<HTMLDivElement>()
const vrmCanvasRef = ref<HTMLCanvasElement>()
const l2dCanvasRef = ref<HTMLCanvasElement>()
const activeMode = ref<Mode>('vrm')
const vrmReady = ref(false)
const l2dReady = ref(false)
const loading = ref(true)
const outfitButtons = ref<Array<{ name: string; visible: boolean }>>([])

// ═══════════════════════════════════════════
//  VRM state (ported from old vrm.js)
// ═══════════════════════════════════════════
let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: any = null
let vrm: any = null
let clock = new THREE.Clock()
let vrmPaused = false
let vrmAnimId: number | null = null
let mx = 0, my = 0
let lookAtVec = new THREE.Vector3()
let blinkTimer = 3, blinkPhase = 0, blinkVal = 0
let lastEmo = ''
let meshList: Array<{ name: string; mesh: any; visible: boolean }> = []
let resizeObs: ResizeObserver | null = null

function onMouseMove(e: MouseEvent) {
  mx = (e.clientX / window.innerWidth) * 2 - 1
  my = -(e.clientY / window.innerHeight) * 2 + 1
}

function getBone(name: string) {
  if (!vrm?.humanoid) return null
  const hb = vrm.humanoid
  try { return hb.getNormalizedBoneNode(name) } catch {}
  try { return hb.getRawBoneNode(name) } catch {}
  return hb.humanBones[name]?.node || null
}

function setBoneRot(name: string, x: number, y: number, z: number) {
  const node = getBone(name)
  if (node) node.quaternion.setFromEuler(new THREE.Euler(x, y, z, 'XYZ'))
}

function setNaturalPose() {
  if (!vrm?.humanoid) return
  for (const bn of Object.keys(vrm.humanoid.humanBones)) {
    if (bn === 'hips') continue
    const node = getBone(bn)
    if (node) node.quaternion.identity()
  }
  setBoneRot('leftUpperArm', 0, 0, 1.2)
  setBoneRot('rightUpperArm', 0, 0, -1.2)
  setBoneRot('leftLowerArm', 0, 0, 0.15)
  setBoneRot('rightLowerArm', 0, 0, -0.15)
}

function doBlink(dt: number) {
  blinkTimer -= dt
  if (blinkPhase === 0 && blinkTimer <= 0) { blinkPhase = 1; blinkVal = 0 }
  else if (blinkPhase === 1) { blinkVal += dt * 18; if (blinkVal >= 1) { blinkVal = 1; blinkPhase = 2 } }
  else if (blinkPhase === 2) { blinkVal -= dt * 12; if (blinkVal <= 0) { blinkVal = 0; blinkPhase = 0; blinkTimer = 2 + Math.random() * 5 } }
  try { vrm.expressionManager.setValue('blink', blinkVal) } catch {}
}

function getEmo(e: any): string {
  if (!e) return 'neutral'
  if (e.concern > 0.6) return 'sad'
  if (e.curiosity > 0.7) return 'curious'
  if (e.engagement > 0.7 && e.confidence > 0.6) return 'excited'
  if (e.engagement > 0.6) return 'happy'
  if (e.concern > 0.4) return 'worried'
  return 'neutral'
}

function updateVrmEmotion() {
  if (!vrm?.expressionManager) return
  const emo = getEmo(emotion.current)
  if (emo === lastEmo) return
  lastEmo = emo
  const m = vrm.expressionManager
  for (const n of ['happy', 'angry', 'sad', 'relaxed']) {
    try { m.setValue(n, 0) } catch {}
  }
  switch (emo) {
    case 'happy': m.setValue('happy', 0.7); break
    case 'excited': m.setValue('happy', 0.9); break
    case 'sad': m.setValue('sad', 0.6); break
    case 'worried': m.setValue('sad', 0.3); break
    case 'curious': break
  }
}

function animateVrm() {
  if (vrmPaused) return
  vrmAnimId = requestAnimationFrame(animateVrm)
  const dt = clock.getDelta()
  const t = clock.elapsedTime

  if (vrm) {
    // Breathing
    vrm.scene.position.y = Math.sin(t * 1.5) * 0.003
    vrm.scene.rotation.y = Math.PI + Math.sin(t * 0.4) * 0.015

    // LookAt
    if (vrm.lookAt) {
      lookAtVec.set(mx * 2, my * 1.5 + 1.2, 2)
      try { vrm.lookAt.lookAt(lookAtVec) } catch {}
    }

    doBlink(dt)
    updateVrmEmotion()
    vrm.update(dt)
  }

  if (controls) controls.update()
  if (renderer && scene && camera) renderer.render(scene, camera)
}

async function initVrm() {
  if (!vrmCanvasRef.value || !containerRef.value) return
  const w = containerRef.value.clientWidth
  const h = containerRef.value.clientHeight

  renderer = new THREE.WebGLRenderer({ canvas: vrmCanvasRef.value, antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
  renderer.setSize(w, h)
  renderer.setClearColor(0x0a0a0a)

  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(30, w / h, 0.1, 100)
  camera.position.set(0, 1.3, 3)
  camera.lookAt(0, 1, 0)
  scene.add(new THREE.AmbientLight(0xffffff, 0.7))
  const dl = new THREE.DirectionalLight(0xffffff, 1)
  dl.position.set(2, 3, 2)
  scene.add(dl)

  controls = new OrbitControls(camera, vrmCanvasRef.value)
  controls.target.set(0, 1, 0)
  controls.enableDamping = true
  controls.dampingFactor = 0.1
  controls.update()

  // Resize observer
  resizeObs = new ResizeObserver(() => {
    if (vrmPaused || !containerRef.value) return
    const nw = containerRef.value.clientWidth
    const nh = containerRef.value.clientHeight
    if (nw < 1 || nh < 1) return
    camera!.aspect = nw / nh
    camera!.updateProjectionMatrix()
    renderer!.setSize(nw, nh)
  })
  resizeObs.observe(containerRef.value)
  document.addEventListener('mousemove', onMouseMove)
  animateVrm()

  // Load model
  const loader = new GLTFLoader()
  loader.register((parser: any) => new VRMLoaderPlugin(parser))
  const gltf = await loader.loadAsync(`${API_BASE}/desktop/static/model/flare/Flare.vrm`)
  vrm = gltf.userData.vrm
  if (!vrm) throw new Error('No VRM data')

  VRMUtils.removeUnnecessaryVertices(gltf.scene)
  VRMUtils.rotateVRM0(vrm)
  vrm.scene.rotation.y = Math.PI
  scene.add(vrm.scene)
  setNaturalPose()

  // Collect meshes
  meshList = []
  vrm.scene.traverse((c: any) => {
    if (c.isMesh || c.isSkinnedMesh) {
      meshList.push({ name: c.name || c.uuid?.substring(0, 8) || 'mesh', mesh: c, visible: true })
    }
  })
  // Outfit buttons — costume meshes only
  outfitButtons.value = meshList
    .filter(m => /costume/i.test(m.name))
    .map(m => ({ name: m.name, visible: m.mesh.visible }))

  vrmReady.value = true
}

function toggleMesh(name: string) {
  const m = meshList.find(x => x.name === name)
  if (m) {
    m.mesh.visible = !m.mesh.visible
    const btn = outfitButtons.value.find(b => b.name === name)
    if (btn) btn.visible = m.mesh.visible
  }
}

// ═══════════════════════════════════════════
//  Live2D state (ported from old live2d.js)
//  Loads Pixi v7 + pixi-live2d-display from CDN
// ═══════════════════════════════════════════
let l2dApp: any = null
let l2dModel: any = null
let l2dResizeObs: ResizeObserver | null = null
let l2dLastEmo = ''
const l2dMouseHandler = (e: MouseEvent) => { if (l2dModel) l2dModel.focus(e.clientX, e.clientY) }

const L2D_EMOTION_MAP: Record<string, { expression: string | null; params: Record<string, number> }> = {
  happy:   { expression: null,     params: { ParamEyeLSmile: 0.7, ParamEyeRSmile: 0.7, ParamMouthForm: 0.6 } },
  excited: { expression: '星星眼', params: { ParamEyeLSmile: 0.5, ParamEyeRSmile: 0.5, ParamMouthForm: 0.8 } },
  sad:     { expression: 'QAQ',    params: { ParamBrowLY: -0.4, ParamBrowRY: -0.4, ParamMouthForm: -0.2 } },
  angry:   { expression: '生气',   params: { Param8: 0.8, ParamBrowLAngle: -0.5, ParamBrowRAngle: -0.5 } },
  curious: { expression: '问号',   params: { ParamAngleZ: 8.0, ParamBrowLY: 0.3, ParamBrowRY: 0.3 } },
  sleepy:  { expression: null,     params: { ParamEyeLOpen: 0.3, ParamEyeROpen: 0.3 } },
  neutral: { expression: null,     params: {} },
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) { resolve(); return }
    const s = document.createElement('script')
    s.src = src
    s.onload = () => resolve()
    s.onerror = () => reject(new Error(`Failed to load ${src}`))
    document.head.appendChild(s)
  })
}

async function initLive2d() {
  if (!l2dCanvasRef.value || !containerRef.value) return

  // Load SDKs from CDN (same as old frontend)
  await loadScript(`${API_BASE}/static/live2dcubismcore.min.js`)
  await loadScript('https://cdn.jsdelivr.net/npm/pixi.js@7.x/dist/pixi.min.js')
  await loadScript('https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js')

  // Wait for PIXI.live2d
  const PIXI = (window as any).PIXI
  if (!PIXI?.live2d) {
    await new Promise<void>(resolve => {
      const check = () => (PIXI?.live2d) ? resolve() : setTimeout(check, 100)
      check()
    })
  }

  l2dApp = new PIXI.Application({
    view: l2dCanvasRef.value,
    autoStart: true,
    backgroundColor: 0x0a0a0a,
    backgroundAlpha: 1,
    resizeTo: containerRef.value,
  })

  const model = await PIXI.live2d.Live2DModel.from(`${API_BASE}/static/model/PurpleBird/PurpleBird.model3.json`, { autoInteract: false })
  l2dApp.stage.addChild(model)
  l2dModel = model

  const reposition = () => {
    if (!containerRef.value) return
    const w = containerRef.value.clientWidth
    const h = containerRef.value.clientHeight
    const mw = model.internalModel?.originalWidth || model.width
    const mh = model.internalModel?.originalHeight || model.height
    if (!mw || !mh || !w || !h) return
    const scale = Math.min((h * 0.95) / mh, (w * 0.95) / mw) * 2.2
    model.scale.set(scale)
    model.anchor.set(0.5, 0.5)
    model.x = w / 2
    model.y = h
  }
  reposition()

  l2dResizeObs = new ResizeObserver(reposition)
  l2dResizeObs.observe(containerRef.value)
  document.addEventListener('mousemove', l2dMouseHandler)

  // Start paused — VRM is default front
  l2dApp.stop()
  l2dReady.value = true
}

function updateL2dEmotion() {
  if (!l2dModel) return
  const emo = getEmo(emotion.current)
  if (emo === l2dLastEmo) return
  l2dLastEmo = emo
  const mapping = L2D_EMOTION_MAP[emo] || L2D_EMOTION_MAP.neutral
  try {
    if (mapping.expression) {
      const em = l2dModel.internalModel?.motionManager?.expressionManager
      if (em) em.setExpression(mapping.expression)
    }
    const cm = l2dModel.internalModel?.coreModel
    if (cm) {
      for (const p in mapping.params) {
        try { cm.setParameterValueById(p, mapping.params[p]) } catch {}
      }
    }
  } catch {}
}

// ═══════════════════════════════════════════
//  Mode switching (exactly like old frontend)
// ═══════════════════════════════════════════
function switchMode(mode: Mode) {
  if (mode === activeMode.value) return
  activeMode.value = mode

  if (mode === 'vrm') {
    if (l2dApp) l2dApp.stop()
    if (vrmReady.value && vrmPaused) {
      vrmPaused = false
      clock.getDelta()
      if (containerRef.value && renderer && camera) {
        const w = containerRef.value.clientWidth
        const h = containerRef.value.clientHeight
        camera.aspect = w / h
        camera.updateProjectionMatrix()
        renderer.setSize(w, h)
      }
      animateVrm()
    }
  } else {
    vrmPaused = true
    if (vrmAnimId) { cancelAnimationFrame(vrmAnimId); vrmAnimId = null }
    if (l2dApp) l2dApp.start()
  }
}

// ═══════════════════════════════════════════
//  Lifecycle
// ═══════════════════════════════════════════
onMounted(async () => {
  // Init VRM
  try {
    await initVrm()
  } catch (e) {
    console.warn('VRM failed, fallback to Live2D:', e)
    activeMode.value = 'live2d'
  }
  loading.value = false

  // Init Live2D in background
  try {
    await initLive2d()
    console.log('Live2D ready')
  } catch (e) {
    console.warn('Live2D init failed:', e)
  }
})

onUnmounted(() => {
  // VRM cleanup
  vrmPaused = true
  if (vrmAnimId) cancelAnimationFrame(vrmAnimId)
  if (resizeObs) resizeObs.disconnect()
  document.removeEventListener('mousemove', onMouseMove)
  renderer?.dispose()

  // Live2D cleanup
  if (l2dResizeObs) l2dResizeObs.disconnect()
  document.removeEventListener('mousemove', l2dMouseHandler)
  if (l2dApp) { l2dApp.stop(); l2dApp.destroy(false) }
})
</script>

<template>
  <div ref="containerRef" class="avatar-stage">
    <!-- Live2D canvas (behind VRM) -->
    <canvas ref="l2dCanvasRef" class="av-canvas" :class="{ 'av-front': activeMode === 'live2d' }" />
    <!-- VRM canvas (default front) -->
    <canvas ref="vrmCanvasRef" class="av-canvas" :class="{ 'av-front': activeMode === 'vrm' }" />

    <!-- Loading -->
    <div v-if="loading" class="stage-loading">
      <div class="loading-ring" />
      <span class="loading-text">Loading avatar...</span>
    </div>

    <!-- Floating pills -->
    <div class="av-pills">
      <span class="pill mood-pill">{{ emotion.current.mood_label }}</span>
    </div>

    <!-- Outfit panel (VRM only) -->
    <div v-if="activeMode === 'vrm' && outfitButtons.length" class="outfit-panel">
      <button
        v-for="o in outfitButtons"
        :key="o.name"
        class="outfit-btn"
        :class="{ off: !o.visible }"
        @click="toggleMesh(o.name)"
      >{{ o.name }}</button>
    </div>

    <!-- Mode toggle -->
    <div class="av-controls">
      <button class="av-btn" :class="{ active: activeMode === 'vrm' }" @click="switchMode('vrm')">3D</button>
      <button class="av-btn" :class="{ active: activeMode === 'live2d' }" @click="switchMode('live2d')">2D</button>
    </div>
  </div>
</template>

<style scoped>
.avatar-stage {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 420px;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: #0a0a0a;
  border: 1px solid var(--border);
}

/* Both canvases always in DOM, stacked */
.av-canvas {
  position: absolute;
  inset: 0;
  width: 100% !important;
  height: 100% !important;
  z-index: 1;
}

.av-canvas.av-front {
  z-index: 2;
}

/* Loading */
.stage-loading {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  z-index: 10;
  background: #0a0a0a;
}

.loading-ring {
  width: 28px;
  height: 28px;
  border: 2px solid rgba(var(--accent-rgb), 0.12);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.loading-text {
  font-family: var(--font-heading);
  font-size: 11px;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-dim);
}

@keyframes spin { to { transform: rotate(360deg); } }

/* Floating pills */
.av-pills {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 10;
  display: flex;
  gap: 6px;
}

.pill {
  padding: 4px 12px;
  border-radius: 100px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  font-family: var(--font-heading);
  font-size: 10px;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--accent);
}

/* Outfit panel */
.outfit-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.outfit-btn {
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  color: var(--text-secondary);
  font-family: var(--font-heading);
  font-size: 10px;
  letter-spacing: 0.5px;
  cursor: pointer;
  transition: all 0.2s;
}

.outfit-btn:hover {
  border-color: rgba(var(--accent-rgb), 0.2);
}

.outfit-btn.off {
  opacity: 0.35;
  text-decoration: line-through;
}

/* Mode toggle — bottom center */
.av-controls {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: var(--radius);
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.06);
  z-index: 10;
}

.av-btn {
  padding: 6px 16px;
  border-radius: var(--radius-sm);
  border: none;
  background: transparent;
  color: var(--text-dim);
  font-family: var(--font-heading);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 1px;
  cursor: pointer;
  transition: all 0.2s;
}

.av-btn.active {
  background: rgba(var(--accent-rgb), 0.15);
  color: var(--accent);
}
</style>
