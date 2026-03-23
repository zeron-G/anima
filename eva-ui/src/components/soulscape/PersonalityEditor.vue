<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  title: string
  content: string
  maxLength?: number
}>()

const emit = defineEmits<{
  (e: 'save', content: string): void
}>()

const editContent = ref(props.content)
const saving = ref(false)

watch(() => props.content, (v) => { editContent.value = v })

const charCount = ref(0)
watch(editContent, (v) => { charCount.value = v.length })

async function save() {
  saving.value = true
  emit('save', editContent.value)
  setTimeout(() => { saving.value = false }, 500)
}
</script>

<template>
  <div class="editor-panel glass">
    <div class="editor-header">
      <h3 class="editor-title">{{ title }}</h3>
      <span class="char-count" :class="{ over: maxLength && charCount > maxLength }">
        {{ charCount }}{{ maxLength ? `/${maxLength}` : '' }}
      </span>
    </div>
    <textarea
      v-model="editContent"
      class="editor-textarea"
      :placeholder="`Edit ${title}...`"
    />
    <button class="save-btn btn-primary" @click="save" :disabled="saving">
      {{ saving ? 'Saving...' : 'Save' }}
    </button>
  </div>
</template>

<style scoped>
.editor-panel {
  padding: var(--space-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.editor-title {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 400;
  color: var(--text);
}

.char-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
}

.char-count.over {
  color: var(--error);
}

.editor-textarea {
  flex: 1;
  min-height: 180px;
  background: rgba(10, 10, 20, 0.5);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px;
  color: var(--text);
  font-size: 13px;
  line-height: 1.7;
  font-family: var(--font-body);
  resize: vertical;
  outline: none;
  transition: border-color var(--transition-fast);
}

.editor-textarea:focus {
  border-color: rgba(var(--accent-rgb), 0.25);
}

.editor-textarea::placeholder {
  color: var(--text-dim);
}

.save-btn {
  align-self: flex-end;
  padding: 8px 24px;
  font-size: 12px;
}
</style>
