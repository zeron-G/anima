<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useEmotionStore } from '@/stores/emotionStore'
import { usePersonaStore } from '@/stores/personaStore'
import { usePlatform } from '@/composables/usePlatform'
import { sceneManager } from '@/three/SceneManager'
import { createEmotionOrbScene } from '@/three/EmotionOrbScene'
import { createRelationshipOrbitScene } from '@/three/RelationshipOrbitScene'
import PersonaPetals from '@/components/soulscape/PersonaPetals.vue'
import GrowthTimeline from '@/components/soulscape/GrowthTimeline.vue'
import PersonalityEditor from '@/components/soulscape/PersonalityEditor.vue'
import GoldenReplyManager from '@/components/soulscape/GoldenReplyManager.vue'
import * as api from '@/api/soulscape'

const emotion = useEmotionStore()
const persona = usePersonaStore()
const { enable3D } = usePlatform()

const orbCanvas = ref<HTMLCanvasElement>()
const orbitCanvas = ref<HTMLCanvasElement>()
const activeTab = ref<'personality' | 'style' | 'golden'>('personality')
const loading = ref(true)

// Extract only numeric petal fields for PersonaPetals (which expects Record<string, number>)
const petalData = computed<Record<string, number>>(() => {
  const result: Record<string, number> = {}
  for (const [key, value] of Object.entries(persona.state)) {
    if (typeof value === 'number') result[key] = value
  }
  return result
})

// Load all soulscape data
async function loadData() {
  loading.value = true
  try {
    const [personaRes, personalityRes, relationshipRes, growthRes, goldenRes, styleRes, driftRes] = await Promise.all([
      api.getPersona(),
      api.getPersonality(),
      api.getRelationship(),
      api.getGrowthLog(),
      api.getGoldenReplies(),
      api.getStyleRules(),
      api.getDrift(50),
    ])
    persona.updateState(personaRes.data)
    persona.personality = personalityRes.data.content || ''
    persona.relationship = relationshipRes.data.content || ''
    persona.growthLog = growthRes.data.content || ''
    persona.goldenReplies = goldenRes.data.replies || []
    persona.styleRules = styleRes.data.content || ''
    persona.driftEntries = driftRes.data.entries || []
  } catch (e) {
    console.error('Soulscape load failed:', e)
  } finally {
    loading.value = false
  }
}

// Three.js setup
onMounted(async () => {
  await loadData()

  if (enable3D && orbCanvas.value) {
    sceneManager.register('emotionOrb', orbCanvas.value, (renderer) => {
      return createEmotionOrbScene(renderer, () => emotion.current)
    })
    sceneManager.activate('emotionOrb')
  }

  if (enable3D && orbitCanvas.value) {
    sceneManager.register('relationshipOrbit', orbitCanvas.value, (renderer) => {
      return createRelationshipOrbitScene(renderer)
    })
    sceneManager.activate('relationshipOrbit')
  }
})

onUnmounted(() => {
  sceneManager.dispose('emotionOrb')
  sceneManager.dispose('relationshipOrbit')
})

// Handlers
async function handlePetalUpdate(key: string, value: number) {
  persona.state[key] = value
  try {
    await api.updatePersona({ [key]: value })
  } catch (e) {
    console.error('Persona update failed:', e)
  }
}

async function savePersonality(content: string) {
  try {
    await api.updatePersonality(content)
    persona.personality = content
  } catch (e) {
    console.error('Personality save failed:', e)
  }
}

async function saveRelationship(content: string) {
  try {
    await api.updateRelationship(content)
    persona.relationship = content
  } catch (e) {
    console.error('Relationship save failed:', e)
  }
}

async function saveStyleRules(content: string) {
  try {
    await api.updateStyleRules(content)
    persona.styleRules = content
  } catch (e) {
    console.error('Style rules save failed:', e)
  }
}

async function deleteGolden(id: string) {
  try {
    await api.deleteGoldenReply(id)
    persona.goldenReplies = persona.goldenReplies.filter(r => r.id !== id)
  } catch (e) {
    console.error('Delete golden failed:', e)
  }
}
</script>

<template>
  <div class="soulscape-view">
    <div v-if="loading" class="loading-overlay">
      <div class="spinner" />
    </div>

    <div class="soulscape-grid">
      <!-- Left: Persona Petals + Relationship Orbit -->
      <div class="soulscape-left">
        <div class="section-card glass">
          <h3 class="section-title">性格花瓣</h3>
          <PersonaPetals :data="petalData" @update="handlePetalUpdate" />
        </div>
        <div class="section-card glass">
          <h3 class="section-title">关系轨道</h3>
          <div class="orbit-container">
            <canvas v-if="enable3D" ref="orbitCanvas" width="350" height="250" />
            <div v-else class="fallback-orbit">
              <span class="orbit-eva">Eva 🔵</span>
              <span class="orbit-line">━━━━━━━━</span>
              <span class="orbit-master">主人 🟡</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Center: Emotion Orb -->
      <div class="soulscape-center">
        <div class="orb-container">
          <canvas v-if="enable3D" ref="orbCanvas" width="400" height="400" />
          <div v-else class="fallback-orb">
            <div class="orb-circle breathing">
              <span class="mood-text">{{ emotion.current.mood_label }}</span>
            </div>
          </div>
          <div class="orb-stats">
            <div class="stat" v-for="dim in ['engagement', 'confidence', 'curiosity', 'concern']" :key="dim">
              <span class="stat-label">{{ dim }}</span>
              <div class="stat-bar">
                <div class="stat-fill" :style="{ width: `${(emotion.current as any)[dim] * 100}%` }" />
              </div>
              <span class="stat-value">{{ ((emotion.current as any)[dim] * 100).toFixed(0) }}%</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Right: Editor Panels -->
      <div class="soulscape-right">
        <div class="tab-bar">
          <button :class="{ active: activeTab === 'personality' }" @click="activeTab = 'personality'">人格</button>
          <button :class="{ active: activeTab === 'style' }" @click="activeTab = 'style'">风格</button>
          <button :class="{ active: activeTab === 'golden' }" @click="activeTab = 'golden'">Golden</button>
        </div>

        <div class="tab-content">
          <template v-if="activeTab === 'personality'">
            <PersonalityEditor title="personality.md" :content="persona.personality" :max-length="2000" @save="savePersonality" />
            <PersonalityEditor title="relationship.md" :content="persona.relationship" @save="saveRelationship" />
          </template>
          <template v-else-if="activeTab === 'style'">
            <PersonalityEditor title="style.md" :content="persona.styleRules" @save="saveStyleRules" />
          </template>
          <template v-else-if="activeTab === 'golden'">
            <GoldenReplyManager :replies="persona.goldenReplies" @delete="deleteGolden" />
          </template>
        </div>
      </div>
    </div>

    <!-- Bottom: Growth Timeline -->
    <div class="soulscape-bottom glass">
      <h3 class="section-title">成长时间线</h3>
      <GrowthTimeline :log="persona.growthLog" :drift-entries="persona.driftEntries" />
    </div>
  </div>
</template>

<style scoped>
.soulscape-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 16px;
  gap: 16px;
  overflow-y: auto;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 2px solid hsla(200, 60%, 50%, 0.2);
  border-top-color: var(--eva-ice);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.soulscape-grid {
  display: grid;
  grid-template-columns: 1fr 1.2fr 1fr;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.soulscape-left,
.soulscape-right {
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

.soulscape-center {
  display: flex;
  align-items: center;
  justify-content: center;
}

.section-card {
  padding: 16px;
}

.section-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--eva-ice);
  margin-bottom: 12px;
  letter-spacing: 1px;
}

.orbit-container {
  display: flex;
  justify-content: center;
}

.fallback-orbit {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  font-size: 14px;
}

.orbit-line {
  color: var(--eva-text-dim);
  opacity: 0.3;
}

.orb-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
}

.fallback-orb .orb-circle {
  width: 200px;
  height: 200px;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, var(--eva-ice), hsla(200, 50%, 20%, 0.4));
  display: flex;
  align-items: center;
  justify-content: center;
}

.mood-text {
  font-size: 18px;
  color: white;
  font-weight: 300;
  text-transform: capitalize;
}

.orb-stats {
  display: flex;
  flex-direction: column;
  gap: 6px;
  width: 280px;
}

.stat {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}

.stat-label {
  width: 80px;
  color: var(--eva-text-dim);
  text-transform: capitalize;
}

.stat-bar {
  flex: 1;
  height: 4px;
  background: hsla(200, 20%, 20%, 0.3);
  border-radius: 2px;
  overflow: hidden;
}

.stat-fill {
  height: 100%;
  background: var(--eva-ice);
  border-radius: 2px;
  transition: width 1s ease;
}

.stat-value {
  width: 35px;
  text-align: right;
  color: var(--eva-text-dim);
}

/* Tabs */
.tab-bar {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
}

.tab-bar button {
  flex: 1;
  padding: 8px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--eva-text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.tab-bar button.active {
  background: var(--eva-glass);
  color: var(--eva-ice);
}

.tab-bar button:hover:not(.active) {
  background: hsla(200, 20%, 15%, 0.3);
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.soulscape-bottom {
  padding: 16px;
  flex-shrink: 0;
}

/* Responsive */
@media (max-width: 1000px) {
  .soulscape-grid {
    grid-template-columns: 1fr;
  }
}
</style>
