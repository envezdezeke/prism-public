// Canvas cannot use Tailwind classes, so the graph reads its colours from the CSS custom properties in
// index.css at draw time. Tokens stay single-source (D-32): no colour literal lives in TypeScript.
export interface GraphPalette {
  page: string
  surface: string
  ink: string
  ink2: string
  line: string
  navy: string
  hold: string
  risk: string
  ok: string
  deal: string
  history: string
  edge: string
  document: string
  identity: string
  money: string
}

const VARS: Record<keyof GraphPalette, string> = {
  page: '--color-page',
  surface: '--color-surface',
  ink: '--color-ink',
  ink2: '--color-ink-2',
  line: '--color-line',
  navy: '--color-navy',
  hold: '--color-hold',
  risk: '--color-risk',
  ok: '--color-ok',
  deal: '--color-graph-deal',
  history: '--color-graph-history',
  edge: '--color-graph-edge',
  document: '--color-graph-document',
  identity: '--color-graph-identity',
  money: '--color-graph-money',
}

export function readPalette(el: Element = document.documentElement): GraphPalette {
  const cs = getComputedStyle(el)
  const out = {} as GraphPalette
  for (const k of Object.keys(VARS) as (keyof GraphPalette)[]) out[k] = cs.getPropertyValue(VARS[k]).trim()
  return out
}

// Canvas alpha for a token colour. Works for the #rrggbb tokens index.css declares.
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(color)
  if (!m) return color
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
  return `#${m[1]}${a.toString(16).padStart(2, '0')}`
}
