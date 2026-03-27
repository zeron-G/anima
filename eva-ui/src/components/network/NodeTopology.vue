<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as d3 from 'd3'

interface NetworkNode {
  node_id: string
  hostname: string
  agent_name?: string
  status: string
  embodiment?: string
  current_load: number
}

const props = defineProps<{
  nodes: NetworkNode[]
  selectedNodeId?: string
}>()

const emit = defineEmits<{ (e: 'select', node: NetworkNode): void }>()
const svgRef = ref<SVGSVGElement>()
let simulation: d3.Simulation<NetworkNode & d3.SimulationNodeDatum, undefined> | null = null

const statusColors: Record<string, string> = {
  alive: '#44cc66',
  suspect: '#ddaa44',
  dead: '#cc4444',
  unknown: '#667085',
}

function labelFor(node: NetworkNode) {
  return node.hostname || node.agent_name || node.node_id.slice(0, 8)
}

function radiusFor(node: NetworkNode) {
  return node.embodiment === 'robot_dog' ? 24 : 19
}

function draw() {
  if (!svgRef.value) return

  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()
  simulation?.stop()
  simulation = null

  if (!props.nodes.length) return

  const width = svgRef.value.clientWidth || 500
  const height = svgRef.value.clientHeight || 340
  const nodes = props.nodes.map((node) => ({ ...node, x: width / 2, y: height / 2 }))
  const links = nodes.slice(1).map((node, index) => ({
    source: nodes[0]?.node_id ?? node.node_id,
    target: node.node_id,
    weight: index + 1,
  }))

  simulation = d3.forceSimulation(nodes as Array<NetworkNode & d3.SimulationNodeDatum>)
    .force('charge', d3.forceManyBody().strength(-330))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide<NetworkNode & d3.SimulationNodeDatum>().radius((node) => radiusFor(node) + 26))
    .force('link', d3.forceLink(links).id((node: any) => node.node_id).distance(120).strength(0.3))
    .force('y', d3.forceY(height / 2).strength(0.02))
    .on('tick', ticked)

  const link = svg.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', 'rgba(120, 160, 180, 0.18)')
    .attr('stroke-width', 1)

  const halo = svg.append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', (node) => radiusFor(node) + 10)
    .attr('fill', 'transparent')
    .attr('stroke', (node) => node.node_id === props.selectedNodeId ? 'rgba(0, 229, 200, 0.32)' : 'rgba(255, 255, 255, 0.05)')
    .attr('stroke-width', (node) => node.node_id === props.selectedNodeId ? 2.4 : 1)

  const node = svg.append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('r', (d) => radiusFor(d))
    .attr('fill', (d) => statusColors[d.status] || '#667085')
    .attr('opacity', (d) => d.node_id === props.selectedNodeId ? 0.95 : 0.74)
    .attr('cursor', 'pointer')
    .attr('stroke', (d) => d.embodiment === 'robot_dog' ? 'rgba(255, 228, 130, 0.9)' : 'rgba(8, 10, 18, 0.92)')
    .attr('stroke-width', (d) => d.embodiment === 'robot_dog' ? 2.8 : 2)
    .on('click', (_event, d) => emit('select', d))

  const labels = svg.append('g')
    .selectAll('text')
    .data(nodes)
    .join('text')
    .text((d) => labelFor(d))
    .attr('fill', 'rgba(230, 242, 248, 0.88)')
    .attr('font-size', '11px')
    .attr('font-family', 'var(--font-mono)')
    .attr('text-anchor', 'middle')
    .attr('dy', 42)

  function ticked() {
    link
      .attr('x1', (d: any) => d.source.x)
      .attr('y1', (d: any) => d.source.y)
      .attr('x2', (d: any) => d.target.x)
      .attr('y2', (d: any) => d.target.y)

    halo
      .attr('cx', (d: any) => d.x)
      .attr('cy', (d: any) => d.y)

    node
      .attr('cx', (d: any) => d.x)
      .attr('cy', (d: any) => d.y)

    labels
      .attr('x', (d: any) => d.x)
      .attr('y', (d: any) => d.y)
  }
}

onMounted(draw)
onBeforeUnmount(() => simulation?.stop())
watch(() => [props.nodes, props.selectedNodeId], draw, { deep: true })
</script>

<template>
  <div class="topology-container">
    <svg ref="svgRef" class="topology-svg" />
  </div>
</template>

<style scoped>
.topology-container {
  width: 100%;
  height: 100%;
  min-height: 320px;
}

.topology-svg {
  width: 100%;
  height: 100%;
}
</style>
