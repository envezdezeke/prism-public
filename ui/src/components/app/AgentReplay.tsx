import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, CheckCircle2, FileSearch, FileText, Folder, FolderOpen, History, Network, Pause, Play, RotateCcw, SkipForward, Sparkles, Users, X } from 'lucide-react'
import type { InvestigatorTrace, TraceStep } from '../../api/types.ts'
import type { GEdge, GNode } from '../../graph/graphview.ts'
import type { AgentFocus, GraphControls } from './GraphCanvas.tsx'

// Full-window playback of a recorded Nemotron investigator trace. Each tool call is an errand:
// the cursor goes to the source on the left shelf, picks up a file card, carries it to the graph
// node it is about, reads it there, then files it into the case on the right. Display only: the
// deterministic engine made the hold; nothing here changes a scored output.

type Pt = { x: number; y: number }
type Phase = 'fetch' | 'carry' | 'scan' | 'file' | 'done'
type SourceKind = 'docs' | 'registry' | 'history' | 'ring'

interface ToolLook {
  color: string
  label: string
  icon: typeof Network
  source: SourceKind
  say: (target: string) => string
}

const TOOLS: Record<string, ToolLook> = {
  cluster_summary: { color: 'var(--color-agent-cluster)', label: 'Ring summary', icon: Network, source: 'ring', say: () => 'Sizing up the whole ring' },
  deal_entities: { color: 'var(--color-agent-entities)', label: 'Parties & accounts', icon: Users, source: 'registry', say: (t) => `Pulling the parties on ${t}` },
  entity_deals: { color: 'var(--color-agent-reach)', label: 'Who else uses it', icon: FileSearch, source: 'registry', say: (t) => `Who else uses “${t}”?` },
  deal_documents: { color: 'var(--color-agent-docs)', label: 'Documents', icon: FileText, source: 'docs', say: (t) => `Reading the file for ${t}` },
  history_deal: { color: 'var(--color-agent-history)', label: 'Loan history', icon: History, source: 'history', say: (t) => `Looking up prior loan ${t}` },
}
// Older trace tool names (the mock fixture) map onto the same looks.
TOOLS.graph_neighbors = { ...TOOLS.entity_deals, label: 'Graph neighbours', say: (t) => `Following the links out of ${t}` }
TOOLS.history_search = TOOLS.history_deal
TOOLS.doc_forensics = { ...TOOLS.deal_documents, label: 'Document forensics', say: (t) => `Comparing how ${t} was produced` }
TOOLS.deposit_pattern = { color: 'var(--color-agent-other)', label: 'Deposit pattern', icon: Sparkles, source: 'docs', say: () => 'Scanning deposits for a staged pattern' }
const FALLBACK: ToolLook = { color: 'var(--color-agent-other)', label: 'Lookup', icon: Sparkles, source: 'registry', say: (t) => `Looking into ${t}` }
const mix = (c: string, pct: number): string => `color-mix(in srgb, ${c} ${pct}%, transparent)`
// Canvas needs a real colour, not a CSS variable.
function resolveColor(c: string): string {
  const m = /var\((--[\w-]+)\)/.exec(c)
  if (!m || typeof document === 'undefined') return c
  return getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() || c
}
const look = (tool: string): ToolLook => TOOLS[tool] ?? FALLBACK

const MS: Record<Exclude<Phase, 'done'>, number> = { fetch: 850, carry: 950, scan: 1300, file: 600 }
const NEXT: Record<Exclude<Phase, 'done'>, Phase> = { fetch: 'carry', carry: 'scan', scan: 'file', file: 'fetch' }

const norm = (s: string): string => s.toLowerCase().split(/\s+/).filter(Boolean).sort().join(' ')

export function resolveTargets(step: TraceStep, nodes: GNode[], clusterDeals: string[]): { ids: string[]; name: string } {
  const a = step.args as Record<string, unknown>
  const str = (k: string): string | null => (typeof a[k] === 'string' ? (a[k] as string) : null)
  const byName = (v: string): GNode | undefined => {
    const l = v.toLowerCase()
    return nodes.find((n) => n.id.toLowerCase() === l || n.label.toLowerCase() === l)
  }
  const kind = str('kind')
  const value = str('value')
  if (value) {
    const nv = norm(value)
    const hit = nodes.find((n) => n.type === 'entity' && (!kind || n.kind === kind) && norm(n.label) === nv)
    if (hit) return { ids: [hit.id], name: value }
  }
  for (const k of ['deal', 'deal_id', 'entity', 'doc']) {
    const v = str(k)
    const hit = v ? byName(v) : undefined
    if (hit) return { ids: [hit.id], name: hit.label }
  }
  const present = clusterDeals.filter((d) => nodes.some((n) => n.id === d))
  const name = str('cluster_id') ?? str('deal') ?? value ?? 'the ring'
  return { ids: present, name }
}

function neighbours(ids: string[], edges: GEdge[]): string[] {
  const set = new Set(ids)
  const out: string[] = []
  for (const e of edges) {
    if (set.has(e.source) && !set.has(e.target)) out.push(e.target)
    else if (set.has(e.target) && !set.has(e.source)) out.push(e.source)
    if (out.length >= 10) break
  }
  return [...new Set(out)]
}

function centroid(pts: Pt[]): Pt | null {
  if (pts.length === 0) return null
  return { x: pts.reduce((s, p) => s + p.x, 0) / pts.length, y: pts.reduce((s, p) => s + p.y, 0) / pts.length }
}

interface Card {
  step: number
  tool: string
  name: string
  text: string
}

function useTyped(text: string, active: boolean, cps = 90): string {
  const [n, setN] = useState(0)
  useEffect(() => {
    setN(0)
    if (!active) return
    const id = window.setInterval(() => setN((k) => (k >= text.length ? k : k + 2)), 1000 / cps)
    return () => window.clearInterval(id)
  }, [text, active, cps])
  return active ? text.slice(0, n) : ''
}

// ------------------------------------------------------------------ connections diagram

function Connections({ touched, nodes, edges, active }: { touched: string[]; nodes: GNode[]; edges: GEdge[]; active: Set<string> }) {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const deals = touched.filter((id) => byId.get(id)?.type !== 'entity')
  const ents = touched.filter((id) => byId.get(id)?.type === 'entity')
  if (deals.length + ents.length === 0) return <p className="text-xs text-ink-2">Connections appear here as the agent finds them.</p>
  const W = 288
  const pos = new Map<string, Pt>()
  deals.forEach((id, i) => pos.set(id, { x: ((i + 0.5) * W) / deals.length, y: 22 }))
  ents.forEach((id, i) => pos.set(id, { x: ((i + 0.5) * W) / Math.max(ents.length, 1), y: 82 }))
  const links = edges.filter((e) => pos.has(e.source) && pos.has(e.target))
  const short = (id: string): string => {
    const l = byId.get(id)?.label ?? id
    return l.length > 14 ? `${l.slice(0, 13)}…` : l
  }
  return (
    <svg width={W} height={104} className="overflow-visible" aria-label="Connections found so far">
      {links.map((e, i) => {
        const a = pos.get(e.source) as Pt
        const b = pos.get(e.target) as Pt
        return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--color-ink-2)" strokeWidth={1.25} opacity={0.6} className="animate-fade-in" />
      })}
      {[...pos.entries()].map(([id, p]) => {
        const deal = byId.get(id)?.type !== 'entity'
        const on = active.has(id)
        return (
          <g key={id} className="animate-fade-in">
            <circle cx={p.x} cy={p.y} r={deal ? 7 : 5} fill={deal ? 'var(--color-ink)' : 'var(--color-surface)'} stroke="var(--color-ink)" strokeWidth={on ? 2.5 : 1.25} />
            <text x={p.x} y={deal ? p.y - 11 : p.y + 16} textAnchor="middle" fontSize={10} fill="var(--color-ink-2)">
              {short(id)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ------------------------------------------------------------------ main

export function AgentReplay({
  trace,
  nodes,
  edges,
  clusterDeals,
  controls,
  onClose,
  onFocus,
}: {
  trace: InvestigatorTrace
  nodes: GNode[]
  edges: GEdge[]
  clusterDeals: string[]
  controls: React.MutableRefObject<GraphControls | null>
  onClose: () => void
  onFocus: (f: AgentFocus | null) => void
}) {
  const wrap = useRef<HTMLDivElement>(null)
  const caseFile = useRef<HTMLDivElement>(null)
  const tileRefs = useRef(new Map<string, HTMLElement>())
  const [stepIdx, setStepIdx] = useState(0)
  const [phase, setPhase] = useState<Phase>('fetch')
  const [playing, setPlaying] = useState(true)
  const [speed, setSpeed] = useState(1)
  const [cards, setCards] = useState<Card[]>([])
  const [touched, setTouched] = useState<string[]>([])
  const [cursor, setCursor] = useState<Pt>({ x: 120, y: 200 })
  const [trail, setTrail] = useState<Pt[]>([])
  const [flyer, setFlyer] = useState<{ from: Pt; to: Pt; go: boolean } | null>(null)
  const cursorRef = useRef(cursor)

  const steps = trace.steps
  const step = steps[Math.min(stepIdx, steps.length - 1)]
  const tl = look(step?.tool ?? '')
  const target = useMemo(() => (step ? resolveTargets(step, nodes, clusterDeals) : { ids: [], name: '' }), [step, nodes, clusterDeals])
  const neigh = useMemo(() => neighbours(target.ids, edges), [target, edges])
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes])
  const ringDeals = useMemo(() => clusterDeals.filter((d) => byId.get(d)?.type === 'deal'), [clusterDeals, byId])

  // Which shelf tile this errand starts from.
  const tileKey = useMemo(() => {
    if (tl.source === 'docs') {
      const deal = target.ids.find((id) => byId.get(id)?.type === 'deal') ?? ringDeals[0]
      return deal ? `docs:${deal}` : 'registry'
    }
    return tl.source === 'ring' ? 'registry' : tl.source
  }, [tl, target, byId, ringDeals])

  const relRect = (el: HTMLElement | null | undefined): Pt | null => {
    const host = wrap.current?.getBoundingClientRect()
    const r = el?.getBoundingClientRect()
    if (!host || !r) return null
    return { x: r.left - host.left + r.width / 2, y: r.top - host.top + r.height / 2 }
  }

  // Canvas highlight: the node(s) being read, and while reading, their links.
  useEffect(() => {
    if (phase === 'done') {
      onFocus(null)
      return
    }
    const ids = phase === 'fetch' ? new Set<string>() : new Set(target.ids)
    const near = phase === 'scan' || phase === 'file' ? new Set(neigh) : new Set<string>()
    onFocus({ ids, near, color: resolveColor(tl.color) })
  }, [phase, target, neigh, tl.color, onFocus])
  useEffect(() => () => onFocus(null), [onFocus])

  // Frame the step's nodes while the cursor is still at the shelf.
  useEffect(() => {
    if (phase !== 'fetch' || target.ids.length === 0) return
    controls.current?.focusOn(new Set([...target.ids, ...neigh]), 800 / speed, 240)
  }, [stepIdx, phase, target, neigh, controls, speed])

  // Animation loop: the cursor eases toward the goal of the current errand move.
  useEffect(() => {
    let raf = 0
    const tick = (): void => {
      const c = controls.current
      let goal: Pt | null = null
      if (phase === 'fetch') goal = relRect(tileRefs.current.get(tileKey))
      else if (phase === 'carry' || phase === 'scan') {
        goal = c ? centroid(target.ids.map((id) => c.screenPos(id)).filter((p): p is Pt => p !== null)) : null
      } else if (phase === 'file') goal = relRect(caseFile.current)
      if (goal && phase !== 'done') {
        const cur = cursorRef.current
        const k = Math.min((phase === 'scan' ? 0.25 : 0.1) * speed, 1)
        const next = { x: cur.x + (goal.x - cur.x) * k, y: cur.y + (goal.y - cur.y) * k }
        cursorRef.current = next
        setCursor(next)
        setTrail((t) => [...t.slice(-7), next])
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [controls, target, phase, speed, tileKey])

  const finishStep = (): void => {
    setFlyer(null)
    setCards((cs) => [{ step: step.step, tool: step.tool, name: target.name, text: step.result_summary }, ...cs])
    setTouched((t) => [...new Set([...t, ...target.ids])])
    if (stepIdx + 1 >= steps.length) setPhase('done')
    else {
      setStepIdx((i) => i + 1)
      setPhase('fetch')
    }
  }

  // Errand clock: fetch -> carry -> scan -> file -> next step.
  useEffect(() => {
    if (!playing || phase === 'done') return
    const t = window.setTimeout(() => {
      if (phase === 'scan') {
        const to = relRect(caseFile.current)
        setFlyer({ from: cursorRef.current, to: to ? { x: to.x - 110, y: to.y - 40 } : cursorRef.current, go: false })
        setPhase('file')
      } else if (phase === 'file') finishStep()
      else setPhase(NEXT[phase])
    }, MS[phase] / speed)
    return () => window.clearTimeout(t)
    // Re-arm only on a new phase or step, not on every cursor frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, phase, speed, stepIdx])

  useLayoutEffect(() => {
    if (flyer && !flyer.go) requestAnimationFrame(() => setFlyer((f) => (f ? { ...f, go: true } : f)))
  }, [flyer])

  useEffect(() => {
    if (phase === 'done') controls.current?.focusOn(new Set([...clusterDeals, ...touched]), 900, 300)
  }, [phase, controls, clusterDeals, touched])

  const typed = useTyped(step?.result_summary ?? '', phase === 'scan')
  const restart = (): void => {
    setCards([])
    setTouched([])
    setStepIdx(0)
    setPhase('fetch')
    setPlaying(true)
  }
  const skip = (): void => {
    if (phase !== 'done') finishStep()
  }

  const Icon = tl.icon
  const carrying = phase === 'carry' || phase === 'scan'
  const activeIds = new Set(phase === 'done' ? [] : target.ids)

  const tiles: { key: string; label: string; sub: string; icon: typeof Folder; openIcon: typeof Folder }[] = [
    ...ringDeals.map((d) => ({ key: `docs:${d}`, label: byId.get(d)?.label ?? d, sub: 'Document folder', icon: Folder, openIcon: FolderOpen })),
    { key: 'registry', label: 'Parties & accounts', sub: 'Entity registry', icon: Users, openIcon: Users },
    { key: 'history', label: 'Loan history', sub: 'Prior deals on file', icon: BookOpen, openIcon: BookOpen },
  ]

  return (
    <div ref={wrap} className="pointer-events-none absolute inset-0 z-10" aria-live="polite">
      {/* Header strip. */}
      <div className="pointer-events-auto absolute inset-x-4 top-4 flex items-center gap-3 rounded-lg border border-line bg-surface px-4 py-2.5 shadow-sm">
        <span className="relative flex size-2.5">
          {phase !== 'done' && playing && <span className="absolute inline-flex size-full animate-ping rounded-full opacity-75" style={{ background: tl.color }} />}
          <span className="relative inline-flex size-2.5 rounded-full" style={{ background: phase === 'done' ? 'var(--color-agent-done)' : tl.color }} />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink">
            {phase === 'done' ? trace.mode === 'off' ? `Case assembled on ${trace.cluster_id}` : `Nemotron assembled the case on ${trace.cluster_id}` : trace.mode === 'off' ? `Walking through the evidence on ${trace.cluster_id}` : `Nemotron is investigating ${trace.cluster_id}`}
          </p>
          <p className="text-xs text-ink-2">
            {trace.mode === 'off' ? 'Walk-through of the engine’s evidence (no recorded Nemotron run for this ring)' : 'Replay of a recorded Nemotron run'} · advisory only, decisions come from the engine · Esc to exit
          </p>
        </div>
        <ol className="ml-4 flex flex-1 items-center gap-1" aria-label="Steps">
          {steps.map((s, i) => (
            <li
              key={i}
              title={`${s.step}. ${s.tool}`}
              className="h-1.5 flex-1 rounded-full transition-all duration-500"
              style={{
                background: i < stepIdx || phase === 'done' ? look(s.tool).color : i === stepIdx ? mix(look(s.tool).color, 55) : 'var(--color-line)',
                transform: i === stepIdx && phase !== 'done' ? 'scaleY(1.8)' : 'none',
              }}
            />
          ))}
        </ol>
        <span className="text-xs tabular-nums text-ink-2">
          {Math.min(stepIdx + 1, steps.length)}/{steps.length}
        </span>
        <div className="flex items-center gap-1">
          <IconBtn label={playing ? 'Pause' : 'Play'} onClick={() => setPlaying((p) => !p)} disabled={phase === 'done'}>
            {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          </IconBtn>
          <IconBtn label="Next step" onClick={skip} disabled={phase === 'done'}>
            <SkipForward className="size-3.5" />
          </IconBtn>
          <IconBtn label="Replay" onClick={restart}>
            <RotateCcw className="size-3.5" />
          </IconBtn>
          <button type="button" onClick={() => setSpeed((s) => (s === 1 ? 2 : 1))} className="h-7 rounded-md border border-line px-2 text-xs font-medium text-ink-2 hover:bg-subtle">
            {speed}×
          </button>
          <IconBtn label="Exit replay" onClick={onClose}>
            <X className="size-3.5" />
          </IconBtn>
        </div>
      </div>

      {/* Left: the sources shelf. */}
      <aside className="pointer-events-auto absolute top-24 bottom-4 left-4 flex w-56 flex-col gap-2 rounded-lg border border-line bg-surface p-3 shadow-sm">
        <p className="text-xs font-semibold tracking-wide text-ink-2 uppercase">Sources</p>
        {tiles.map((t) => {
          const open = phase === 'fetch' && tileKey === t.key
          const TIcon = open ? t.openIcon : t.icon
          return (
            <div
              key={t.key}
              ref={(el) => {
                if (el) tileRefs.current.set(t.key, el)
              }}
              className="relative flex items-center gap-2 rounded-md border px-2 py-2 transition-all duration-300"
              style={{
                borderColor: open ? tl.color : 'var(--color-line)',
                background: open ? mix(tl.color, 8) : 'var(--color-surface)',
                transform: open ? 'translateX(4px)' : 'none',
              }}
            >
              <TIcon className="size-4 shrink-0" style={{ color: open ? tl.color : 'var(--color-ink-2)' }} aria-hidden="true" />
              <span className="min-w-0">
                <span className="block truncate text-xs font-semibold text-ink">{t.label}</span>
                <span className="block text-xs text-ink-2">{t.sub}</span>
              </span>
              {/* Pages fanning out of an opened source. */}
              {open &&
                [0, 1, 2].map((i) => (
                  <span
                    key={i}
                    aria-hidden="true"
                    className="absolute top-1 right-3 h-6 w-4 animate-fade-in rounded-sm border bg-surface"
                    style={{ borderColor: tl.color, transform: `translateX(${(i - 1) * 7}px) rotate(${(i - 1) * 12}deg)` }}
                  />
                ))}
            </div>
          )
        })}
      </aside>

      {/* Right: the case file. */}
      <aside className="pointer-events-auto absolute top-24 right-4 bottom-4 flex w-80 flex-col rounded-lg border border-line bg-surface shadow-sm">
        <div ref={caseFile} className="border-b border-line px-3 py-2">
          <p className="text-xs font-semibold text-ink">
            Case file <span className="font-normal text-ink-2">· {cards.length} findings</span>
          </p>
          <div className="pt-2">
            <Connections touched={touched} nodes={nodes} edges={edges} active={activeIds} />
          </div>
        </div>
        <ol className="flex-1 space-y-2 overflow-y-auto p-2">
          {cards.map((c) => {
            const l = look(c.tool)
            const CIcon = l.icon
            return (
              <li key={`${c.step}-${c.tool}`} className="animate-fade-in rounded-md border-l-4 bg-subtle px-2 py-1.5 text-xs leading-snug text-ink" style={{ borderColor: l.color }}>
                <span className="mb-0.5 flex items-center gap-1 font-semibold" style={{ color: l.color }}>
                  <CIcon className="size-3" aria-hidden="true" />
                  {c.step}. {l.label}
                </span>
                {c.text}
              </li>
            )
          })}
        </ol>
        {phase === 'done' && (
          <div className="border-t border-line p-3">
            <p className="mb-1 flex items-center gap-1 text-xs font-semibold text-ink">
              <Sparkles className="size-3.5" aria-hidden="true" /> Case summary
              <span
                className="animate-fade-in ml-auto inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold"
                style={{ borderColor: 'var(--color-agent-done)', color: 'var(--color-agent-done)' }}
              >
                <CheckCircle2 className="size-3" aria-hidden="true" /> Replay complete
              </span>
            </p>
            <p className="text-xs leading-relaxed text-ink">{trace.case_summary}</p>
          </div>
        )}
      </aside>

      {/* The agent: trail, cursor, name tag with its intent, and the file it carries. */}
      {phase !== 'done' && (
        <>
          {trail.map((p, i) => (
            <span key={i} aria-hidden="true" className="absolute rounded-full" style={{ left: p.x - 3, top: p.y - 3, width: 6, height: 6, background: tl.color, opacity: ((i + 1) / trail.length) * 0.3 }} />
          ))}
          <div aria-hidden="true" className="absolute z-30" style={{ left: cursor.x, top: cursor.y }}>
            <svg width="26" height="26" viewBox="0 0 24 24">
              <path d="M3 2 L20 11 L12 13 L9 21 Z" fill={tl.color} stroke="white" strokeWidth="1.6" strokeLinejoin="round" />
            </svg>
            <span className="absolute top-5 left-5 flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold whitespace-nowrap text-white shadow-sm" style={{ background: tl.color }}>
              <Icon className="size-3" aria-hidden="true" /> {phase === 'fetch' ? 'Fetching' : 'Nemotron'} · {tl.say(target.name)}
            </span>
            {carrying && (
              <div className="absolute top-12 left-4 w-64 animate-fade-in rounded-md border bg-surface px-2.5 py-2 text-xs shadow-sm" style={{ borderColor: tl.color }}>
                <p className="flex items-center gap-1 font-semibold" style={{ color: tl.color }}>
                  <FileText className="size-3" aria-hidden="true" /> {tl.label} · {target.name}
                </p>
                {phase === 'scan' && (
                  <p className="mt-1 border-t border-line pt-1 font-mono text-xs text-ink-2">
                    {typed}
                    <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse align-middle" style={{ background: tl.color }} />
                  </p>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {/* The finding flying into the case file. */}
      {flyer && (
        <div
          className="absolute z-20 w-56 rounded-md border bg-surface px-2 py-1.5 text-xs shadow-sm"
          style={{
            left: 0,
            top: 0,
            borderColor: tl.color,
            transform: `translate(${(flyer.go ? flyer.to : flyer.from).x}px, ${(flyer.go ? flyer.to : flyer.from).y}px) scale(${flyer.go ? 0.85 : 1})`,
            transition: `transform ${(MS.file * 0.9) / speed}ms cubic-bezier(.5,0,.2,1)`,
          }}
        >
          <span className="font-semibold" style={{ color: tl.color }}>
            {tl.label}
          </span>{' '}
          · {target.name}
        </div>
      )}
    </div>
  )
}

function IconBtn({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      disabled={disabled}
      className="inline-flex size-7 items-center justify-center rounded-md border border-line text-ink hover:bg-subtle disabled:opacity-40"
    >
      {children}
    </button>
  )
}
