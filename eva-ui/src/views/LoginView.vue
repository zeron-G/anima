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
    <div class="login-card glass">
      <!-- Eva Logo -->
      <div class="login-header">
        <div class="eva-logo">
          <div class="logo-orb breathing" />
          <span class="logo-text">Eva</span>
        </div>
        <p class="login-subtitle">ANIMA — AI Life System</p>
      </div>

      <!-- Backend checking -->
      <div v-if="checking" class="login-status">
        <div class="spinner" />
        <p>连接后端...</p>
      </div>

      <!-- Backend not running -->
      <div v-else-if="!backendReady" class="login-status error-state">
        <p class="status-title">Eva 后端未运行</p>
        <p class="status-hint">请在终端执行：</p>
        <code class="status-code">python -m anima</code>
        <div class="retry-indicator">
          <div class="spinner small" />
          <span>自动重试中...</span>
        </div>
      </div>

      <!-- Auth form -->
      <div v-else-if="authRequired" class="login-form">
        <input
          v-model="password"
          type="password"
          placeholder="密码"
          class="login-input"
          @keydown="handleKeydown"
          autofocus
        />
        <button class="login-btn" @click="login">
          进入 Eva 的世界
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
  background: radial-gradient(ellipse at center, hsla(200, 60%, 12%, 1) 0%, hsla(220, 25%, 6%, 1) 100%);
}

.login-card {
  width: 380px;
  padding: 40px 32px;
  text-align: center;
}

.login-header {
  margin-bottom: 32px;
}

.eva-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 8px;
}

.logo-orb {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 30%, var(--eva-ice), hsla(200, 60%, 30%, 0.6));
}

.logo-text {
  font-size: 32px;
  font-weight: 200;
  color: var(--eva-ice);
  letter-spacing: 4px;
}

.login-subtitle {
  font-size: 13px;
  color: var(--eva-text-dim);
  letter-spacing: 2px;
}

.login-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--eva-text-dim);
  font-size: 14px;
}

.status-title {
  font-size: 16px;
  color: var(--eva-text);
}

.status-hint {
  font-size: 13px;
}

.status-code {
  display: block;
  padding: 8px 16px;
  background: hsla(220, 20%, 10%, 0.5);
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  color: var(--eva-ice);
}

.retry-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--eva-text-dim);
  opacity: 0.6;
}

.spinner {
  width: 24px;
  height: 24px;
  border: 2px solid hsla(200, 60%, 50%, 0.2);
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

.login-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.login-input {
  width: 100%;
  padding: 12px 16px;
  background: hsla(220, 20%, 12%, 0.5);
  border: 1px solid hsla(200, 30%, 30%, 0.2);
  border-radius: 10px;
  color: var(--eva-text);
  font-size: 15px;
  outline: none;
  transition: border-color 0.3s;
  text-align: center;
  letter-spacing: 4px;
}

.login-input:focus {
  border-color: hsla(200, 60%, 50%, 0.4);
}

.login-btn {
  padding: 12px;
  background: hsla(200, 50%, 35%, 0.5);
  border: 1px solid hsla(200, 50%, 50%, 0.2);
  border-radius: 10px;
  color: var(--eva-ice);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.3s;
  letter-spacing: 2px;
}

.login-btn:hover {
  background: hsla(200, 50%, 40%, 0.6);
  box-shadow: 0 0 20px hsla(200, 60%, 50%, 0.15);
}

.login-error {
  font-size: 13px;
  color: hsl(0, 60%, 60%);
}
</style>
