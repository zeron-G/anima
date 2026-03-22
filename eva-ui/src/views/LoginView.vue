<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import client from '@/api/client'
import EvaAvatar from '@/components/global/EvaAvatar.vue'

const router = useRouter()
const password = ref('')
const error = ref('')
const backendReady = ref(false)
const checking = ref(true)
const authRequired = ref(false)

async function checkBackend() {
  checking.value = true
  try {
    await client.get('/v1/settings/system', { timeout: 3000 })
    backendReady.value = true
    // Try without auth first — if it works, auth is disabled
    authRequired.value = false
    checking.value = false
    // Auto-navigate to chat if no auth needed
    router.push('/')
  } catch (e: any) {
    if (e.response?.status === 401) {
      backendReady.value = true
      authRequired.value = true
      checking.value = false
    } else {
      backendReady.value = false
      checking.value = false
      // Retry in 3 seconds
      setTimeout(checkBackend, 3000)
    }
  }
}

async function login() {
  error.value = ''
  try {
    const res = await client.post('/v1/auth/login', { password: password.value })
    localStorage.setItem('eva_auth_token', res.data.token)
    router.push('/')
  } catch (e: any) {
    error.value = e.response?.data?.error || 'Login failed'
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') login()
}

onMounted(checkBackend)
</script>

<template>
  <div class="login-view">
    <!-- Background gradient accent -->
    <div class="login-bg-accent" />

    <div class="login-card glass-elevated">
      <!-- Eva Logo / Avatar -->
      <div class="login-header">
        <div class="logo-avatar-wrap">
          <EvaAvatar :size="72" />
          <div class="logo-ring" />
        </div>
        <h1 class="logo-text">Eva</h1>
        <p class="login-subtitle">ANIMA Life System</p>
      </div>

      <!-- Backend checking -->
      <div v-if="checking" class="login-status">
        <div class="spinner" />
        <p class="status-msg">Connecting to backend...</p>
      </div>

      <!-- Backend not running -->
      <div v-else-if="!backendReady" class="login-status error-state">
        <p class="status-title">Backend Offline</p>
        <p class="status-hint">Run in terminal:</p>
        <code class="status-code">python -m anima</code>
        <div class="retry-indicator">
          <div class="spinner small" />
          <span>Auto-retrying...</span>
        </div>
      </div>

      <!-- Auth form -->
      <div v-else-if="authRequired" class="login-form">
        <div class="input-group">
          <input
            v-model="password"
            type="password"
            placeholder="Password"
            class="login-input"
            @keydown="handleKeydown"
            autofocus
          />
        </div>
        <button class="login-btn" @click="login">
          <span class="btn-text">Enter</span>
          <svg class="btn-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 12h14M12 5l7 7-7 7"/>
          </svg>
        </button>
        <p v-if="error" class="login-error">{{ error }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
}

.login-bg-accent {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 50% 40% at 50% 40%, hsla(var(--eva-ice-hsl), 0.07) 0%, transparent 60%),
    radial-gradient(ellipse 40% 30% at 30% 70%, hsla(var(--eva-pink-hsl), 0.04) 0%, transparent 50%);
  pointer-events: none;
}

.login-card {
  width: 400px;
  padding: 48px 36px 40px;
  text-align: center;
  position: relative;
  z-index: 1;
}

/* ── Header / Logo ── */
.login-header {
  margin-bottom: 36px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.logo-avatar-wrap {
  position: relative;
  width: 72px;
  height: 72px;
  margin-bottom: 16px;
}

.logo-ring {
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 1px solid hsla(var(--eva-ice-hsl), 0.15);
  box-shadow: 0 0 20px hsla(var(--eva-ice-hsl), 0.1);
  pointer-events: none;
  animation: loginRingBreathe 4s ease-in-out infinite;
}

@keyframes loginRingBreathe {
  0%, 100% { box-shadow: 0 0 15px hsla(var(--eva-ice-hsl), 0.08); }
  50% { box-shadow: 0 0 30px hsla(var(--eva-ice-hsl), 0.18); }
}

.logo-text {
  font-family: 'Sora', sans-serif;
  font-size: 28px;
  font-weight: 300;
  color: var(--eva-text);
  letter-spacing: 0.15em;
  margin-bottom: 4px;
}

.login-subtitle {
  font-family: 'Sora', sans-serif;
  font-size: 11px;
  font-weight: 400;
  color: var(--eva-text-dim);
  letter-spacing: 0.2em;
  text-transform: uppercase;
}

/* ── Status ── */
.login-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--eva-text-dim);
  font-size: 14px;
}

.status-msg {
  font-size: 13px;
}

.status-title {
  font-family: 'Sora', sans-serif;
  font-size: 15px;
  font-weight: 500;
  color: var(--eva-text);
}

.status-hint {
  font-size: 12px;
}

.status-code {
  display: block;
  padding: 10px 20px;
  background: hsla(222, 25%, 8%, 0.6);
  border-radius: var(--radius-sm);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.06);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  color: var(--eva-ice);
}

.retry-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--eva-text-dim);
  opacity: 0.6;
}

.spinner {
  width: 22px;
  height: 22px;
  border: 2px solid hsla(var(--eva-ice-hsl), 0.12);
  border-top-color: var(--eva-ice);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.spinner.small {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── Auth Form ── */
.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.input-group {
  position: relative;
}

.login-input {
  width: 100%;
  padding: 14px 20px;
  background: hsla(222, 25%, 10%, 0.5);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.08);
  border-radius: var(--radius);
  color: var(--eva-text);
  font-family: 'DM Sans', sans-serif;
  font-size: 15px;
  outline: none;
  transition: border-color 0.3s ease, box-shadow 0.3s ease;
  text-align: center;
  letter-spacing: 4px;
}

.login-input:focus {
  border-color: hsla(var(--eva-ice-hsl), 0.25);
  box-shadow: 0 0 0 3px hsla(var(--eva-ice-hsl), 0.06);
}

.login-input::placeholder {
  color: var(--eva-text-dim);
  letter-spacing: 0.05em;
  opacity: 0.6;
}

.login-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px;
  background: hsla(var(--eva-ice-hsl), 0.12);
  border: 1px solid hsla(var(--eva-ice-hsl), 0.15);
  border-radius: var(--radius);
  cursor: pointer;
  transition: all 0.3s var(--ease-out-expo);
}

.login-btn:hover {
  background: hsla(var(--eva-ice-hsl), 0.18);
  border-color: hsla(var(--eva-ice-hsl), 0.25);
  box-shadow: 0 0 24px hsla(var(--eva-ice-hsl), 0.1);
}

.btn-text {
  font-family: 'Sora', sans-serif;
  font-size: 13px;
  font-weight: 500;
  color: var(--eva-ice);
  letter-spacing: 0.15em;
  text-transform: uppercase;
}

.btn-arrow {
  color: var(--eva-ice);
  transition: transform 0.25s var(--ease-out-expo);
}

.login-btn:hover .btn-arrow {
  transform: translateX(3px);
}

.login-error {
  font-size: 12px;
  color: var(--eva-pink);
}
</style>
