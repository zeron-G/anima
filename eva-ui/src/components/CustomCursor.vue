<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'

const dot = ref<HTMLElement>()
const ring = ref<HTMLElement>()
const label = ref<HTMLElement>()
const fine = matchMedia('(pointer:fine)').matches
const reduce = matchMedia('(prefers-reduced-motion:reduce)').matches

const LABELS: Record<string, string> = { wake: 'wake', open: 'open', descend: 'descend', send: 'send', '': 'type' }
let mx = innerWidth / 2, my = innerHeight / 2, rx = mx, ry = my, raf = 0

function onMove(e: MouseEvent) { mx = e.clientX; my = e.clientY }
function onOver(e: MouseEvent) {
  const el = (e.target as HTMLElement).closest('[data-cur]')
  if (el && ring.value && label.value) {
    ring.value.classList.add('lg')
    const k = el.getAttribute('data-cur') || ''
    const l = LABELS[k] ?? k
    if (l) { label.value.textContent = l; label.value.classList.add('on') }
  }
}
function onOut(e: MouseEvent) {
  if ((e.target as HTMLElement).closest('[data-cur]') && ring.value && label.value) {
    ring.value.classList.remove('lg'); label.value.classList.remove('on')
  }
}
function loop() {
  rx += (mx - rx) * 0.18; ry += (my - ry) * 0.18
  if (dot.value) dot.value.style.transform = `translate(${mx}px,${my}px)`
  if (ring.value) ring.value.style.transform = `translate(${rx}px,${ry}px)`
  if (label.value) { label.value.style.left = rx + 'px'; label.value.style.top = ry + 'px' }
  raf = requestAnimationFrame(loop)
}

onMounted(() => {
  if (!fine || reduce) return
  addEventListener('mousemove', onMove)
  document.addEventListener('mouseover', onOver)
  document.addEventListener('mouseout', onOut)
  raf = requestAnimationFrame(loop)
})
onBeforeUnmount(() => {
  removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseover', onOver)
  document.removeEventListener('mouseout', onOut)
  cancelAnimationFrame(raf)
})
</script>

<template>
  <div v-if="fine && !reduce">
    <div ref="dot" class="cdot"></div>
    <div ref="ring" class="cring"></div>
    <div ref="label" class="clabel"></div>
  </div>
</template>

<style scoped>
.cdot, .cring { position: fixed; top: 0; left: 0; z-index: 90; pointer-events: none; border-radius: 50%; mix-blend-mode: difference; will-change: transform }
.cdot { width: 6px; height: 6px; background: #fff; margin: -3px 0 0 -3px }
.cring { width: 34px; height: 34px; border: 1px solid rgba(255,255,255,.6); margin: -17px 0 0 -17px; transition: width .22s, height .22s, margin .22s }
.cring.lg { width: 64px; height: 64px; margin: -32px 0 0 -32px; border-color: var(--phosphor) }
.clabel { position: fixed; z-index: 90; pointer-events: none; font-family: var(--mono); font-size: 9.5px; letter-spacing: .18em; text-transform: uppercase; color: var(--phosphor); opacity: 0; transform: translate(-50%, 18px); transition: opacity .2s }
.clabel.on { opacity: 1 }
</style>
