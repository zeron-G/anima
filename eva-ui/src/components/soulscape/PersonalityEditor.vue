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
      <h3>{{ title }}</h3>
      <span class="char-count" :class="{ over: maxLength && charCount > maxLength }">
        {{ charCount }}{{ maxLength ? `/${maxLength}` : '' }}
      </span>
    </div>
    <textarea
      v-model="editContent"
      class="editor-textarea"
      :placeholder="`编辑 ${title}...`"
    />
    <button class="save-btn" @click="save" :disabled="saving">
      {{ saving ? '保存中...' : '保存' }}
    </button>
  </div>
</template>

<style scoped>
.editor-panel {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.editor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.editor-header h3 {
  font-size: 15px;
  font-weight: 500;
  color: var(--eva-text);
}

.char-count {
  font-size: 11px;
  color: var(--eva-text-dim);
}

.char-count.over {
  color: hsl(0, 60%, 60%);
}

.editor-textarea {
  flex: 1;
  min-height: 200px;
  background: hsla(220, 20%, 10%, 0.5);
  border: 1px solid hsla(200, 30%, 30%, 0.2);
  border-radius: 8px;
  padding: 12px;
  color: var(--eva-text);
  font-size: 13px;
  line-height: 1.6;
  font-family: inherit;
  resize: vertical;
  outline: none;
  transition: border-color 0.3s;
}

.editor-textarea:focus {
  border-color: hsla(200, 60%, 50%, 0.4);
}

.save-btn {
  align-self: flex-end;
  padding: 8px 20px;
  border-radius: 8px;
  border: 1px solid hsla(200, 50%, 50%, 0.2);
  background: hsla(200, 40%, 25%, 0.4);
  color: var(--eva-ice);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.save-btn:hover {
  background: hsla(200, 40%, 30%, 0.5);
  box-shadow: 0 0 12px hsla(200, 60%, 50%, 0.15);
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
