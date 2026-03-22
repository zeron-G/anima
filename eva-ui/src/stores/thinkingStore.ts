import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface ThinkingEntry {
  axis: 'human' | 'self' | 'world'
  summary: string
  action: 'quiet' | 'updated' | 'proposed'
  timestamp: number
}

export const useThinkingStore = defineStore('thinking', () => {
  const entries = ref<ThinkingEntry[]>([])
  const latest = ref<ThinkingEntry | null>(null)

  function add(data: ThinkingEntry) {
    entries.value.push(data)
    if (entries.value.length > 20) entries.value.shift()
    latest.value = data
  }

  return { entries, latest, add }
})
