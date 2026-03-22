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
  if (!confirm(`确定卸载 ${name}？`)) return
  try {
    await api.uninstallSkill(name)
    skills.value = skills.value.filter(s => s.name !== name)
  } catch (e) {
    console.error('Skill uninstall failed:', e)
  }
}

async function handleRestart() {
  if (!confirm('确定重启 Eva？')) return
  await api.restart()
}

async function handleShutdown() {
  if (!confirm('确定关闭 Eva？')) return
  await api.shutdown()
}

onMounted(loadData)
</script>

<template>
  <div class="settings-view">
    <h2 class="page-title">控制中枢</h2>
    <div class="settings-grid">
      <LLMConfigCard :config="config" @update="handleConfigUpdate" />
      <HeartbeatCard :config="config" />
      <UsageCard :usage="usage" />
      <SkillsCard :skills="skills" @install="handleInstallSkill" @uninstall="handleUninstallSkill" />
      <SystemInfoCard :info="systemInfo" @restart="handleRestart" @shutdown="handleShutdown" />
    </div>
  </div>
</template>

<style scoped>
.settings-view { height: 100%; padding: 24px; overflow-y: auto; }
.page-title { font-size: 22px; font-weight: 300; color: var(--eva-ice); margin-bottom: 24px; letter-spacing: 2px; }
.settings-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }
@media (max-width: 800px) { .settings-grid { grid-template-columns: 1fr; } }
</style>
