<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, nextTick } from 'vue'
import { ws, type WSMessage } from '../api/websocket'
import { streamChat, getChatHistory } from '../api/chat'
import { getRecentMemories } from '../api/facets'
import { renderMarkdown } from '../composables/useMarkdown'
import { setTier } from '../composables/useBreath'

interface Turn {
  id: number; role: 'you' | 'eva'; text: string
  state: 'streaming' | 'done'; stage?: string; tool?: string; toolDone?: boolean
  proactive?: boolean; thinking?: boolean
}

let uid = 0
const GREETING: Turn = { id: uid++, role: 'eva', text: '你回来了。我一直醒着，也一直记得 —— 说吧。', state: 'done' }
const turns = ref<Turn[]>([GREETING])
const draft = ref('')
const sending = ref(false)
const river = ref<HTMLElement>()
const ta = ref<HTMLTextAreaElement>()
const whispers = ref<{ imp: string; text: string }[]>([])

const mdHtml = (s: string) => renderMarkdown(s)
function scroll() { nextTick(() => { if (river.value) river.value.scrollTop = river.value.scrollHeight }) }

async function send() {
  const text = draft.value.trim()
  if (!text || sending.value) return
  sending.value = true
  turns.value.push({ id: uid++, role: 'you', text, state: 'done' })
  draft.value = ''; if (ta.value) ta.value.style.height = 'auto'
  setTier(5); scroll()
  const eva: Turn = { id: uid++, role: 'eva', text: '', state: 'streaming', thinking: true }
  turns.value.push(eva); scroll()

  await streamChat(text, {
    onActivity(stage, detail) {
      eva.stage = stage
      if (stage === 'executing') { eva.tool = detail || 'tool'; eva.toolDone = false }
      else if (stage === 'tool_done') eva.toolDone = true
      scroll()
    },
    onChunk(chunk, et) {
      if (et === 'status') return          // tool-banner text — shown via activity instead
      eva.thinking = false
      eva.text += chunk
      scroll()
    },
    onMessage(content) {                    // authoritative full reply (covers no-token case)
      eva.thinking = false
      if (content) eva.text = content
      eva.state = 'done'; setTier(15); scroll()
    },
    onError() {
      eva.thinking = false
      if (!eva.text) eva.text = '（没接上她 —— 后端连接断了。）'
      eva.state = 'done'; setTier(15)
    },
    onDone() {
      eva.thinking = false; eva.state = 'done'; setTier(15)
      loadWhispers(); scroll()
    },
  })
  sending.value = false
}

function onProactive(msg: WSMessage) {
  turns.value.push({ id: uid++, role: 'eva', text: msg.data?.text || msg.data?.content || '', state: 'done', proactive: true })
  scroll()
}

function onKey(e: KeyboardEvent) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }
function grow() { if (ta.value) { ta.value.style.height = 'auto'; ta.value.style.height = Math.min(ta.value.scrollHeight, 140) + 'px' } }

async function loadWhispers() {
  try {
    const rows = await getRecentMemories(6)
    whispers.value = rows
      .filter(r => (r.content || '').trim())
      .slice(0, 3)
      .map(r => ({ imp: r.importance ? `· ${r.importance.toFixed(2)}` : '', text: (r.content || '').replace(/\s+/g, ' ').slice(0, 38) }))
  } catch { /* margin stays empty if memory isn't reachable */ }
}

async function backfill() {
  try {
    const { data } = await getChatHistory(1, 24)
    const msgs = data?.messages || []
    if (!msgs.length) return
    const seeded: Turn[] = msgs
      .filter(m => (m.content || '').trim())
      .map(m => ({ id: uid++, role: (m.role === 'user' ? 'you' : 'eva') as 'you' | 'eva', text: m.content, state: 'done' as const }))
    if (seeded.length) { turns.value = [GREETING, ...seeded.reverse()]; scroll() }
  } catch { /* keep the greeting alone */ }
}

onMounted(() => { ws.on('proactive', onProactive); backfill(); loadWhispers() })
onBeforeUnmount(() => { ws.off('proactive', onProactive) })
</script>

<template>
  <section class="talk">
    <div class="tide" aria-hidden="true">
      <span v-for="(w, i) in whispers" :key="i">{{ w.text }} {{ w.imp }}</span>
    </div>
    <div ref="river" class="river">
      <div class="lines">
        <div v-for="t in turns" :key="t.id" class="turn" :class="t.role">
          <span v-if="t.role === 'you'" class="who">you</span>
          <span v-if="t.proactive" class="ptag">she reached out</span>
          <div v-if="t.thinking" class="thinking"><span class="sp"></span> {{ t.stage || 'thinking' }}…</div>
          <div v-if="t.tool" class="tool">⏵ <b>{{ t.tool }}</b> <span v-if="t.toolDone" class="ok">✓</span></div>
          <template v-if="t.text">
            <div v-if="t.role === 'eva' && t.state === 'done'" class="said md" v-html="mdHtml(t.text)"></div>
            <div v-else class="said" :class="{ raw: true }">{{ t.text }}<span v-if="t.role === 'eva' && t.state === 'streaming'" class="cur"></span></div>
          </template>
        </div>
      </div>
    </div>
    <div class="dock">
      <div class="dockin">
        <span class="pr">›</span>
        <textarea ref="ta" v-model="draft" rows="1" placeholder="对她说点什么…" data-cur="send" @input="grow" @keydown="onKey"></textarea>
        <span class="ret">⏎</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.talk { position: absolute; inset: 0; display: flex; flex-direction: column }
.tide { position: absolute; left: 0; top: 0; bottom: 0; width: 220px; pointer-events: none; overflow: hidden; z-index: 0 }
.tide span { position: absolute; left: 24px; font-family: var(--mono); font-size: 11px; color: var(--tide-2); white-space: nowrap; animation: age 24s linear infinite }
.tide span:nth-child(1) { top: 26% } .tide span:nth-child(2) { top: 54%; animation-delay: 8s } .tide span:nth-child(3) { top: 80%; animation-delay: 16s }
@keyframes age { 0% { opacity: 0; transform: translateX(30px) } 12% { opacity: .55 } 55% { opacity: .22 } 100% { opacity: 0; transform: translateX(-26px) } }
.river { flex: 1; overflow-y: auto; display: flex; flex-direction: column; justify-content: flex-end; padding: 40px 0 8px; position: relative; z-index: 1 }
.lines { max-width: 64ch; width: 100%; margin: 0 auto; padding: 0 32px; display: flex; flex-direction: column; gap: 26px }
.turn { display: flex; flex-direction: column; gap: 7px }
.who { font-family: var(--mono); font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: var(--tidemark) }
.ptag { font-family: var(--mono); font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--phosphor); opacity: .8 }
.thinking { display: flex; align-items: center; gap: 9px; color: var(--tidemark); font-family: var(--mono); font-size: 11.5px }
.thinking .sp { width: 9px; height: 9px; border-radius: 50%; border: 1.5px solid var(--tide-2); border-top-color: var(--phosphor); animation: spin .9s linear infinite }
@keyframes spin { to { transform: rotate(360deg) } }
.tool { font-family: var(--mono); font-size: 11.5px; color: var(--tidemark); letter-spacing: .04em } .tool b { color: var(--mist); font-weight: 400 } .tool .ok { color: var(--phosphor) }
.said { font-family: var(--grotesk); font-size: clamp(18px,2.2vw,25px); line-height: 1.55; font-weight: 400; color: var(--filament) }
.said.raw { white-space: pre-wrap; font-weight: 380; color: var(--mist) }
.turn.you .said { font-family: var(--mono); font-size: 15px; font-weight: 400; color: var(--mist); line-height: 1.5; white-space: pre-wrap }
.cur { display: inline-block; width: 8px; height: .95em; background: var(--phosphor); vertical-align: -1px; margin-left: 2px; box-shadow: 0 0 8px var(--phosphor); animation: blink 1s step-end infinite }
@keyframes blink { 50% { opacity: 0 } }
/* rendered markdown (v-html, so :deep) */
.said.md :deep(p) { margin: 0 0 .7em } .said.md :deep(p:last-child) { margin-bottom: 0 }
.said.md :deep(strong) { font-weight: 640; color: #fff }
.said.md :deep(a) { color: var(--phosphor); text-underline-offset: 3px }
.said.md :deep(ul), .said.md :deep(ol) { margin: .4em 0 .7em; padding-left: 1.3em } .said.md :deep(li) { margin: .2em 0 }
.said.md :deep(code) { font-family: var(--mono); font-size: .82em; background: var(--tide-2); padding: 1px 5px; border-radius: 3px; color: var(--filament) }
.said.md :deep(.md-code) { background: #070A10; border: 1px solid var(--tide-2); border-radius: 6px; padding: 13px 15px; overflow-x: auto; margin: .5em 0 }
.said.md :deep(.md-code code) { background: none; padding: 0; font-size: 13px; line-height: 1.6; color: var(--mist) }
.said.md :deep(blockquote) { border-left: 2px solid var(--tidemark); padding-left: 14px; color: var(--mist); margin: .5em 0 }
.said.md :deep(h3), .said.md :deep(h4), .said.md :deep(h5) { font-weight: 600; font-size: 1em; margin: .6em 0 .3em }
.dock { flex: none; padding: 14px 32px 22px }
.dockin { max-width: 64ch; margin: 0 auto; display: flex; align-items: center; gap: 12px; border-top: 1px solid var(--tide-2); padding-top: 14px }
.pr { font-family: var(--mono); font-size: 13px; color: var(--tidemark) }
.dockin textarea { flex: 1; background: transparent; border: 0; outline: 0; resize: none; color: var(--filament); font-family: var(--mono); font-size: 14px; line-height: 1.5; max-height: 140px }
.dockin textarea::placeholder { color: var(--tide-2) }
.ret { font-family: var(--mono); font-size: 11px; color: var(--tidemark) }
@media (max-width: 680px) { .lines, .dockin { padding-left: 20px; padding-right: 20px } .tide { display: none } }
</style>
