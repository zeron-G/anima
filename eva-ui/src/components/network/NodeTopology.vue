<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import * as d3 from 'd3'

interface NetworkNode {
  node_id: string
  hostname: string
  status: string
  current_load: number
}

const props = defineProps<{ nodes: NetworkNode[] }>()
const emit = defineEmits<{ (e: 'select', node: NetworkNode): void }>()
const svgRef = ref<SVGSVGElement>()

const statusColors: Record<string, string> = {
  alive: '#44cc66', suspect: '#ddaa44', dead: '#cc4444', unknown: '#666',
}

function draw() {
  if (!svgRef.value || !props.nodes.length) return
  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()

  const width = svgRef.value.clientWidth || 500
  const height = svgRef.value.clientHeight || 400

  const nodes = props.nodes.map(n => ({ ...n, x: width / 2, y: height / 2 }))

  // Links between all nodes
  const links: any[] = []
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      links.push({ source: i, target: j })
    }
  }

  const simulation = d3.forceSimulation(nodes as any)
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('link', d3.forceLink(links).distance(120))
    .on('tick', ticked)

  // Keep reference for future cleanup
  void simulation

  const g = svg.append('g')

  const link = g.selectAll('line').data(links).join('line')
    .attr('stroke', 'hsla(200, 30%, 30%, 0.2)').attr('stroke-width', 1)

  const node = g.selectAll('circle').data(nodes).join('circle')
    .attr('r', 20)
    .attr('fill', (d: any) => statusColors[d.status] || '#666')
    .attr('opacity', 0.7)
    .attr('cursor', 'pointer')
    .attr('stroke', 'var(--eva-dark)')
    .attr('stroke-width', 2)
    .on('click', (_ev: any, d: any) => emit('select', d))

  const labels = g.selectAll('text').data(nodes).join('text')
    .text((d: any) => d.hostname || d.node_id.slice(0, 8))
    .attr('fill', 'var(--eva-text)')
    .attr('font-size', '11px')
    .attr('text-anchor', 'middle')
    .attr('dy', 35)

  function ticked() {
    link.attr('x1', (d: any) => d.source.x).attr('y1', (d: any) => d.source.y)
        .attr('x2', (d: any) => d.target.x).attr('y2', (d: any) => d.target.y)
    node.attr('cx', (d: any) => d.x).attr('cy', (d: any) => d.y)
    labels.attr('x', (d: any) => d.x).attr('y', (d: any) => d.y)
  }
}

onMounted(draw)
watch(() => props.nodes, draw, { deep: true })
</script>

<template>
  <div class="topology-container">
    <svg ref="svgRef" class="topology-svg" />
  </div>
</template>

<style scoped>
.topology-container { width: 100%; height: 100%; min-height: 350px; }
.topology-svg { width: 100%; height: 100%; }
</style>
