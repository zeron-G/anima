import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface PersonaState {
  warmth: number
  assertiveness: number
  playfulness: number
  formality: number
  curiosity: number
  independence: number
  [key: string]: number | string  // allow extra fields like last_updated
}

export const usePersonaStore = defineStore('persona', () => {
  const state = ref<PersonaState>({
    warmth: 0.7,
    assertiveness: 0.4,
    playfulness: 0.7,
    formality: 0.3,
    curiosity: 0.7,
    independence: 0.5,
  })

  const personality = ref('')
  const relationship = ref('')
  const growthLog = ref('')
  const goldenReplies = ref<any[]>([])
  const styleRules = ref('')
  const boundaries = ref('')
  const driftEntries = ref<any[]>([])

  function updateState(data: Partial<PersonaState>) {
    Object.assign(state.value, data)
  }

  return {
    state, personality, relationship, growthLog,
    goldenReplies, styleRules, boundaries, driftEntries,
    updateState,
  }
})
