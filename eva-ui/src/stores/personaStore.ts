import { defineStore } from 'pinia'
import { ref } from 'vue'

// S3: personality is prose (personality.md / relationship.md), not a numeric
// vector. The former 6-dim PersonaState was a redundant slow-emotion and has
// been removed — edit prose via the Soulscape editors instead.
export const usePersonaStore = defineStore('persona', () => {
  const personality = ref('')
  const relationship = ref('')
  const growthLog = ref('')
  const goldenReplies = ref<any[]>([])
  const styleRules = ref('')
  const boundaries = ref('')
  const driftEntries = ref<any[]>([])

  return {
    personality, relationship, growthLog,
    goldenReplies, styleRules, boundaries, driftEntries,
  }
})
