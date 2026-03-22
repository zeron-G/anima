<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import * as d3 from 'd3'

interface MemoryNode {
  id: string
  content: string
  type: string
  importance: number
  created_at: number
}

const props = defineProps<{
  memories: MemoryNode[]
}>()

const emit = defineEmits<{
  (e: 'select', memory: MemoryNode): void
}>()

const svgRef = ref<SVGSVGElement>()

const typeColors: Record<string, string> = {
  chat: '#66bbee',
  self_thinking: '#aa77dd',
  system: '#8899aa',
  task: '#ddaa44',
  document: '#44cc66',
}

function draw() {
  if (!svgRef.value || !props.memories.length) return
  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()

  const width = svgRef.value.clientWidth || 600
  const height = svgRef.value.clientHeight || 400

  const nodes = props.memories.map(m => ({
    ...m,
    x: Math.random() * width,
    y: Math.random() * height,
    r: 3 + m.importance * 8,
  }))

  const simulation = d3.forceSimulation(nodes as any)
    .force('charge', d3.forceManyBody().strength(-15))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius((d: any) => d.r + 2))
    .on('tick', ticked)

  const g = svg.append('g')

  const circles = g.selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', d => d.r)
    .attr('fill', d => typeColors[d.type] || '#666')
    .attr('opacity', d => 0.3 + d.importance * 0.7)
    .attr('cursor', 'pointer')
    .on('click', (_event, d) => emit('select', d as any))

  circles.append('title').text(d => d.content.slice(0, 80))

  function ticked() {
    circles.attr('cx', (d: any) => d.x).attr('cy', (d: any) => d.y)
  }

  // Cleanup
  return () => simulation.stop()
}

onMounted(draw)
watch(() => props.memories, draw, { deep: true })
</script>

<template>
  <div class="starfield-container">
    <svg ref="svgRef" class="starfield-svg" />
  </div>
</template>

<style scoped>
.starfield-container { width: 100%; height: 100%; min-height: 400px; }
.starfield-svg { width: 100%; height: 100%; background: transparent; }
</style>
