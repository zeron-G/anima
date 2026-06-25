import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'

// Dev-server proxy target. Defaults to a local backend; point at any backend
// via the VITE_DEV_PROXY env var (e.g. VITE_DEV_PROXY=http://192.168.1.10:8420).
const proxyTarget = process.env.VITE_DEV_PROXY || 'http://localhost:8420'
const wsTarget = proxyTarget.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': proxyTarget,
      '/ws': {
        target: wsTarget,
        ws: true,
      },
      '/api': proxyTarget,
      '/static': proxyTarget,
      '/desktop': proxyTarget,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
