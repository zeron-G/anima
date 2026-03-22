import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export interface EmotionState {
  engagement: number
  confidence: number
  curiosity: number
  concern: number
  mood_label: string
  user_state: string
  intensity: number
}

export const useEmotionStore = defineStore('emotion', () => {
  const current = ref<EmotionState>({
    engagement: 0.5,
    confidence: 0.6,
    curiosity: 0.7,
    concern: 0.2,
    mood_label: 'focused',
    user_state: 'neutral',
    intensity: 0.5,
  })

  const dominant = computed(() => {
    const { engagement, confidence, curiosity, concern } = current.value
    const dims = { engagement, confidence, curiosity, concern }
    return Object.entries(dims).sort((a, b) => b[1] - a[1])[0][0]
  })

  // CSS variables computed from emotion
  const cssVars = computed(() => {
    const e = current.value
    const hue = 200 - e.engagement * 30
    const saturation = 60 + e.curiosity * 20
    const lightness = 12 + e.confidence * 8
    return {
      '--eva-bg-h': `${hue}`,
      '--eva-bg-s': `${saturation}%`,
      '--eva-bg-l': `${lightness}%`,
      '--particle-speed': `${0.5 + e.engagement * 1.5}`,
      '--breath-duration': `${4 - e.engagement * 2}s`,
      '--glow-opacity': `${0.1 + e.confidence * 0.3}`,
      '--pulse-intensity': `${e.concern * 0.5}`,
    }
  })

  function update(data: Partial<EmotionState>) {
    Object.assign(current.value, data)
  }

  function shift(data: { field: string; old: number; new: number }) {
    if (data.field in current.value) {
      (current.value as any)[data.field] = data.new
    }
  }

  return { current, dominant, cssVars, update, shift }
})
