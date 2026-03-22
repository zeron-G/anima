<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import client from '@/api/client'

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
    authRequired.value = false
    checking.value = false
    router.push('/')
  } catch (e: any) {
    if (e.response?.status === 401) {
      backendReady.value = true
      authRequired.value = true
      checking.value = false
    } else {
      backendReady.value = false
      checking.value = false
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
    error.value = e.response?.data?.error || 'Authentication failed'
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter') login()
}

onMounted(checkBackend)
</script>

<template>
  <div class="login-view">
    <!-- Background glow -->
    <div class="login-bg" />

    <div class="login-card glass-elevated">
      <!-- Logo -->
      <div class="login-header">
        <div class="logo-circle">
          <div class="logo-inner" />
          <div class="logo-ring" />
        </div>
        <h1 class="logo-text">ANIMA</h1>
        <p class="login-subtitle">Intelligent Life System</p>
      </div>

      <!-- Checking backend -->
      <div v-if="checking" class="login-status">
        <div class="spinner" />
        <p class="status-msg">Connecting to backend...</p>
      </div>

      <!-- Backend offline -->
      <div v-else-if="!backendReady" class="login-status">
        <p class="status-title">Backend Offline</p>
        <p class="status-hint">Start the backend process:</p>
        <code class="status-code">python -m anima</code>
        <div class="retry-row">
          <div class="spinner small" />
          <span>Auto-retrying...</span>
        </div>
      </div>

      <!-- Auth form -->
      <div v-else-if="authRequired" class="login-form">
        <input
          v-model="password"
          type="password"
          placeholder="Password"
          class="login-input"
          @keydown="handleKeydown"
          autofocus
        />
        <button class="login-btn btn-primary" @click="login">
          <span>Enter</span>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
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

.login-bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 40% 35% at 50% 40%, rgba(var(--accent-rgb), 0.06) 0%, transparent 60%),
    radial-gradient(ellipse 30% 25% at 30% 70%, rgba(var(--magenta-rgb), 0.03) 0%, transparent 50%);
  pointer-events: none;
}

.login-card {
  width: 420px;
  padding: 56px 44px 48px;
  text-align: center;
  position: relative;
  z-index: 1;
}

.login-header {
  margin-bottom: 44px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.logo-circle {
  position: relative;
  width: 64px;
  height: 64px;
  margin-bottom: 20px;
}

.logo-inner {
  position: absolute;
  inset: 20px;
  border-radius: 50%;
  background: radial-gradient(circle at 35% 35%, var(--accent), rgba(0, 184, 160, 0.6));
  box-shadow: 0 0 24px rgba(var(--accent-rgb), 0.3);
}

.logo-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid rgba(var(--accent-rgb), 0.2);
  animation: loginRing 4s ease-in-out infinite;
}

@keyframes loginRing {
  0%, 100% { box-shadow: 0 0 12px rgba(var(--accent-rgb), 0.08); }
  50% { box-shadow: 0 0 28px rgba(var(--accent-rgb), 0.2); }
}

.logo-text {
  font-family: var(--font-heading);
  font-size: 24px;
  font-weight: 300;
  color: var(--text);
  letter-spacing: 6px;
  margin-bottom: 6px;
}

.login-subtitle {
  font-family: var(--font-heading);
  font-size: 10px;
  font-weight: 400;
  color: var(--text-dim);
  letter-spacing: 4px;
  text-transform: uppercase;
}

/* Status */
.login-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
}

.status-msg {
  font-size: 13px;
  color: var(--text-secondary);
}

.status-title {
  font-family: var(--font-heading);
  font-size: 15px;
  font-weight: 500;
  color: var(--text);
}

.status-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.status-code {
  display: block;
  padding: 10px 24px;
  background: rgba(5, 5, 8, 0.6);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--accent);
}

.retry-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-dim);
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(var(--accent-rgb), 0.12);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.spinner.small {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* Form */
.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.login-input {
  width: 100%;
  padding: 14px 20px;
  background: rgba(10, 10, 20, 0.5);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 15px;
  outline: none;
  transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
  text-align: center;
  letter-spacing: 4px;
}

.login-input:focus {
  border-color: rgba(var(--accent-rgb), 0.25);
  box-shadow: 0 0 0 3px rgba(var(--accent-rgb), 0.06);
}

.login-input::placeholder {
  color: var(--text-dim);
  letter-spacing: 0.05em;
}

.login-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px;
  width: 100%;
}

.login-error {
  font-size: 12px;
  color: var(--error);
}
</style>
