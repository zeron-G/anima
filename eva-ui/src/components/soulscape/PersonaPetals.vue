<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import * as d3 from 'd3'

const props = defineProps<{
  data: Record<string, number>
}>()

const emit = defineEmits<{
  (e: 'update', key: string, value: number): void
}>()

const svgRef = ref<SVGSVGElement>()

const dimensions = computed(() => {
  const keys = ['warmth', 'assertiveness', 'playfulness', 'formality', 'curiosity', 'independence']
  return keys.filter(k => typeof props.data[k] === 'number').map(k => ({
    key: k,
    value: props.data[k] as number,
  }))
})

const colors: Record<string, string> = {
  warmth: '#e040a0',
  assertiveness: '#00e5c8',
  playfulness: '#f0c860',
  formality: '#8899bb',
  curiosity: '#8040ff',
  independence: '#00b8a0',
}

const labels: Record<string, string> = {
  warmth: 'Warmth',
  assertiveness: 'Assert',
  playfulness: 'Playful',
  formality: 'Formal',
  curiosity: 'Curious',
  independence: 'Indep',
}

function draw() {
  if (!svgRef.value) return
  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()

  const width = 300, height = 300
  const cx = width / 2, cy = height / 2
  const maxR = 115

  const g = svg.append('g').attr('transform', `translate(${cx},${cy})`)

  // Background rings
  for (let r = 0.25; r <= 1; r += 0.25) {
    g.append('circle')
      .attr('r', maxR * r)
      .attr('fill', 'none')
      .attr('stroke', 'rgba(232,230,240,0.04)')
      .attr('stroke-width', 1)
  }

  const n = dimensions.value.length
  if (n === 0) return

  const angleStep = (Math.PI * 2) / n

  // Axis lines
  dimensions.value.forEach((_, i) => {
    const angle = i * angleStep - Math.PI / 2
    g.append('line')
      .attr('x1', 0).attr('y1', 0)
      .attr('x2', Math.cos(angle) * maxR)
      .attr('y2', Math.sin(angle) * maxR)
      .attr('stroke', 'rgba(232,230,240,0.04)')
      .attr('stroke-width', 1)
  })

  // Filled shape (connecting all tips)
  const shapePoints = dimensions.value.map((dim, i) => {
    const angle = i * angleStep - Math.PI / 2
    const r = dim.value * maxR
    return `${Math.cos(angle) * r},${Math.sin(angle) * r}`
  }).join(' ')

  g.append('polygon')
    .attr('points', shapePoints)
    .attr('fill', 'rgba(0, 229, 200, 0.06)')
    .attr('stroke', 'rgba(0, 229, 200, 0.15)')
    .attr('stroke-width', 1)

  // Petals + tips
  dimensions.value.forEach((dim, i) => {
    const angle = i * angleStep - Math.PI / 2
    const r = dim.value * maxR
    const tipX = Math.cos(angle) * r
    const tipY = Math.sin(angle) * r
    const labelR = maxR + 24

    // Petal shape
    const petalWidth = 16
    const perpAngle = angle + Math.PI / 2

    const path = d3.path()
    path.moveTo(0, 0)
    path.quadraticCurveTo(
      Math.cos(perpAngle) * petalWidth + tipX * 0.5,
      Math.sin(perpAngle) * petalWidth + tipY * 0.5,
      tipX, tipY
    )
    path.quadraticCurveTo(
      Math.cos(perpAngle) * -petalWidth + tipX * 0.5,
      Math.sin(perpAngle) * -petalWidth + tipY * 0.5,
      0, 0
    )

    g.append('path')
      .attr('d', path.toString())
      .attr('fill', colors[dim.key] || '#00e5c8')
      .attr('opacity', 0.25)
      .attr('stroke', colors[dim.key] || '#00e5c8')
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.5)

    // Tip dot (draggable)
    g.append('circle')
      .attr('cx', tipX)
      .attr('cy', tipY)
      .attr('r', 5)
      .attr('fill', colors[dim.key] || '#00e5c8')
      .attr('stroke', 'rgba(232,230,240,0.3)')
      .attr('stroke-width', 1.5)
      .attr('cursor', 'pointer')
      .attr('filter', 'drop-shadow(0 0 4px rgba(0,229,200,0.3))')
      .call(d3.drag<SVGCircleElement, unknown>()
        .on('drag', (event) => {
          const dx = event.x, dy = event.y
          const dist = Math.sqrt(dx * dx + dy * dy)
          const newValue = Math.max(0, Math.min(1, dist / maxR))
          emit('update', dim.key, Math.round(newValue * 100) / 100)
        }) as any
      )

    // Label
    g.append('text')
      .attr('x', Math.cos(angle) * labelR)
      .attr('y', Math.sin(angle) * labelR)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('fill', 'rgba(232,230,240,0.35)')
      .attr('font-family', 'Sora, sans-serif')
      .attr('font-size', '10px')
      .attr('letter-spacing', '1px')
      .text(`${labels[dim.key] || dim.key} ${(dim.value * 100).toFixed(0)}%`)
  })
}

onMounted(draw)
watch(() => props.data, draw, { deep: true })
</script>

<template>
  <div class="petals-container">
    <svg ref="svgRef" viewBox="0 0 300 300" width="300" height="300" />
  </div>
</template>

<style scoped>
.petals-container {
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
