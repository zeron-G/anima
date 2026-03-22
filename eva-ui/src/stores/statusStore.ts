import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useStatusStore = defineStore('status', () => {
  const uptime = ref(0)
  const queueDepth = ref(0)
  const idleScore = ref(0)
  const activeTier = ref('')
  const heartbeatStage = ref('')
  const evolution = ref<any>(null)
  const activities = ref<any[]>([])

  function update(data: any) {
    if (data.uptime_s !== undefined) uptime.value = data.uptime_s
    if (data.queue_depth !== undefined) queueDepth.value = data.queue_depth
    if (data.idle_score !== undefined) idleScore.value = data.idle_score
    if (data.active_tier) activeTier.value = data.active_tier
    if (data.heartbeat_stage) heartbeatStage.value = data.heartbeat_stage
  }

  function updateEvolution(data: any) {
    evolution.value = data
  }

  function addActivity(data: any) {
    activities.value.push(data)
    if (activities.value.length > 50) activities.value.shift()
  }

  function updateNode(_data: any) {
    // Will be implemented in NetworkView
  }

  return { uptime, queueDepth, idleScore, activeTier, heartbeatStage,
           evolution, activities, update, updateEvolution, addActivity, updateNode }
})
