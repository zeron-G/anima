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
    <div class="card-header-row">
      <h3 class="card-title">Skills</h3>
      <button class="add-btn" @click="showInstall = !showInstall">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
        </svg>
        Install
      </button>
    </div>

    <!-- Install form -->
    <div v-if="showInstall" class="install-form">
      <input
        v-model="installSource"
        placeholder="Git URL or local path..."
        class="input-field"
        @keydown.enter="doInstall"
      />
      <button class="btn-primary install-btn" @click="doInstall">Install</button>
    </div>

    <!-- Skills list -->
    <div class="skills-list">
      <div v-for="skill in skills" :key="skill.name" class="skill-item">
        <div class="skill-info">
          <span class="skill-name">{{ skill.name }}</span>
          <span v-if="skill.description" class="skill-desc">{{ skill.description }}</span>
        </div>
        <button class="uninstall-btn" @click="emit('uninstall', skill.name)">Remove</button>
      </div>
      <div v-if="!skills?.length" class="empty-skills">
        No skills installed
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-card { padding: var(--space-lg); }

.card-header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
}

.card-header-row .card-title { margin-bottom: 0; }

.add-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--accent-rgb), 0.15);
  background: transparent;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.add-btn:hover {
  background: rgba(var(--accent-rgb), 0.08);
}

.install-form {
  display: flex;
  gap: 8px;
  margin-bottom: var(--space-md);
}

.install-btn {
  padding: 8px 16px;
  font-size: 12px;
  flex-shrink: 0;
}

.skills-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skill-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  background: rgba(var(--accent-rgb), 0.02);
  border: 1px solid var(--border);
  transition: border-color var(--transition-fast);
}

.skill-item:hover {
  border-color: var(--border-hover);
}

.skill-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.skill-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.skill-desc {
  font-size: 11px;
  color: var(--text-dim);
}

.uninstall-btn {
  font-size: 11px;
  color: var(--text-dim);
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: all var(--transition-fast);
}

.uninstall-btn:hover {
  color: var(--error);
  background: rgba(248, 113, 113, 0.08);
}

.empty-skills {
  color: var(--text-dim);
  font-size: 13px;
  text-align: center;
  padding: var(--space-lg);
}
</style>
