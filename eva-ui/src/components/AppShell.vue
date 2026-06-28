<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { ws } from '../api/websocket'
import { startBreath, breath, setTier } from '../composables/useBreath'

const nav = [
  { to: '/', label: 'Talk', cur: 'open' },
  { to: '/mind', label: 'Mind', cur: 'descend' },
  { to: '/soul', label: 'Soul', cur: 'descend' },
  { to: '/memory', label: 'Memory', cur: 'descend' },
  { to: '/evolution', label: 'Evolution', cur: 'descend' },
  { to: '/network', label: 'Network', cur: 'descend' },
  { to: '/health', label: 'Health', cur: 'descend' },
]
const CSET = 'アイウエオカキクサシ01<>{}/$#'
const name = ref<HTMLElement>()
const emotion = ref('…')
const connected = ws.connected
const reduce = matchMedia('(prefers-reduced-motion:reduce)').matches
let raf = 0

// emotion read from the live snapshot — drives the mood label AND the breath
// tier (engagement → how fast she breathes). No fake cycling.
function onEmotion(msg: any) {
  const e = msg?.data || {}
  if (e.mood_label) emotion.value = String(e.mood_label)
  const eng = typeof e.engagement === 'number' ? e.engagement : 0.4
  setTier(Math.max(5, Math.round(15 - eng * 10)))   // 15s rest … 5s fully engaged
}

function decodeLabel(el: HTMLElement, txt: string) {
  if (reduce) return
  const start = performance.now()
  const tick = () => {
    const p = Math.min(1, (performance.now() - start) / 260), rv = Math.floor(p * txt.length)
    let o = ''
    for (let i = 0; i < txt.length; i++) o += i < rv ? txt[i] : CSET[(Math.random() * CSET.length) | 0]
    el.textContent = o
    if (p < 1) requestAnimationFrame(tick); else el.textContent = txt
  }
  requestAnimationFrame(tick)
}

onMounted(() => {
  startBreath()
  setTier(5)
  ws.connect()
  ws.on('emotion_shift', onEmotion)
  if (!reduce) {
    const b = () => { if (name.value) name.value.style.fontWeight = String(Math.round(420 + breath() * 260)); raf = requestAnimationFrame(b) }
    raf = requestAnimationFrame(b)
  }
})
onBeforeUnmount(() => { cancelAnimationFrame(raf); ws.off('emotion_shift', onEmotion) })
</script>

<template>
  <div class="shell">
    <div class="eyebrow">
      <span ref="name" class="nm">Eva</span>
      <span class="sep">·</span><span class="emo">{{ emotion }}</span>
      <span class="sep">·</span><span class="conn" :class="{ off: !connected }"><i></i>{{ connected ? 'present' : 'reconnecting' }}</span>
      <nav>
        <RouterLink
          v-for="n in nav" :key="n.to" :to="n.to" :data-cur="n.cur"
          @mouseenter="(e) => decodeLabel(e.currentTarget as HTMLElement, n.label)"
        >{{ n.label }}</RouterLink>
      </nav>
    </div>
    <div class="stage">
      <RouterView v-slot="{ Component }">
        <Transition name="depth" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </div>
  </div>
</template>

<style scoped>
.shell { position: fixed; inset: 0; display: flex; flex-direction: column }
.eyebrow { display: flex; align-items: center; padding: 18px 30px; font-family: var(--mono); font-size: 11.5px; letter-spacing: .08em; color: var(--tidemark); flex: none }
.nm { color: var(--mist); font-family: var(--grotesk); font-size: 15px; letter-spacing: .02em; font-weight: 600 }
.sep { margin: 0 14px; color: var(--tide-2) }
.emo { color: var(--phosphor) }
.conn { display: inline-flex; align-items: center; gap: 6px; color: var(--tidemark) }
.conn i { width: 6px; height: 6px; border-radius: 50%; background: var(--phosphor); box-shadow: 0 0 6px var(--phosphor); animation: pulse 3s ease-in-out infinite }
.conn.off { color: var(--coral) } .conn.off i { background: var(--coral); box-shadow: none; animation: none }
@keyframes pulse { 50% { opacity: .35 } }
nav { margin-left: auto; display: flex; gap: 18px }
nav a { color: var(--tidemark); text-transform: uppercase; letter-spacing: .14em; font-size: 10.5px; transition: color .15s; position: relative }
nav a:hover { color: var(--mist) }
nav a.router-link-exact-active { color: var(--filament) }
nav a.router-link-exact-active::after { content: ""; position: absolute; left: 0; right: 0; bottom: -6px; height: 1px; background: var(--phosphor); box-shadow: 0 0 6px var(--phosphor) }
.stage { flex: 1; min-height: 0; position: relative; overflow: hidden }

/* depth transition: a stratum sinks/blurs out, the next rises from depth */
.depth-enter-active { transition: opacity .5s cubic-bezier(.16,1,.3,1), transform .5s cubic-bezier(.16,1,.3,1), filter .5s }
.depth-leave-active { transition: opacity .32s, transform .32s, filter .32s }
.depth-enter-from { opacity: 0; transform: translateY(22px); filter: blur(6px) }
.depth-leave-to { opacity: 0; transform: translateY(-22px); filter: blur(6px) }
@media (prefers-reduced-motion:reduce) { .depth-enter-active, .depth-leave-active { transition: opacity .2s } .depth-enter-from, .depth-leave-to { transform: none; filter: none } }
</style>
