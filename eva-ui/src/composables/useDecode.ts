// Decode/scramble reveal — text settles from code-glyphs into the real string.
// LEXICON's signature motion: Eva is made of tokens, so she "decodes" into view.
const reduce = matchMedia('(prefers-reduced-motion:reduce)').matches
const CSET = 'アイウエオカキクサシスナ01<>{}/$#*+=日心言'

/** Scramble-reveal a single string into `el`'s text content. */
export function decodeText(el: HTMLElement, finalText: string, dur = 460): void {
  if (reduce) { el.textContent = finalText; return }
  const len = finalText.length
  const start = performance.now()
  const step = () => {
    const p = Math.min(1, (performance.now() - start) / dur)
    const reveal = Math.floor(p * len)
    let out = ''
    for (let i = 0; i < len; i++) {
      const ch = finalText[i]
      out += (i < reveal || ch === ' ' || ch === '\n') ? ch : CSET[(Math.random() * CSET.length) | 0]
    }
    el.textContent = out
    if (p < 1) requestAnimationFrame(step)
    else el.textContent = finalText
  }
  requestAnimationFrame(step)
}

/** Decode every leaf text node already inside `el` (preserves inner markup like <b>). */
export function decodeInPlace(el: HTMLElement, dur = 460): void {
  if (reduce) return
  const nodes = walkText(el)
  for (const node of nodes) {
    const target = node.textContent || ''
    const len = target.length
    const start = performance.now()
    const step = () => {
      const p = Math.min(1, (performance.now() - start) / dur)
      const reveal = Math.floor(p * len)
      let out = ''
      for (let i = 0; i < len; i++) {
        const ch = target[i]
        out += (i < reveal || ch === ' ' || ch === '\n') ? ch : CSET[(Math.random() * CSET.length) | 0]
      }
      node.textContent = out
      if (p < 1) requestAnimationFrame(step)
      else node.textContent = target
    }
    requestAnimationFrame(step)
  }
}

function walkText(el: HTMLElement): Text[] {
  const out: Text[] = []
  const walk = (n: Node) => {
    n.childNodes.forEach(c => {
      if (c.nodeType === Node.TEXT_NODE && (c.textContent || '').trim()) out.push(c as Text)
      else if (c.nodeType === Node.ELEMENT_NODE) walk(c)
    })
  }
  walk(el)
  return out
}
