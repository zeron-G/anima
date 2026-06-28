// The threshold glyph-particle field: Eva's name/words as spring-driven glyphs
// that breathe (weight) and condense into place. Canvas 2D, one RAF, no libs.
import { breath } from './useBreath'

const reduce = matchMedia('(prefers-reduced-motion:reduce)').matches

interface Particle {
  ch: string; x: number; y: number; tx: number; ty: number
  vx: number; vy: number; a: number; ta: number; size: number
}

export class GlyphField {
  private g: CanvasRenderingContext2D
  private W = 0; private H = 0; private DPR = 1
  private parts: Particle[] = []
  private grot: string
  private running = false
  private raf = 0

  private cv: HTMLCanvasElement

  constructor(cv: HTMLCanvasElement) {
    this.cv = cv
    this.g = cv.getContext('2d') as CanvasRenderingContext2D
    this.grot = (getComputedStyle(document.documentElement).getPropertyValue('--grotesk') || 'system-ui, sans-serif').trim()
    this.resize = this.resize.bind(this)
    this.frame = this.frame.bind(this)
    this.resize()
    addEventListener('resize', this.resize)
  }

  private resize(): void {
    this.DPR = Math.min(devicePixelRatio || 1, 2)
    this.W = this.cv.clientWidth; this.H = this.cv.clientHeight
    this.cv.width = this.W * this.DPR; this.cv.height = this.H * this.DPR
    this.g.setTransform(this.DPR, 0, 0, this.DPR, 0, 0)
  }

  private layout(str: string, size: number, cx: number, cy: number, maxW: number) {
    const g = this.g
    g.font = `500 ${size}px ${this.grot}`
    const words = str.split(' '); const lines: string[] = []; let cur = ''
    for (const w of words) {
      const test = cur ? cur + ' ' + w : w
      if (g.measureText(test).width > maxW && cur) { lines.push(cur); cur = w } else cur = test
    }
    if (cur) lines.push(cur)
    const lh = size * 1.18, total = lines.length * lh
    const slots: { ch: string; x: number; y: number }[] = []
    lines.forEach((ln, li) => {
      const w = g.measureText(ln).width; let x = cx - w / 2
      const y = cy - total / 2 + li * lh + size * 0.78
      for (const ch of ln) { const cw = g.measureText(ch).width; if (ch !== ' ') slots.push({ ch, x: x + cw / 2, y }); x += cw }
    })
    return slots
  }

  /** Set the text the field condenses into (reuses particles → letters rearrange). */
  setText(str: string, size: number): void {
    const slots = this.layout(str, size, this.W / 2, this.H * 0.42, Math.min(this.W * 0.82, 920))
    const rnd = (a: number, b: number) => a + Math.random() * (b - a)
    const n = Math.max(this.parts.length, slots.length)
    for (let i = 0; i < n; i++) {
      const slot = slots[i]
      if (i < this.parts.length && slot) {
        const p = this.parts[i]; p.ch = slot.ch; p.tx = slot.x; p.ty = slot.y; p.ta = 1; p.size = size
      } else if (slot) {
        this.parts.push({ ch: slot.ch, x: slot.x + rnd(-240, 240), y: slot.y + rnd(-180, 180), tx: slot.x, ty: slot.y, vx: 0, vy: 0, a: 0, ta: 1, size })
      } else {
        const p = this.parts[i]; p.ta = 0; p.vx += rnd(-1.4, 1.4); p.vy += rnd(-1.4, 1.4)
      }
    }
  }

  start(): void { if (this.running) return; this.running = true; this.raf = requestAnimationFrame(this.frame) }

  stop(): void {
    this.running = false
    cancelAnimationFrame(this.raf)
    removeEventListener('resize', this.resize)
  }

  private frame(): void {
    if (!this.running) return
    const g = this.g, b = breath()
    g.clearRect(0, 0, this.W, this.H)
    const weight = Math.round(320 + b * 300), track = b * 0.04, glow = b * b
    for (let i = this.parts.length - 1; i >= 0; i--) {
      const p = this.parts[i]
      p.vx += (p.tx - p.x) * 0.16; p.vy += (p.ty - p.y) * 0.16
      p.vx *= 0.78; p.vy *= 0.78; p.x += p.vx; p.y += p.vy
      p.a += (p.ta - p.a) * 0.08
      if (p.ta === 0 && p.a < 0.02) { this.parts.splice(i, 1); continue }
      if (p.a <= 0.01) continue
      g.font = `${weight} ${p.size}px ${this.grot}`
      g.textAlign = 'center'; g.textBaseline = 'alphabetic'
      g.fillStyle = `rgba(232,240,238,${p.a})`
      if (glow > 0.15 && !reduce) { g.shadowColor = `rgba(91,224,184,${glow * 0.7 * p.a})`; g.shadowBlur = 18 * glow }
      else g.shadowBlur = 0
      g.fillText(p.ch, p.x + (i - this.parts.length / 2) * track * p.size * 0.02, p.y)
    }
    g.shadowBlur = 0
    this.raf = requestAnimationFrame(this.frame)
  }
}
