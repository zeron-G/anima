<script setup lang="ts">
import { ref, watch } from 'vue'

const props = defineProps<{
  config: any
}>()

const emit = defineEmits<{
  (e: 'update', key: string, value: any): void
}>()

// --- State ---
const provider = ref('anthropic')
const apiEndpoint = ref('')
const apiKey = ref('')
const showApiKey = ref(false)

const tier1Model = ref('')
const tier1MaxTokens = ref(16384)
const tier1Custom = ref(false)
const tier1CustomModel = ref('')

const tier2Model = ref('')
const tier2MaxTokens = ref(16384)
const tier2Custom = ref(false)
const tier2CustomModel = ref('')

const dailyBudget = ref(0)
const budgetUnlimited = ref(true)

const saveStatus = ref<string>('')

// Presets
const modelPresets: Record<string, string[]> = {
  anthropic: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001'],
  openai: ['gpt-4o', 'gpt-4-turbo', 'gpt-4o-mini', 'o1', 'o3-mini'],
  codex: ['codex/claude-opus-4-6', 'codex/claude-sonnet-4-6'],
  local: ['local/llama3', 'local/mistral', 'local/deepseek-r1'],
}

const providerLabels: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  codex: 'Codex OAuth',
  local: 'Local / Ollama',
}

// Initialize from config
watch(() => props.config, (cfg) => {
  if (!cfg?.llm) return
  tier1Model.value = cfg.llm.tier1?.model || ''
  tier1MaxTokens.value = cfg.llm.tier1?.max_tokens || 16384
  tier2Model.value = cfg.llm.tier2?.model || ''
  tier2MaxTokens.value = cfg.llm.tier2?.max_tokens || 16384
  dailyBudget.value = cfg.llm.budget?.daily_limit_usd || 0
  budgetUnlimited.value = dailyBudget.value === 0

  // Detect provider from model name
  if (tier1Model.value.startsWith('codex/')) provider.value = 'codex'
  else if (tier1Model.value.startsWith('openai/')) provider.value = 'openai'
  else if (tier1Model.value.startsWith('local/')) provider.value = 'local'
  else provider.value = 'anthropic'

  // Check if model is in presets
  const presets = modelPresets[provider.value] || []
  tier1Custom.value = !presets.includes(tier1Model.value)
  tier2Custom.value = !presets.includes(tier2Model.value)
  if (tier1Custom.value) tier1CustomModel.value = tier1Model.value
  if (tier2Custom.value) tier2CustomModel.value = tier2Model.value
}, { immediate: true, deep: true })

function getEffectiveModel(tier: 1 | 2): string {
  if (tier === 1) return tier1Custom.value ? tier1CustomModel.value : tier1Model.value
  return tier2Custom.value ? tier2CustomModel.value : tier2Model.value
}

async function applyChanges() {
  saveStatus.value = 'saving'
  try {
    const m1 = getEffectiveModel(1)
    const m2 = getEffectiveModel(2)
    if (m1) emit('update', 'llm.tier1.model', m1)
    if (m2) emit('update', 'llm.tier2.model', m2)
    if (tier1MaxTokens.value) emit('update', 'llm.tier1.max_tokens', tier1MaxTokens.value)
    if (tier2MaxTokens.value) emit('update', 'llm.tier2.max_tokens', tier2MaxTokens.value)

    const budget = budgetUnlimited.value ? 0 : dailyBudget.value
    emit('update', 'llm.budget.daily_limit_usd', budget)

    if (apiEndpoint.value) emit('update', 'llm.api_base_url', apiEndpoint.value)
    if (apiKey.value) emit('update', 'llm.api_key', apiKey.value)

    saveStatus.value = 'saved'
    setTimeout(() => { saveStatus.value = '' }, 2000)
  } catch {
    saveStatus.value = 'error'
    setTimeout(() => { saveStatus.value = '' }, 3000)
  }
}
</script>

<template>
  <div class="config-card glass">
    <h3 class="card-title">LLM Configuration</h3>

    <!-- Provider -->
    <div class="field-group">
      <label class="form-label">Provider</label>
      <div class="provider-grid">
        <button
          v-for="(label, key) in providerLabels"
          :key="key"
          class="provider-btn"
          :class="{ active: provider === key }"
          @click="provider = key as string"
        >
          {{ label }}
        </button>
      </div>
    </div>

    <!-- API Endpoint -->
    <div class="field-group">
      <label class="form-label">API Endpoint</label>
      <input
        v-model="apiEndpoint"
        class="input-field"
        :placeholder="provider === 'openai' ? 'https://api.openai.com/v1' : provider === 'local' ? 'http://localhost:11434' : 'https://api.anthropic.com'"
      />
    </div>

    <!-- API Key -->
    <div class="field-group">
      <label class="form-label">API Key</label>
      <div class="key-input-row">
        <input
          v-model="apiKey"
          :type="showApiKey ? 'text' : 'password'"
          class="input-field key-input"
          placeholder="sk-..."
        />
        <button class="toggle-btn" @click="showApiKey = !showApiKey">
          <svg v-if="!showApiKey" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
          </svg>
          <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
            <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>
          </svg>
        </button>
      </div>
    </div>

    <div class="divider" />

    <!-- Tier 1 -->
    <div class="tier-section">
      <div class="tier-header">
        <span class="tier-badge">Tier 1</span>
        <span class="tier-desc">User conversations</span>
      </div>
      <div class="tier-fields">
        <div class="field-group">
          <label class="form-label">Model</label>
          <div v-if="!tier1Custom" class="model-select-row">
            <select v-model="tier1Model" class="input-field select-field">
              <option v-for="m in modelPresets[provider]" :key="m" :value="m">{{ m }}</option>
            </select>
            <button class="toggle-btn small" @click="tier1Custom = true; tier1CustomModel = tier1Model" title="Custom model">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
              </svg>
            </button>
          </div>
          <div v-else class="model-select-row">
            <input v-model="tier1CustomModel" class="input-field" placeholder="Enter model identifier..." />
            <button class="toggle-btn small" @click="tier1Custom = false" title="Use presets">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                <polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>
              </svg>
            </button>
          </div>
        </div>
        <div class="field-group half">
          <label class="form-label">Max Tokens</label>
          <input v-model.number="tier1MaxTokens" type="number" class="input-field" />
        </div>
      </div>
    </div>

    <!-- Tier 2 -->
    <div class="tier-section">
      <div class="tier-header">
        <span class="tier-badge secondary">Tier 2</span>
        <span class="tier-desc">Internal thinking</span>
      </div>
      <div class="tier-fields">
        <div class="field-group">
          <label class="form-label">Model</label>
          <div v-if="!tier2Custom" class="model-select-row">
            <select v-model="tier2Model" class="input-field select-field">
              <option v-for="m in modelPresets[provider]" :key="m" :value="m">{{ m }}</option>
            </select>
            <button class="toggle-btn small" @click="tier2Custom = true; tier2CustomModel = tier2Model" title="Custom model">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
              </svg>
            </button>
          </div>
          <div v-else class="model-select-row">
            <input v-model="tier2CustomModel" class="input-field" placeholder="Enter model identifier..." />
            <button class="toggle-btn small" @click="tier2Custom = false" title="Use presets">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                <polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>
              </svg>
            </button>
          </div>
        </div>
        <div class="field-group half">
          <label class="form-label">Max Tokens</label>
          <input v-model.number="tier2MaxTokens" type="number" class="input-field" />
        </div>
      </div>
    </div>

    <div class="divider" />

    <!-- Budget -->
    <div class="field-group">
      <label class="form-label">Daily Budget</label>
      <div class="budget-row">
        <div class="budget-input-wrap" :class="{ disabled: budgetUnlimited }">
          <span class="budget-prefix">$</span>
          <input
            v-model.number="dailyBudget"
            type="number"
            step="0.5"
            class="input-field budget-input"
            :disabled="budgetUnlimited"
          />
        </div>
        <label class="toggle-label">
          <input type="checkbox" v-model="budgetUnlimited" class="toggle-checkbox" />
          <span class="toggle-text">Unlimited</span>
        </label>
      </div>
    </div>

    <!-- Apply -->
    <button class="apply-btn btn-primary" @click="applyChanges">
      <span v-if="saveStatus === 'saving'">Saving...</span>
      <span v-else-if="saveStatus === 'saved'">Changes Applied</span>
      <span v-else-if="saveStatus === 'error'">Failed — Retry</span>
      <span v-else>Apply Changes</span>
    </button>
  </div>
</template>

<style scoped>
.config-card {
  padding: var(--space-lg);
}

.field-group {
  margin-bottom: var(--space-md);
}

.field-group.half {
  max-width: 180px;
}

/* Provider grid */
.provider-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
}

.provider-btn {
  padding: 8px 4px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  font-family: var(--font-heading);
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 0.5px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.provider-btn:hover {
  border-color: var(--border-hover);
  background: rgba(var(--accent-rgb), 0.03);
}

.provider-btn.active {
  border-color: rgba(var(--accent-rgb), 0.3);
  background: rgba(var(--accent-rgb), 0.08);
  color: var(--accent);
}

/* Key input */
.key-input-row {
  display: flex;
  gap: 6px;
}

.key-input {
  flex: 1;
}

.toggle-btn {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--transition-fast);
}

.toggle-btn:hover {
  border-color: var(--border-hover);
  color: var(--accent);
}

.toggle-btn.small {
  width: 36px;
  height: 36px;
}

/* Tier sections */
.tier-section {
  margin-bottom: var(--space-md);
}

.tier-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: var(--space-sm);
}

.tier-badge {
  padding: 3px 10px;
  border-radius: 100px;
  font-family: var(--font-heading);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 1px;
  text-transform: uppercase;
  background: rgba(var(--accent-rgb), 0.1);
  color: var(--accent);
  border: 1px solid rgba(var(--accent-rgb), 0.15);
}

.tier-badge.secondary {
  background: rgba(var(--violet-rgb), 0.1);
  color: var(--violet);
  border-color: rgba(var(--violet-rgb), 0.15);
}

.tier-desc {
  font-size: 12px;
  color: var(--text-dim);
}

.tier-fields {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding-left: var(--space-md);
  border-left: 1px solid var(--border);
}

/* Model select row */
.model-select-row {
  display: flex;
  gap: 6px;
}

.select-field {
  flex: 1;
  appearance: none;
  cursor: pointer;
  background-image: url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23666' stroke-width='1.5' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  padding-right: 32px;
}

/* Budget */
.budget-row {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.budget-input-wrap {
  display: flex;
  align-items: center;
  position: relative;
  flex: 0 0 140px;
}

.budget-input-wrap.disabled {
  opacity: 0.35;
}

.budget-prefix {
  position: absolute;
  left: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 13px;
  z-index: 1;
  pointer-events: none;
}

.budget-input {
  padding-left: 28px;
}

.toggle-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.toggle-checkbox {
  appearance: none;
  width: 18px;
  height: 18px;
  border: 1px solid var(--border-hover);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  position: relative;
  transition: all var(--transition-fast);
}

.toggle-checkbox:checked {
  background: rgba(var(--accent-rgb), 0.2);
  border-color: rgba(var(--accent-rgb), 0.4);
}

.toggle-checkbox:checked::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 6px;
  width: 4px;
  height: 8px;
  border: solid var(--accent);
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}

.toggle-text {
  font-size: 13px;
  color: var(--text-secondary);
}

/* Apply button */
.apply-btn {
  width: 100%;
  margin-top: var(--space-lg);
  padding: 12px;
  font-size: 13px;
}
</style>
