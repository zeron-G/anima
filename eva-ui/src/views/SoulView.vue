<script setup lang="ts">
import { onMounted, ref } from 'vue'
import Stratum from '../components/Stratum.vue'
import { getEmotion, getPersonality, getRelationship, getGrowthLog, type Emotion } from '../api/facets'

const emotion = ref<Emotion | null>(null)
const persona = ref('')
const relation = ref('')
const growthEntries = ref<number | null>(null)
const err = ref(false)

function excerpt(md: string, n = 320): string {
  return md.replace(/^#.*$/gm, '').replace(/\n{2,}/g, '\n').trim().slice(0, n).trim()
}
const pct = (v?: number) => v == null ? '—' : Math.round(v * 100) + '%'

onMounted(async () => {
  try {
    const [e, p, r, g] = await Promise.all([getEmotion(), getPersonality(), getRelationship(), getGrowthLog()])
    emotion.value = e; persona.value = p; relation.value = r
    growthEntries.value = (g.match(/^[-#*]|\n\d+\./gm)?.length) || (g ? g.split(/\n{2,}/).filter(Boolean).length : 0)
  } catch { err.value = true }
})
</script>

<template>
  <Stratum line="她是" line-bold="谁">
    <p v-if="err" class="s-note">没接上灵魂存储。</p>
    <template v-else>
      <div class="s-head">她如何描述自己</div>
      <p class="s-prose">{{ persona ? excerpt(persona) + (persona.length > 320 ? '…' : '') : '（人格散文还空着。）' }}</p>

      <div class="s-head">此刻的情绪</div>
      <div class="s-rows">
        <div class="s-row"><span class="s-lab">mood</span><span class="s-val"><b>{{ emotion?.mood_label || '—' }}</b> · {{ emotion?.user_state }}</span><span class="s-st s-ok">live</span></div>
        <div class="s-row"><span class="s-lab">engagement</span><span class="s-val"><b>{{ pct(emotion?.engagement) }}</b></span><span class="s-st s-dim">投入</span></div>
        <div class="s-row"><span class="s-lab">curiosity</span><span class="s-val"><b>{{ pct(emotion?.curiosity) }}</b></span><span class="s-st s-dim">好奇</span></div>
        <div class="s-row"><span class="s-lab">confidence</span><span class="s-val"><b>{{ pct(emotion?.confidence) }}</b></span><span class="s-st s-dim">笃定</span></div>
        <div class="s-row"><span class="s-lab">valence</span><span class="s-val"><b>{{ emotion ? emotion.valence.toFixed(2) : '—' }}</b> · arousal {{ emotion ? emotion.arousal.toFixed(2) : '—' }}</span><span class="s-st" :class="(emotion?.valence ?? 0) < -0.2 ? 's-warn' : 's-dim'">效价</span></div>
      </div>

      <div class="s-head">与你 · 成长</div>
      <div class="s-rows">
        <div class="s-row"><span class="s-lab">relationship</span><span class="s-val">{{ relation ? excerpt(relation, 70) + '…' : '关系笔记还空着' }}</span><span class="s-st s-ok">deepening</span></div>
        <div class="s-row"><span class="s-lab">growth log</span><span class="s-val"><b>{{ growthEntries ?? '—' }}</b> 条</span><span class="s-st s-dim">entries</span></div>
      </div>
    </template>
  </Stratum>
</template>
