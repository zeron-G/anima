<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useEmotionStore } from '@/stores/emotionStore'
import { usePersonaStore } from '@/stores/personaStore'
import SoulscapeAvatar from '@/components/soulscape/SoulscapeAvatar.vue'
import PersonaPetals from '@/components/soulscape/PersonaPetals.vue'
import PersonalityEditor from '@/components/soulscape/PersonalityEditor.vue'
import GoldenReplyManager from '@/components/soulscape/GoldenReplyManager.vue'
import * as api from '@/api/soulscape'

const emotion = useEmotionStore()
const persona = usePersonaStore()

const activeTab = ref<'personality' | 'style' | 'golden'>('personality')
const loading = ref(true)

const petalData = computed<Record<string, number>>(() => {
  const result: Record<string, number> = {}
  for (const [key, value] of Object.entries(persona.state)) {
    if (typeof value === 'number') result[key] = value
  }
  return result
})

const emotionDims = ['engagement', 'confidence', 'curiosity', 'concern'] as const

function barColor(dim: string): string {
  return { engagement: '#00e5c8', confidence: '#f0c860', curiosity: '#8040ff', concern: '#e040a0' }[dim] || '#00e5c8'
}

async function loadData() {
  loading.value = true
  try {
    const [personaRes, personalityRes, relationshipRes, goldenRes, styleRes] = await Promise.all([
      api.getPersona(), api.getPersonality(), api.getRelationship(), api.getGoldenReplies(), api.getStyleRules(),
    ])
    persona.updateState(personaRes.data)
    persona.personality = personalityRes.data.content || ''
    persona.relationship = relationshipRes.data.content || ''
    persona.goldenReplies = goldenRes.data.replies || []
    persona.styleRules = styleRes.data.content || ''
  } catch (e) { console.error('Soulscape load failed:', e) }
  finally { loading.value = false }
}

onMounted(loadData)

async function handlePetalUpdate(key: string, value: number) {
  persona.state[key] = value
  try { await api.updatePersona({ [key]: value }) } catch {}
}
async function savePersonality(content: string) {
  try { await api.updatePersonality(content); persona.personality = content } catch {}
}
async function saveRelationship(content: string) {
  try { await api.updateRelationship(content); persona.relationship = content } catch {}
}
async function saveStyleRules(content: string) {
  try { await api.updateStyleRules(content); persona.styleRules = content } catch {}
}
async function deleteGolden(id: string) {
  try { await api.deleteGoldenReply(id); persona.goldenReplies = persona.goldenReplies.filter(r => r.id !== id) } catch {}
}
</script>

<template>
  <div class="soul-scroll">

    <!-- ═══ SECTION 1: Avatar (hero, near full viewport) ═══ -->
    <section class="soul-section avatar-section">
      <div class="section-inner">
        <div class="section-label">Consciousness</div>
        <h1 class="page-title">Soulscape</h1>
        <p class="section-desc">Interact with Eva's avatar. Drag to rotate, scroll to zoom. Switch between 3D and 2D modes.</p>
      </div>
      <div class="avatar-wrap">
        <SoulscapeAvatar />
      </div>
    </section>

    <!-- ═══ SECTION 2: Emotion + Persona (side by side) ═══ -->
    <section class="soul-section stats-section">
      <div class="stats-grid">

        <!-- Left: Emotion bars -->
        <div class="emotion-card glass">
          <h3 class="card-heading">Emotional State</h3>
          <div class="emotion-list">
            <div v-for="dim in emotionDims" :key="dim" class="emo-row">
              <span class="emo-label">{{ dim }}</span>
              <div class="emo-track">
                <div class="emo-fill" :style="{ width: `${((emotion.current as any)[dim] || 0) * 100}%`, background: barColor(dim) }" />
              </div>
              <span class="emo-val" :style="{ color: barColor(dim) }">{{ (((emotion.current as any)[dim] || 0) * 100).toFixed(0) }}%</span>
            </div>
          </div>
          <div class="emo-meta">
            <div class="meta-chip">
              <span class="meta-k">Mood</span>
              <span class="meta-v">{{ emotion.current.mood_label }}</span>
            </div>
            <div class="meta-chip">
              <span class="meta-k">Dominant</span>
              <span class="meta-v">{{ emotion.dominant }}</span>
            </div>
            <div class="meta-chip">
              <span class="meta-k">Intensity</span>
              <span class="meta-v">{{ ((emotion.current.intensity || 0) * 100).toFixed(0) }}%</span>
            </div>
          </div>
        </div>

        <!-- Right: Persona Petals -->
        <div class="petals-card glass">
          <h3 class="card-heading">Persona Dimensions</h3>
          <p class="card-hint">Drag petal tips to adjust values</p>
          <div class="petals-center">
            <PersonaPetals :data="petalData" @update="handlePetalUpdate" />
          </div>
        </div>

      </div>
    </section>

    <!-- ═══ SECTION 3: Editors ═══ -->
    <section class="soul-section editor-section">
      <div class="section-inner">
        <div class="section-label">Identity</div>
        <h2 class="section-heading">Personality & Rules</h2>
        <p class="section-desc">Edit Eva's personality definition, communication style, and curated golden replies.</p>
      </div>

      <div class="editor-tabs">
        <button :class="{ active: activeTab === 'personality' }" @click="activeTab = 'personality'">Personality</button>
        <button :class="{ active: activeTab === 'style' }" @click="activeTab = 'style'">Style Rules</button>
        <button :class="{ active: activeTab === 'golden' }" @click="activeTab = 'golden'">Golden Replies</button>
      </div>

      <div class="editor-content">
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
    </section>

    <!-- Bottom spacer -->
    <div class="bottom-spacer" />

  </div>
</template>

<style scoped>
.soul-scroll {
  height: 100%;
  overflow-y: auto;
  scroll-behavior: smooth;
}

/* ═══ Sections ═══ */
.soul-section {
  padding: 0 var(--space-2xl);
}

.section-inner {
  max-width: 600px;
  margin-bottom: var(--space-xl);
}

.section-desc {
  font-size: 14px;
  font-weight: 300;
  color: var(--text-secondary);
  line-height: 1.7;
  margin-top: 6px;
}

.section-heading {
  font-family: var(--font-display);
  font-size: 28px;
  font-weight: 300;
  color: var(--text);
  letter-spacing: -0.3px;
}

/* ═══ Section 1: Avatar ═══ */
.avatar-section {
  padding-top: var(--space-2xl);
  padding-bottom: var(--space-3xl);
}

.avatar-wrap {
  height: 560px;
  border-radius: var(--radius-lg);
  overflow: hidden;
}

/* ═══ Section 2: Stats ═══ */
.stats-section {
  padding-bottom: var(--space-3xl);
}

.stats-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-lg);
}

.emotion-card,
.petals-card {
  padding: var(--space-xl);
}

.card-heading {
  font-family: var(--font-heading);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 3px;
  text-transform: uppercase;
  color: var(--text-secondary);
  margin-bottom: var(--space-xl);
}

.card-hint {
  font-size: 12px;
  color: var(--text-dim);
  margin-top: -12px;
  margin-bottom: var(--space-md);
}

/* Emotion bars */
.emotion-list {
  display: flex;
  flex-direction: column;
  gap: 18px;
  margin-bottom: var(--space-xl);
}

.emo-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.emo-label {
  width: 90px;
  font-family: var(--font-heading);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--text-dim);
}

.emo-track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.03);
  overflow: hidden;
}

.emo-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 1s ease;
}

.emo-val {
  width: 40px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 500;
}

/* Emotion meta chips */
.emo-meta {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.meta-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 100px;
  background: rgba(var(--accent-rgb), 0.04);
  border: 1px solid var(--border);
}

.meta-k {
  font-size: 10px;
  font-weight: 400;
  letter-spacing: 1px;
  text-transform: uppercase;
  color: var(--text-dim);
}

.meta-v {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  text-transform: capitalize;
}

/* Petals */
.petals-center {
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ═══ Section 3: Editors ═══ */
.editor-section {
  padding-bottom: var(--space-2xl);
}

.editor-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: var(--space-lg);
  max-width: 480px;
}

.editor-tabs button {
  flex: 1;
  padding: 11px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-dim);
  font-family: var(--font-heading);
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0.5px;
  cursor: pointer;
  transition: all 0.2s;
}

.editor-tabs button.active {
  background: rgba(var(--accent-rgb), 0.08);
  border-color: rgba(var(--accent-rgb), 0.15);
  color: var(--accent);
}

.editor-tabs button:hover:not(.active) {
  border-color: var(--border-hover);
}

.editor-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-lg);
}

.bottom-spacer {
  height: var(--space-3xl);
}

/* ═══ Responsive ═══ */
@media (max-width: 900px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
  .avatar-wrap {
    height: 400px;
  }
}
</style>
