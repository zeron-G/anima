<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  config: any
}>()

const emit = defineEmits<{
  (e: 'update', key: string, value: any): void
}>()

const tier1Model = ref(props.config?.llm?.tier1?.model || '')
const tier2Model = ref(props.config?.llm?.tier2?.model || '')

function saveTier1() {
  emit('update', 'llm.tier1.model', tier1Model.value)
}
function saveTier2() {
  emit('update', 'llm.tier2.model', tier2Model.value)
}
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">LLM 配置</h3>
    <div class="field">
      <label>Tier 1 (用户消息)</label>
      <div class="input-row">
        <input v-model="tier1Model" class="field-input" />
        <button class="save-btn" @click="saveTier1">保存</button>
      </div>
    </div>
    <div class="field">
      <label>Tier 2 (内部思考)</label>
      <div class="input-row">
        <input v-model="tier2Model" class="field-input" />
        <button class="save-btn" @click="saveTier2">保存</button>
      </div>
    </div>
    <div class="field-info">
      <span>前缀路由: codex/ → Codex OAuth | openai/ → OpenAI API | local/ → 本地 | 默认 → Anthropic</span>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: 20px; }
.card-title { font-size: 15px; font-weight: 500; color: var(--eva-ice); margin-bottom: 16px; }
.field { margin-bottom: 12px; }
.field label { display: block; font-size: 12px; color: var(--eva-text-dim); margin-bottom: 4px; }
.input-row { display: flex; gap: 8px; }
.field-input { flex: 1; background: hsla(220, 20%, 10%, 0.5); border: 1px solid hsla(200, 30%, 30%, 0.2); border-radius: 8px; padding: 8px 12px; color: var(--eva-text); font-size: 13px; font-family: 'JetBrains Mono', monospace; outline: none; }
.field-input:focus { border-color: hsla(200, 60%, 50%, 0.4); }
.save-btn { padding: 8px 14px; border-radius: 8px; border: 1px solid hsla(200, 50%, 50%, 0.2); background: hsla(200, 40%, 25%, 0.4); color: var(--eva-ice); font-size: 12px; cursor: pointer; }
.save-btn:hover { background: hsla(200, 40%, 30%, 0.5); }
.field-info { font-size: 11px; color: var(--eva-text-dim); opacity: 0.5; margin-top: 8px; }
</style>
