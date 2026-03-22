import { WebGLRenderer, Clock } from 'three'

interface SceneEntry {
  renderer: WebGLRenderer
  animate: (delta: number) => void
  active: boolean
  canvas: HTMLCanvasElement
}

export class SceneManager {
  private scenes = new Map<string, SceneEntry>()
  private clock = new Clock()
  private rafId: number | null = null

  register(name: string, canvas: HTMLCanvasElement, setup: (renderer: WebGLRenderer) => (delta: number) => void) {
    const renderer = new WebGLRenderer({ canvas, alpha: true, antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    const animate = setup(renderer)
    this.scenes.set(name, { renderer, animate, active: false, canvas })
  }

  activate(name: string) {
    const s = this.scenes.get(name)
    if (s) s.active = true
    if (!this.rafId) this.loop()
  }

  deactivate(name: string) {
    const s = this.scenes.get(name)
    if (s) s.active = false
    // Stop loop if nothing active
    const anyActive = [...this.scenes.values()].some(e => e.active)
    if (!anyActive && this.rafId) {
      cancelAnimationFrame(this.rafId)
      this.rafId = null
    }
  }

  dispose(name: string) {
    const s = this.scenes.get(name)
    if (s) {
      s.active = false
      s.renderer.dispose()
      this.scenes.delete(name)
    }
  }

  resize(name: string, width: number, height: number) {
    const s = this.scenes.get(name)
    if (s) {
      s.renderer.setSize(width, height)
    }
  }

  private loop() {
    this.rafId = requestAnimationFrame(() => this.loop())
    const delta = this.clock.getDelta()
    this.scenes.forEach(s => {
      if (s.active) s.animate(delta)
    })
  }
}

export const sceneManager = new SceneManager()
