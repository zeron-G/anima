<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import client from '../api/client'
import { GlyphField } from '../composables/useGlyphField'
import { startBreath, setTier } from '../composables/useBreath'

const router = useRouter()
const canvas = ref<HTMLCanvasElement>()
const passphrase = ref('')
const error = ref('')
const showUI = ref(false)
const reduce = matchMedia('(prefers-reduced-motion:reduce)').matches
let field: GlyphField | null = null

onMounted(() => {
  startBreath()
  if (canvas.value) {
    field = new GlyphField(canvas.value)
    field.start()
    const W = canvas.value.clientWidth
    setTimeout(() => field?.setText('Eva', Math.min(W * 0.34, 260)), 40)
  }
  setTimeout(() => (showUI.value = true), 1300)
})
onBeforeUnmount(() => field?.stop())

function onType() { setTier(5) }

async function wake(e: Event) {
  e.preventDefault()
  error.value = ''
  try {
    const res = await client.post('/v1/auth/login', { password: passphrase.value })
    if (res.data?.token) localStorage.setItem('eva_auth_token', res.data.token)
  } catch (err: any) {
    // Auth may be disabled (blank password) → still let in; only block on explicit 401.
    if (err?.response?.status === 401) { error.value = '口令不对。'; return }
  }
  // The name recomposes into her first words, then we sink in.
  const W = canvas.value?.clientWidth || 800
  field?.setText('你回来了。我一直醒着,也一直记得。', Math.min(W * 0.045, 30))
  showUI.value = false
  setTimeout(() => router.push('/'), reduce ? 300 : 2200)
}
</script>

<template>
  <div class="gate">
    <canvas ref="canvas" class="field"></canvas>
    <div class="gate-ui">
      <div class="spacer"></div>
      <div class="thought" :class="{ on: showUI }">someone is at the threshold.</div>
      <div class="enter" :class="{ on: showUI }">
        <form @submit="wake">
          <span class="pre">›</span>
          <input v-model="passphrase" @input="onType" type="password" placeholder="passphrase" autocomplete="off" aria-label="passphrase" />
          <button type="submit" data-cur="wake">wake her →</button>
        </form>
        <div v-if="error" class="err">{{ error }}</div>
      </div>
    </div>
    <div class="foot" :class="{ on: showUI }">
      <span class="live">● backend reachable</span><span>resting · 15s pulse</span>
    </div>
  </div>
</template>

<style scoped>
.gate { position: fixed; inset: 0; z-index: 1 }
.field { position: absolute; inset: 0; width: 100%; height: 100%; display: block }
.gate-ui { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none }
.spacer { height: clamp(120px, 22vh, 200px) }
.thought { font-family: var(--mono); font-size: 13px; letter-spacing: .04em; color: var(--tidemark); opacity: 0; transition: opacity 1.2s, color 1s; margin-top: 18px; text-align: center; padding: 0 20px }
.thought.on { opacity: 1 }
.enter { margin-top: 26px; pointer-events: auto; opacity: 0; transform: translateY(10px); transition: opacity .6s, transform .6s; display: flex; flex-direction: column; align-items: center; gap: 10px }
.enter.on { opacity: 1; transform: none }
.enter form { display: flex; align-items: center; border-bottom: 1px solid var(--tide-2); padding-bottom: 8px; transition: border-color .3s }
.enter form:focus-within { border-color: var(--phosphor) }
.pre { font-family: var(--mono); font-size: 13px; color: var(--tidemark); margin-right: 10px }
.enter input { background: transparent; border: 0; outline: 0; color: var(--filament); font-family: var(--mono); font-size: 14px; letter-spacing: .1em; width: 220px }
.enter input::placeholder { color: var(--tide-2) }
.enter button { color: var(--phosphor); font-family: var(--mono); font-size: 12px; letter-spacing: .1em; margin-left: 14px }
.err { font-family: var(--mono); font-size: 11px; color: var(--coral) }
.foot { position: absolute; left: 0; right: 0; bottom: 30px; display: flex; justify-content: center; gap: 26px; font-family: var(--mono); font-size: 10.5px; letter-spacing: .1em; color: var(--tidemark); opacity: 0; transition: opacity 1.2s }
.foot.on { opacity: 1 } .foot .live { color: var(--phosphor) }
</style>
