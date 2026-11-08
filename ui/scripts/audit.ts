// Design and safety gate over ui/src (and ui/dist when it exists). No dependencies, cross-platform.
// Run: npm --prefix ui run audit. Exits 1 with one "AUDIT FAIL file:line rule" line per violation.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const UI = fileURLToPath(new URL('..', import.meta.url))
const SRC = join(UI, 'src')

interface Rule {
  name: string
  re: RegExp
  // Files (relative to ui/, forward slashes) where the pattern is allowed. Prefix match when it ends in "/".
  allow?: string[]
}

const PALETTE =
  'red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone'
const UTIL = 'bg|text|border|ring|fill|stroke|outline|divide|decoration|accent|caret|from|to|via|placeholder'

const RULES: Rule[] = [
  { name: 'raw-html injection prop', re: /dangerouslySetInnerHTML/ },
  { name: 'emoji', re: /\p{Extended_Pictographic}/u },
  { name: 'raw hex colour outside index.css', re: /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/, allow: ['src/index.css'] },
  { name: 'Tailwind default-palette colour class', re: new RegExp(`\\b(?:${UTIL})-(?:${PALETTE})-\\d{2,3}\\b`) },
  {
    // Red is still only for a fraud hold (D-05). D-31 adds the held card's edge and the clock-bar hold
    // line; D-27 adds the lit ring in the fraud graph and its evidence drawer.
    name: 'hold or destructive colour outside the hold surfaces (Badges, Board, ClockBar, FraudGraph, EvidenceDrawer, components/ui/, index.css)',
    re: /\b(?:bg|text|border|ring|fill|stroke|outline|divide)-(?:hold|destructive)(?:-soft|-foreground)?\b/,
    allow: [
      'src/components/app/Badges.tsx',
      'src/components/app/Board.tsx',
      'src/components/app/ClockBar.tsx',
      'src/components/app/FraudGraph.tsx',
      'src/components/app/EvidenceDrawer.tsx',
      'src/components/app/Overview.tsx',
      'src/components/app/UploadRun.tsx',
      'src/components/app/DealDetail.tsx',
      'src/components/ui/',
      'src/index.css',
    ],
  },
  {
    // D-24: motion is CSS only (transitions and keyframes in index.css).
    name: 'animation library import',
    re: /\bfrom\s+['"](?:framer-motion|motion(?:\/[^'"]*)?|gsap|@react-spring\/[^'"]+|react-spring|animejs|lottie[^'"]*|@formkit\/auto-animate[^'"]*)['"]/,
  },
  {
    // D-23: react-force-graph-2d is the one graph library. No second graph or chart stack.
    name: 'graph or chart library other than react-force-graph-2d',
    re: /\bfrom\s+['"](?:d3(?:-[a-z]+)?|cytoscape[^'"]*|sigma[^'"]*|vis-network[^'"]*|reactflow|@xyflow\/[^'"]+|recharts|chart\.js|react-force-graph(?!-2d)[^'"]*|force-graph|3d-force-graph)['"]/,
  },
  { name: 'innerHTML write', re: /\.innerHTML\s*=|\bouterHTML\s*=/ },
  {
    // Canvas colours come from CSS custom properties (src/graph/palette.ts), never literals.
    name: 'rgb or hsl colour literal outside index.css',
    re: /\b(?:rgba?|hsla?)\(\s*\d/,
    allow: ['src/index.css'],
  },
  {
    name: 'ok (green) colour outside Badges.tsx, Inbox.tsx, DealDetail.tsx, components/ui/',
    re: /\b(?:bg|text|border|ring|fill|stroke|outline|divide)-ok(?:-soft)?\b/,
    allow: [
      'src/components/app/Badges.tsx',
      'src/components/app/Inbox.tsx',
      'src/components/app/DealDetail.tsx',
      'src/components/ui/',
    ],
  },
  {
    name: 'display font or tertiary text outside Primitives.tsx, index.css',
    re: /\bfont-display\b|\btext-tertiary\b/,
    allow: ['src/components/app/Primitives.tsx', 'src/index.css'],
  },
  { name: 'gradient', re: /\bgradient\b|\bbg-(?:linear|radial|conic)-/i },
  { name: 'blur or drop-shadow', re: /\bbackdrop-blur|(?:^|[\s"'`:])blur(?:-[a-z0-9]+)?(?=$|[\s"'`])|(?<![\w-])drop-shadow/ },
  { name: 'medium-or-larger shadow outside components/ui/', re: /(?<![\w-])shadow-(?:md|lg|xl|2xl)\b/, allow: ['src/components/ui/'] },
  { name: 'placeholder text', re: /lorem ipsum|coming soon/i },
  { name: 'forbidden brand name', re: /goldman/i },
  // D-03 amended 2026-11-08: the sponsor's sky blue is allowed as a token in index.css only.
  { name: 'sky blue outside index.css', re: /7399C6/i, allow: ['src/index.css'] },
  { name: 'toast or sonner import', re: /\bfrom\s+['"][^'"]*(?:sonner|toast)[^'"]*['"]|\bimport\s+['"][^'"]*(?:sonner|toast)[^'"]*['"]/i },
  { name: 'VITE_ variable', re: /VITE_/ },
  { name: 'participant data reference', re: /teams\.json|data\/participant|\bparticipant\// },
]

const files: string[] = []
function walk(dir: string): void {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p)
    else if (/\.(ts|tsx|css)$/.test(name)) files.push(p)
  }
}
walk(SRC)
if (existsSync(join(UI, 'index.html'))) files.push(join(UI, 'index.html'))

const fails: string[] = []
const rel = (p: string): string => relative(UI, p).split(sep).join('/')
const allowed = (allow: string[] | undefined, path: string): boolean =>
  allow !== undefined && allow.some((a) => (a.endsWith('/') ? path.startsWith(a) : path === a))

// "#rrggbb" or "#rgb" to hue (degrees) and saturation (0 to 1)
function hexHueSat(hex: string): { h: number; s: number } {
  let x = hex.slice(1)
  if (x.length === 3 || x.length === 4) x = [...x].map((c) => c + c).join('')
  const r = parseInt(x.slice(0, 2), 16) / 255
  const g = parseInt(x.slice(2, 4), 16) / 255
  const b = parseInt(x.slice(4, 6), 16) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  const l = (max + min) / 2
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1))
  let h = 0
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h = (h * 60 + 360) % 360
  }
  return { h, s }
}

// D-03 forbids the brand sky blue; a lifted navy for the dark view must not drift onto it.
function nearGoldmanBlue(hex: string): boolean {
  let x = hex.slice(1)
  if (x.length === 3 || x.length === 4) x = [...x].map((c) => c + c).join('')
  const d = [0x73, 0x99, 0xc6].map((v, k) => v - parseInt(x.slice(k * 2, k * 2 + 2), 16))
  return Math.hypot(d[0], d[1], d[2]) < 48
}

function toPx(v: string): number | null {
  const m = /^(-?[\d.]+)(px|rem)$/.exec(v.trim())
  if (!m) return null
  return m[2] === 'rem' ? parseFloat(m[1]) * 16 : parseFloat(m[1])
}

for (const file of files) {
  const path = rel(file)
  const lines = readFileSync(file, 'utf8').split('\n')
  lines.forEach((line, i) => {
    for (const rule of RULES) {
      if (allowed(rule.allow, path)) continue
      if (rule.re.test(line)) fails.push(`AUDIT FAIL ${path}:${i + 1} ${rule.name}`)
    }
    if (path === 'src/index.css') {
      const radius = /--radius[\w-]*\s*:\s*([^;]+);/.exec(line)
      if (radius) {
        const px = toPx(radius[1])
        if (px !== null && px > 12) fails.push(`AUDIT FAIL ${path}:${i + 1} --radius above 12px (${radius[1].trim()})`)
      }
      // No general dark theme. The one dark scope is the investigation view (D-25).
      if (/\.dark\b|prefers-color-scheme\s*:\s*dark|data-theme\s*=\s*["']?dark/.test(line)) {
        fails.push(`AUDIT FAIL ${path}:${i + 1} dark theme selector`)
      }
      const scope = /\[data-view\s*=\s*["']?([\w-]+)/.exec(line)
      if (scope && scope[1] !== 'investigate') {
        fails.push(`AUDIT FAIL ${path}:${i + 1} data-view scope other than investigate (${scope[1]})`)
      }
      for (const hex of line.match(/#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b/g) ?? []) {
        const { h, s } = hexHueSat(hex)
        if (s > 0.15 && h >= 250 && h <= 320) fails.push(`AUDIT FAIL ${path}:${i + 1} purple-range colour ${hex}`)
        if (nearGoldmanBlue(hex)) fails.push(`AUDIT FAIL ${path}:${i + 1} colour too close to the forbidden sky blue ${hex}`)
      }
      for (const m of line.matchAll(/oklch\(\s*[\d.%]+\s+([\d.]+)\s+([\d.]+)/g)) {
        const c = parseFloat(m[1])
        const h = parseFloat(m[2])
        if (c > 0.05 && h >= 260 && h <= 330) fails.push(`AUDIT FAIL ${path}:${i + 1} purple-range colour ${m[0]}`)
      }
    }
  })
}

// Built output must not carry PDFs or participant data.
const dist = join(UI, 'dist')
let distCount = 0
if (existsSync(dist)) {
  const walkDist = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) {
        walkDist(p)
        continue
      }
      distCount++
      if (/\.pdf$/i.test(name)) fails.push(`AUDIT FAIL ${rel(p)}:1 pdf in dist`)
      else if (/\.(js|css|html|json|svg|map)$/.test(name) && /participant/i.test(readFileSync(p, 'utf8'))) {
        fails.push(`AUDIT FAIL ${rel(p)}:1 "participant" in dist`)
      }
    }
  }
  walkDist(dist)
}

if (fails.length > 0) {
  for (const f of fails) console.log(f)
  process.exit(1)
}
console.log(`AUDIT OK files=${files.length}${distCount > 0 ? ` dist=${distCount}` : ''}`)
