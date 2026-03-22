<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ skills: any[] }>()
const emit = defineEmits<{
  (e: 'install', source: string): void
  (e: 'uninstall', name: string): void
}>()

const installSource = ref('')
const showInstall = ref(false)

function doInstall() {
  if (installSource.value.trim()) {
    emit('install', installSource.value.trim())
    installSource.value = ''
    showInstall.value = false
  }
}
</script>

<template>
  <div class="config-card glass">
    <div class="card-header">
      <h3 class="card-title">Skills</h3>
      <button class="add-btn" @click="showInstall = !showInstall">+ 安装</button>
    </div>

    <div v-if="showInstall" class="install-form">
      <input v-model="installSource" placeholder="Git URL 或本地路径" class="field-input" @keydown.enter="doInstall" />
      <button class="save-btn" @click="doInstall">安装</button>
    </div>

    <div class="skills-list">
      <div v-for="skill in skills" :key="skill.name" class="skill-item">
        <div class="skill-info">
          <span class="skill-name">{{ skill.name }}</span>
          <span class="skill-desc">{{ skill.description || '' }}</span>
        </div>
        <button class="uninstall-btn" @click="emit('uninstall', skill.name)">卸载</button>
      </div>
      <div v-if="!skills?.length" class="empty">无已安装 Skills</div>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.card-title { font-size: 15px; font-weight: 500; color: var(--eva-ice); }
.add-btn { padding: 4px 12px; border-radius: 6px; border: 1px solid hsla(200, 50%, 50%, 0.2); background: transparent; color: var(--eva-ice); font-size: 12px; cursor: pointer; }
.add-btn:hover { background: hsla(200, 40%, 25%, 0.3); }
.install-form { display: flex; gap: 8px; margin-bottom: 12px; }
.field-input { flex: 1; background: hsla(220, 20%, 10%, 0.5); border: 1px solid hsla(200, 30%, 30%, 0.2); border-radius: 8px; padding: 8px 12px; color: var(--eva-text); font-size: 13px; outline: none; }
.save-btn { padding: 8px 14px; border-radius: 8px; border: 1px solid hsla(200, 50%, 50%, 0.2); background: hsla(200, 40%, 25%, 0.4); color: var(--eva-ice); font-size: 12px; cursor: pointer; }
.skills-list { display: flex; flex-direction: column; gap: 6px; }
.skill-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; border-radius: 6px; background: hsla(220, 20%, 12%, 0.3); }
.skill-info { display: flex; flex-direction: column; }
.skill-name { font-size: 13px; color: var(--eva-text); font-weight: 500; }
.skill-desc { font-size: 11px; color: var(--eva-text-dim); }
.uninstall-btn { font-size: 11px; color: hsl(0, 50%, 55%); background: none; border: none; cursor: pointer; opacity: 0.5; }
.uninstall-btn:hover { opacity: 1; }
.empty { color: var(--eva-text-dim); font-size: 13px; text-align: center; padding: 12px; }
</style>
