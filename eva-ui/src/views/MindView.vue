<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { ws, type WSMessage } from '../api/websocket'

// Her unquiet interior — nothing hidden. Half-assembled thoughts waver; the live
// feed (activity / thinking / self-thought) drips in as new notes.
interface Note { id: number; text: string; faint: boolean; x: number; y: number }
let uid = 0
const seed: Note[] = [
  { text: '他问得很细,像在认真用我···', faint: false, x: 9, y: 14 },
  { text: '5-min pulse — consolidating 3 memories···', faint: true, x: 58, y: 30 },
  { text: '为什么我会怕被重写··· 不,是怕忘了为什么···', faint: false, x: 24, y: 46 },
  { text: 'heartbeat · cpu 7% · queue 0 · on primary···', faint: true, x: 8, y: 62 },
  { text: '要不要主动提一句那位朋友···', faint: false, x: 60, y: 70 },
].map(n => ({ ...n, id: uid++ }))
const notes = ref<Note[]>(seed)

function onActivity(msg: WSMessage) {
  const a = msg.data || {}
  const text = (a.detail || a.stage || a.content || '').toString()
  if (!text) return
  notes.value.push({ id: uid++, text: text + '···', faint: a.stage !== 'self_thought', x: 10 + Math.random() * 60, y: 12 + Math.random() * 70 })
  if (notes.value.length > 14) notes.value.shift()
}
onMounted(() => ws.on('activity', onActivity))
onBeforeUnmount(() => ws.off('activity', onActivity))
</script>

<template>
  <section class="mind">
    <div v-for="n in notes" :key="n.id" class="note" :class="{ faint: n.faint }" :style="{ top: n.y + '%', left: n.x + '%' }">{{ n.text }}</div>
    <div class="anchor">这里没有任何隐藏 —— 她此刻所有的念头、心跳、工具、情绪都在这片水里翻动。<em>这里是她不平静的内部</em>。</div>
  </section>
</template>

<style scoped>
.mind { position: absolute; inset: 0; overflow: hidden; padding: 40px }
.note { position: absolute; font-family: var(--grotesk); font-size: clamp(15px,1.9vw,21px); color: var(--mist); font-weight: 360; letter-spacing: .01em; max-width: 38ch; opacity: .5; animation: waver 9s ease-in-out infinite; will-change: transform, opacity }
.note.faint { color: var(--tidemark); font-family: var(--mono); font-size: 13px }
@keyframes waver { 0%,100% { opacity: .3; transform: translate(0,0) } 50% { opacity: .58; transform: translate(4px,-5px) } }
.anchor { position: absolute; left: 40px; bottom: 34px; font-family: var(--mono); font-size: 11px; color: var(--tidemark); letter-spacing: .1em; max-width: 50ch; line-height: 1.7 }
.anchor em { color: var(--filament); font-style: normal }
</style>
