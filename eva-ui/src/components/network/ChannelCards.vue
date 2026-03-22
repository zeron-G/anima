<script setup lang="ts">
const props = defineProps<{
  channels: Array<{ name: string; connected: boolean; type: string }>
}>()

const icons: Record<string, string> = {
  DiscordChannel: '\uD83D\uDCAC', TelegramChannel: '\u2708\uFE0F', WebhookChannel: '\uD83D\uDD17',
}
</script>

<template>
  <div class="channel-cards">
    <div v-for="ch in channels" :key="ch.name" class="channel-card glass">
      <span class="ch-icon">{{ icons[ch.type] || '\uD83D\uDCE1' }}</span>
      <span class="ch-name">{{ ch.name }}</span>
      <span class="ch-status" :class="{ online: ch.connected }">
        {{ ch.connected ? '在线' : '离线' }}
      </span>
    </div>
    <div v-if="channels.length === 0" class="empty">无活跃渠道</div>
  </div>
</template>

<style scoped>
.channel-cards { display: flex; gap: 10px; flex-wrap: wrap; }
.channel-card { display: flex; align-items: center; gap: 8px; padding: 10px 16px; min-width: 150px; }
.ch-icon { font-size: 20px; }
.ch-name { font-size: 14px; color: var(--eva-text); flex: 1; }
.ch-status { font-size: 11px; padding: 2px 8px; border-radius: 8px; }
.ch-status.online { background: hsla(140, 40%, 20%, 0.3); color: hsl(140, 50%, 60%); }
.ch-status:not(.online) { background: hsla(0, 30%, 20%, 0.3); color: hsl(0, 40%, 55%); }
.empty { color: var(--eva-text-dim); font-size: 13px; }
</style>
