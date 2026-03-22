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
  warmth: '#ee6688',
  assertiveness: '#66bbee',
  playfulness: '#ee88aa',
  formality: '#8899aa',
  curiosity: '#aa77dd',
  independence: '#66aaee',
}

const labels: Record<string, string> = {
  warmth: '温暖',
  assertiveness: '主张',
  playfulness: '活泼',
  formality: '正式',
  curiosity: '好奇',
  independence: '独立',
}

function draw() {
  if (!svgRef.value) return
  const svg = d3.select(svgRef.value)
  svg.selectAll('*').remove()

  const width = 300, height = 300
  const cx = width / 2, cy = height / 2
  const maxR = 120

  const g = svg.append('g').attr('transform', `translate(${cx},${cy})`)

  // Background circles
  for (let r = 0.25; r <= 1; r += 0.25) {
    g.append('circle')
      .attr('r', maxR * r)
      .attr('fill', 'none')
      .attr('stroke', 'hsla(200, 30%, 30%, 0.15)')
      .attr('stroke-width', 1)
  }

  const n = dimensions.value.length
  if (n === 0) return

  const angleStep = (Math.PI * 2) / n

  // Petals
  dimensions.value.forEach((dim, i) => {
    const angle = i * angleStep - Math.PI / 2
    const r = dim.value * maxR
    const tipX = Math.cos(angle) * r
    const tipY = Math.sin(angle) * r
    const labelR = maxR + 20

    // Petal shape (elongated ellipse approximation)
    const petalWidth = 18
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
      .attr('fill', colors[dim.key] || '#66aaee')
      .attr('opacity', 0.4)
      .attr('stroke', colors[dim.key] || '#66aaee')
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.6)

    // Tip dot (draggable)
    g.append('circle')
      .attr('cx', tipX)
      .attr('cy', tipY)
      .attr('r', 6)
      .attr('fill', colors[dim.key] || '#66aaee')
      .attr('stroke', 'white')
      .attr('stroke-width', 1.5)
      .attr('cursor', 'pointer')
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
      .attr('fill', 'var(--eva-text-dim)')
      .attr('font-size', '11px')
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
