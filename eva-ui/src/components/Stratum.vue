<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { decodeInPlace } from '../composables/useDecode'

// The shared scaffold for a deep stratum: a receding mono substrate with her
// voice (the depthline) decoding into the dark, and a body slot for the facet.
defineProps<{ line: string; lineBold: string }>()
const dl = ref<HTMLElement>()
onMounted(() => { if (dl.value) decodeInPlace(dl.value, 460) })
</script>

<template>
  <section class="stratum">
    <div ref="dl" class="depthline">{{ line }}<b>{{ lineBold }}</b></div>
    <div class="body"><slot /></div>
  </section>
</template>

<style scoped>
.stratum { position: absolute; inset: 0; overflow-y: auto; padding: 40px 44px 64px }
.depthline { font-family: var(--grotesk); font-size: clamp(22px,3vw,34px); font-weight: 360; color: var(--tidemark); max-width: 30ch; line-height: 1.35; margin-bottom: 30px; text-wrap: balance }
.depthline b { color: var(--mist); font-weight: 520 }
.body { animation: rise .5s cubic-bezier(.2,.7,.2,1) both }
@keyframes rise { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
@media (max-width: 680px) { .stratum { padding: 28px 22px 52px } .s-row { grid-template-columns: 110px 1fr auto } }
</style>
