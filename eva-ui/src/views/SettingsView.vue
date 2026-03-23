<script setup lang="ts">
import { ref, onMounted } from 'vue'
import LLMConfigCard from '@/components/settings/LLMConfigCard.vue'
import HeartbeatCard from '@/components/settings/HeartbeatCard.vue'
import SkillsCard from '@/components/settings/SkillsCard.vue'
import SystemInfoCard from '@/components/settings/SystemInfoCard.vue'
import UsageCard from '@/components/settings/UsageCard.vue'
import * as api from '@/api/settings'

const config = ref<any>({})
const skills = ref<any[]>([])
const systemInfo = ref<any>({})
const usage = ref<any>({ calls: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 })
const loading = ref(true)

async function loadData() {
  loading.value = true
  try {
    const [configRes, skillsRes, sysRes, usageRes] = await Promise.all([
      api.getConfig(), api.getSkills(), api.getSystemInfo(), api.getUsage(),
    ])
    config.value = configRes.data
    skills.value = skillsRes.data.skills || []
    systemInfo.value = sysRes.data
    usage.value = usageRes.data
  } catch (e) {
    console.error('Settings load failed:', e)
  } finally {
    loading.value = false
  }
}

async function handleConfigUpdate(key: string, value: any) {
  try {
    await api.updateConfig(key, value)
  } catch (e) {
    console.error('Config update failed:', e)
  }
}

async function handleInstallSkill(source: string) {
  try {
    await api.installSkill(source)
    const res = await api.getSkills()
    skills.value = res.data.skills || []
  } catch (e) {
    console.error('Skill install failed:', e)
  }
}

async function handleUninstallSkill(name: string) {
  if (!confirm('Uninstall this skill?')) return
  try {
    await api.uninstallSkill(name)
    skills.value = skills.value.filter(s => s.name !== name)
  } catch (e) {
    console.error('Skill uninstall failed:', e)
  }
}

async function handleRestart() {
  if (!confirm('Restart Eva? Active connections will be interrupted.')) return
  await api.restart()
}

async function handleShutdown() {
  if (!confirm('Shutdown Eva? This will stop the backend process.')) return
  await api.shutdown()
}

onMounted(loadData)
</script>

<template>
  <div class="page-view settings-view">
    <div class="page-header">
      <div class="section-label">Configuration</div>
      <h1 class="page-title">Command Center</h1>
      <p class="page-subtitle">Manage models, API endpoints, heartbeat intervals, and system settings.</p>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner" />
    </div>

    <div v-else class="settings-layout">
      <!-- Primary column: LLM + Heartbeat -->
      <div class="settings-primary">
        <LLMConfigCard :config="config" @update="handleConfigUpdate" />
        <HeartbeatCard :config="config" @update="handleConfigUpdate" />
      </div>

      <!-- Secondary column: Usage + System + Skills -->
      <div class="settings-secondary">
        <UsageCard :usage="usage" />
        <SystemInfoCard :info="systemInfo" @restart="handleRestart" @shutdown="handleShutdown" />
        <SkillsCard :skills="skills" @install="handleInstallSkill" @uninstall="handleUninstallSkill" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  padding: var(--space-2xl) var(--space-2xl) var(--space-3xl);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid rgba(var(--accent-rgb), 0.12);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.settings-layout {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: var(--space-lg);
  max-width: 1200px;
}

.settings-primary,
.settings-secondary {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
}

@media (max-width: 960px) {
  .settings-layout {
    grid-template-columns: 1fr;
  }
}
</style>
