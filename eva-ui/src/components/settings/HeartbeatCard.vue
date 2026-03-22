<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ config: any }>()
const emit = defineEmits<{ (e: 'update', key: string, value: any): void }>()

const scriptInterval = computed(() => props.config?.heartbeat?.script_interval_s || 30)
const llmInterval = computed(() => props.config?.heartbeat?.llm_interval_s || 300)
const majorInterval = computed(() => props.config?.heartbeat?.major_interval_s || 900)
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">心跳配置</h3>
    <div class="heartbeat-info">
      <div class="hb-row">
        <span class="hb-label">Script 心跳</span>
        <span class="hb-value">每 {{ scriptInterval }}s</span>
        <span class="hb-desc">系统采样、文件监控</span>
      </div>
      <div class="hb-row">
        <span class="hb-label">LLM 心跳</span>
        <span class="hb-value">每 {{ Math.round(llmInterval / 60) }}min</span>
        <span class="hb-desc">自主思考、三轴调度</span>
      </div>
      <div class="hb-row">
        <span class="hb-label">Major 心跳</span>
        <span class="hb-value">每 {{ Math.round(majorInterval / 60) }}min</span>
        <span class="hb-desc">进化评估</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: 20px; }
.card-title { font-size: 15px; font-weight: 500; color: var(--eva-ice); margin-bottom: 16px; }
.hb-row { display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid hsla(200, 20%, 20%, 0.15); }
.hb-label { width: 100px; font-size: 13px; color: var(--eva-text); }
.hb-value { width: 80px; font-size: 13px; color: var(--eva-ice); font-weight: 600; }
.hb-desc { font-size: 12px; color: var(--eva-text-dim); }
</style>
