// The single heartbeat scalar that governs every motion in LEXICON.
// One source of truth → all motion stays in sync (calm-alive, never busy).
const reduce = matchMedia('(prefers-reduced-motion:reduce)').matches

let omega = (2 * Math.PI) / 15   // rest: a 15-second swell
let target = omega
let phase = 0
let last = 0
let started = false

/** Set the resting period in seconds (15 rest / 5 engaged / etc). */
export function setTier(seconds: number): void {
  target = (2 * Math.PI) / seconds
}

/** 0..1 breath value for the current frame. */
export function breath(): number {
  if (reduce) return 0.5
  return Math.sin(phase) * 0.5 + 0.5
}

function step(now: number): void {
  const s = now / 1000
  const dt = last ? Math.min(s - last, 0.1) : 0
  last = s
  omega += (target - omega) * 0.02
  phase += omega * dt
  requestAnimationFrame(step)
}

export function startBreath(): void {
  if (started || reduce) return
  started = true
  requestAnimationFrame(step)
}
