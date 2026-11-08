import { useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import type { ForceGraphMethods, LinkObject, NodeObject } from 'react-force-graph-2d'
import { edgeKey, kindGroup } from '../../graph/graphview.ts'
import type { GEdge, GNode, GraphView } from '../../graph/graphview.ts'
import type { GraphPalette } from '../../graph/palette.ts'
import { withAlpha } from '../../graph/palette.ts'
import { LayoutCache } from '../../graph/layoutCache.ts'
import { entityKindLabel } from '../../labels.ts'

// Imperative zoom controls for the page toolbar (react-force-graph zoom / zoomToFit).
export interface GraphControls {
  zoomIn: () => void
  zoomOut: () => void
  fit: () => void
  // Screen position (px, relative to the canvas) of a node, or null when it is not laid out.
  screenPos: (id: string) => { x: number; y: number } | null
  // Zoom to a set of nodes (used by the agent replay).
  focusOn: (ids: Set<string>, ms?: number, pad?: number) => void
}

// What the Nemotron replay is working on: the nodes it reads, their linked nodes, and its colour.
export type AgentFocus = { ids: Set<string>; near: Set<string>; color: string }

export type Selection = { kind: 'edge'; edge: GEdge } | { kind: 'node'; id: string } | null

interface NodeData {
  id: string
  node: GNode
  // Last drawn card size (graph units), for the pointer area.
  __card?: { w: number; h: number }
}
interface LinkData {
  key: string
  edge: GEdge
}
type N = NodeObject<NodeData>
type L = LinkObject<NodeData, LinkData>

// Canvas sizes in screen pixels; divided by the zoom so text stays legible on a projector.
const LABEL_PX = 13
const EDGE_LABEL_PX = 12
const DEAL_R = 7
const ENTITY_SIDE = 8
// Entities are small circles (radius in graph units).
const ENTITY_R = 5.5
// A new edge fades in over this many real milliseconds when the clock reveals it (D-29).
const FADE_MS = 600
// Canvas-only halo on ring elements (D-30). Never applied to anything else.
const HALO = 14
const GRAVITY = 0.03
// Text keeps a fixed screen size when zoomed in, and shrinks with the graph when zoomed out.
const textScale = (scale: number): number => Math.max(scale, 1)

const endId = (v: L['source']): string => (typeof v === 'object' && v !== null ? String(v.id) : String(v))

export function GraphCanvas({
  view,
  ringDeals,
  ringHeld,
  palette,
  selection,
  onSelect,
  fitMode,
  fitToken,
  controlsRef,
  onZoom,
  agentFocus = null,
}: {
  agentFocus?: AgentFocus | null
  controlsRef?: React.MutableRefObject<GraphControls | null>
  onZoom?: (k: number) => void
  view: GraphView
  ringDeals: Set<string>
  ringHeld: boolean
  palette: GraphPalette
  selection: Selection
  onSelect: (s: Selection) => void
  fitMode: 'focus' | 'all'
  fitToken: number
}) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const fgRef = useRef<ForceGraphMethods<N, L> | undefined>(undefined)
  const [size, setSize] = useState({ w: 0, h: 0 })
  // Stable objects keep the force layout from jumping between clock ticks.
  const [cache] = useState(() => new LayoutCache())
  const data = useMemo(() => cache.sync(view) as { nodes: N[]; links: L[] }, [cache, view])
  const needsFit = useRef(true)
  // New nodes from the clock: refit once the layout settles so nothing grows off the canvas.
  useEffect(() => {
    needsFit.current = true
  }, [view.nodes.length])

  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setSize({ w: Math.floor(entry.contentRect.width), h: Math.floor(entry.contentRect.height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    const fg = fgRef.current
    if (!fg) return
    fg.d3Force('charge')?.strength(-320)
    fg.d3Force('link')?.distance(110)
    // Gentle pull to the centre so unlinked deals stay near the ring instead of drifting off-screen.
    let nodes: N[] = []
    const gravity = Object.assign(
      (alpha: number) => {
        for (const n of nodes) {
          n.vx = (n.vx ?? 0) - (n.x ?? 0) * GRAVITY * alpha
          n.vy = (n.vy ?? 0) - (n.y ?? 0) * GRAVITY * alpha
        }
      },
      { initialize: (ns: N[]) => (nodes = ns) },
    )
    fg.d3Force('gravity', gravity)
    fg.d3ReheatSimulation()
  }, [size.w])

  // The latest view and mode, read by the fit (called from an effect and from onEngineStop).
  const fitState = useRef({ view, fitMode })
  useEffect(() => {
    fitState.current = { view, fitMode }
  })
  const fit = (fg: ForceGraphMethods<N, L>): void => {
    const { view: v, fitMode: mode } = fitState.current
    const filter = mode === 'focus' && v.focus.size > 0 ? (n: N) => v.focus.has(String(n.id)) : undefined
    fg.zoomToFit(600, 128, filter)
  }

  useEffect(() => {
    needsFit.current = true
    const fg = fgRef.current
    if (!fg) return
    const { view: v, fitMode: mode } = fitState.current
    const filter = mode === 'focus' && v.focus.size > 0 ? (n: N) => v.focus.has(String(n.id)) : undefined
    fg.zoomToFit(600, 128, filter)
  }, [fitToken, fitMode])

  useEffect(() => {
    if (!controlsRef) return
    controlsRef.current = {
      zoomIn: () => {
        const fg = fgRef.current
        if (fg) fg.zoom(fg.zoom() * 1.4, 300)
      },
      zoomOut: () => {
        const fg = fgRef.current
        if (fg) fg.zoom(fg.zoom() / 1.4, 300)
      },
      fit: () => {
        if (fgRef.current) fit(fgRef.current)
      },
      screenPos: (id: string) => {
        const fg = fgRef.current
        const n = data.nodes.find((m) => String(m.id) === id)
        if (!fg || !n || n.x === undefined || n.y === undefined) return null
        return fg.graph2ScreenCoords(n.x, n.y)
      },
      focusOn: (ids: Set<string>, ms = 900, pad = 170) => {
        const fg = fgRef.current
        if (fg && ids.size > 0) fg.zoomToFit(ms, pad, (n: N) => ids.has(String(n.id)))
      },
    }
  })

  const selectedNode = selection?.kind === 'node' ? selection.id : null
  const selectedEdge = selection?.kind === 'edge' ? edgeKey(selection.edge) : null
  const p = palette

  const fadeAlpha = (key: string): number => cache.fade(key, performance.now(), FADE_MS)

  const drawNode = (obj: N, ctx: CanvasRenderingContext2D, scale: number): void => {
    const n = obj.node
    const x = obj.x ?? 0
    const y = obj.y ?? 0
    const inFocus = view.focus.has(n.id)
    const inRing = ringDeals.has(n.id) || view.ringEntities.has(n.id)
    const lit = inRing && ringHeld
    const alpha = inFocus ? 1 : 0.28
    ctx.save()
    if (lit) {
      ctx.shadowColor = p.hold
      ctx.shadowBlur = HALO
    }
    if (n.type === 'entity') {
      const s = ENTITY_SIDE
      const tint = p[kindGroup(n.kind)]
      void s
      ctx.beginPath()
      ctx.arc(x, y, ENTITY_R, 0, 2 * Math.PI)
      ctx.fillStyle = withAlpha(tint, alpha)
      ctx.fill()
      if (lit) {
        ctx.strokeStyle = p.hold
        ctx.lineWidth = 1.5
        ctx.stroke()
      }
    }
    ctx.restore()
    if (n.type !== 'entity') {
      drawCard(obj, ctx, scale, { inFocus, inRing, lit, alpha })
      drawAgentMark(obj, ctx, scale)
      return
    }
    drawAgentMark(obj, ctx, scale)

    if (selectedNode === n.id) {
      ctx.beginPath()
      ctx.arc(x, y, ENTITY_SIDE + 3, 0, 2 * Math.PI)
      ctx.strokeStyle = p.navy
      ctx.lineWidth = 2 / scale
      ctx.stroke()
    }

    // Labels: deals always; entities show their value and kind, in the focus only.
    if (n.type === 'entity' && !inFocus) return
    const fs = LABEL_PX / textScale(scale)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.font = `${n.type === 'entity' ? 400 : 600} ${fs}px 'Inter Variable', ui-sans-serif, sans-serif`
    ctx.fillStyle = withAlpha(lit ? p.hold : n.type === 'entity' ? p.ink2 : p.navy, inFocus ? 1 : 0.4)
    const top = y + (n.type === 'entity' ? ENTITY_SIDE / 2 : DEAL_R) + 4 / textScale(scale)
    ctx.fillText(n.label, x, top)
    if (n.type === 'entity' && inFocus) {
      ctx.font = `400 ${(LABEL_PX - 1) / textScale(scale)}px 'Inter Variable', ui-sans-serif, sans-serif`
      ctx.fillStyle = withAlpha(p.ink2, 0.9)
      ctx.fillText(entityKindLabel(n.kind), x, top + fs * 1.25)
    }
  }

  // A deal is a small card: icon badge, label and a muted status line (red only when the ring is held,
  // amber while it is forming).
  const drawCard = (
    obj: N,
    ctx: CanvasRenderingContext2D,
    scale: number,
    st: { inFocus: boolean; inRing: boolean; lit: boolean; alpha: number },
  ): void => {
    const n = obj.node
    const x = obj.x ?? 0
    const y = obj.y ?? 0
    const k = textScale(scale)
    const fs = LABEL_PX / k
    const sub = (LABEL_PX - 1) / k
    const pad = 6 / k
    const badge = 8 / k
    const history = n.type === 'history_deal'
    const subtitle = st.inRing ? (st.lit ? 'Held: ring' : 'Ring: not held yet') : history ? 'Prior deal' : st.inFocus ? 'Linked, not held' : 'Not linked'
    const font = (w: number, size: number): string => `${w} ${size}px 'Inter Variable', ui-sans-serif, sans-serif`
    ctx.font = font(600, fs)
    const tw = ctx.measureText(n.label).width
    ctx.font = font(400, sub)
    const sw = ctx.measureText(subtitle).width
    const w = pad * 3 + badge * 2 + Math.max(tw, sw)
    const h = pad * 2 + fs * 1.2 + sub * 1.2
    const left = x - w / 2
    const top = y - h / 2
    obj.__card = { w, h }
    const accent = st.lit ? p.hold : st.inRing ? p.risk : p.navy
    ctx.save()
    ctx.globalAlpha = st.alpha
    if (st.lit) {
      ctx.shadowColor = p.hold
      ctx.shadowBlur = HALO
    }
    ctx.beginPath()
    ctx.roundRect(left, top, w, h, 6 / k)
    ctx.fillStyle = p.surface
    ctx.fill()
    ctx.shadowBlur = 0
    ctx.lineWidth = (selectedNode === n.id ? 2.5 : st.inRing ? 1.5 : 1) / k
    ctx.strokeStyle = selectedNode === n.id ? p.navy : st.inRing ? accent : p.line
    if (history) ctx.setLineDash([4 / k, 3 / k])
    ctx.stroke()
    ctx.setLineDash([])
    // Icon badge: a filled circle with a document glyph (hollow for a prior deal).
    const bx = left + pad + badge
    ctx.beginPath()
    ctx.arc(bx, y, badge, 0, 2 * Math.PI)
    if (history) {
      ctx.strokeStyle = p.history
      ctx.lineWidth = 1.5 / k
      ctx.stroke()
    } else {
      ctx.fillStyle = accent
      ctx.fill()
    }
    ctx.strokeStyle = history ? p.history : p.surface
    ctx.lineWidth = 1.2 / k
    for (const dy of [-2.5, 0, 2.5]) {
      ctx.beginPath()
      ctx.moveTo(bx - 3 / k, y + dy / k)
      ctx.lineTo(bx + 3 / k, y + dy / k)
      ctx.stroke()
    }
    ctx.textAlign = 'left'
    ctx.textBaseline = 'top'
    const tx = bx + badge + pad
    ctx.font = font(600, fs)
    ctx.fillStyle = st.lit ? p.hold : p.navy
    ctx.fillText(n.label, tx, top + pad)
    ctx.font = font(400, sub)
    ctx.fillStyle = st.lit ? p.hold : st.inRing ? p.risk : p.ink2
    ctx.fillText(subtitle, tx, top + pad + fs * 1.2)
    ctx.restore()
  }

  // The replay's highlight: the card or circle the agent is reading gets a soft coloured glow and
  // outline; linked nodes get a thin outline. No extra shapes on the canvas.
  const drawAgentMark = (obj: N, ctx: CanvasRenderingContext2D, scale: number): void => {
    if (!agentFocus) return
    const id = String(obj.id)
    const main = agentFocus.ids.has(id)
    if (!main && !agentFocus.near.has(id)) return
    const k = textScale(scale)
    const x = obj.x ?? 0
    const y = obj.y ?? 0
    ctx.save()
    ctx.strokeStyle = agentFocus.color
    ctx.lineWidth = (main ? 2.5 : 1.5) / k
    if (main) {
      ctx.shadowColor = agentFocus.color
      ctx.shadowBlur = 18
    } else {
      ctx.globalAlpha = 0.7
    }
    ctx.beginPath()
    const c = obj.__card
    if (c && obj.node.type !== 'entity') {
      const g = 4 / k
      ctx.roundRect(x - c.w / 2 - g, y - c.h / 2 - g, c.w + 2 * g, c.h + 2 * g, 8 / k)
    } else {
      ctx.arc(x, y, ENTITY_R + 3 / k, 0, 2 * Math.PI)
    }
    ctx.stroke()
    ctx.restore()
  }

  const paintNodeArea = (obj: N, color: string, ctx: CanvasRenderingContext2D): void => {
    ctx.fillStyle = color
    const c = obj.__card
    if (c) {
      ctx.fillRect((obj.x ?? 0) - c.w / 2, (obj.y ?? 0) - c.h / 2, c.w, c.h)
      return
    }
    ctx.beginPath()
    ctx.arc(obj.x ?? 0, obj.y ?? 0, DEAL_R + 4, 0, 2 * Math.PI)
    ctx.fill()
  }

  // Dotted grid behind the graph, fixed in screen spacing, panning with the view.
  const drawGrid = (ctx: CanvasRenderingContext2D, scale: number): void => {
    const t = ctx.getTransform()
    const gap = 24 / scale
    const x0 = -t.e / t.a
    const y0 = -t.f / t.d
    const x1 = x0 + ctx.canvas.width / t.a
    const y1 = y0 + ctx.canvas.height / t.d
    const r = 1 / scale
    ctx.save()
    ctx.fillStyle = withAlpha(p.ink2, 0.22)
    for (let gx = Math.floor(x0 / gap) * gap; gx <= x1; gx += gap) {
      for (let gy = Math.floor(y0 / gap) * gap; gy <= y1; gy += gap) {
        ctx.fillRect(gx - r, gy - r, 2 * r, 2 * r)
      }
    }
    ctx.restore()
  }

  const drawLink = (obj: L, ctx: CanvasRenderingContext2D, scale: number): void => {
    const s = obj.source as N
    const t = obj.target as N
    if (typeof s !== 'object' || typeof t !== 'object') return
    const e = obj.edge
    const sx = s.x ?? 0
    const sy = s.y ?? 0
    const tx = t.x ?? 0
    const ty = t.y ?? 0
    const inFocus = view.focus.has(endId(obj.source)) && view.focus.has(endId(obj.target))
    const ring = view.ringEdges.has(e)
    const lit = ring && ringHeld
    const fade = fadeAlpha(obj.key)
    const base = inFocus ? 0.35 + 0.65 * e.rarity : 0.12
    const selected = selectedEdge === obj.key

    ctx.save()
    ctx.beginPath()
    ctx.moveTo(sx, sy)
    ctx.lineTo(tx, ty)
    // Weight drives thickness (contract). Kept in screen pixels so zoom does not bloat it.
    ctx.lineWidth = (0.75 + e.weight * 0.9) / scale
    if (lit) {
      ctx.shadowColor = p.hold
      ctx.shadowBlur = HALO
      ctx.strokeStyle = withAlpha(p.hold, fade)
    } else if (ring) {
      ctx.strokeStyle = withAlpha(p.navy, 0.85 * fade)
    } else {
      ctx.strokeStyle = withAlpha(inFocus ? p.navy : p.edge, base * fade)
    }
    ctx.stroke()
    if (agentFocus) {
      const a = endId(obj.source)
      const b = endId(obj.target)
      if ((agentFocus.ids.has(a) && agentFocus.near.has(b)) || (agentFocus.ids.has(b) && agentFocus.near.has(a)) || (agentFocus.ids.has(a) && agentFocus.ids.has(b))) {
        ctx.shadowBlur = 0
        ctx.globalAlpha = 0.9
        ctx.lineWidth = (2.25 + e.weight * 0.9) / scale
        ctx.strokeStyle = agentFocus.color
        ctx.stroke()
        ctx.globalAlpha = 1
      }
    }
    if (selected) {
      ctx.shadowBlur = 0
      ctx.lineWidth = (3 + e.weight * 0.9) / scale
      ctx.strokeStyle = withAlpha(p.navy, 0.6)
      ctx.stroke()
    }
    ctx.restore()

    // Edge label: shared kind and rarity, focus edges only (D-28).
    if (inFocus && fade > 0.5 && (scale >= 1.2 || selected)) {
      const label = `${entityKindLabel(e.entity_kind).toLowerCase()} · ${e.rarity.toFixed(2)}`
      const fs = EDGE_LABEL_PX / textScale(scale)
      ctx.font = `400 ${fs}px 'Inter Variable', ui-sans-serif, sans-serif`
      const mx = (sx + tx) / 2
      const my = (sy + ty) / 2
      const w = ctx.measureText(label).width + 6 / textScale(scale)
      ctx.fillStyle = withAlpha(p.surface, 0.85)
      ctx.fillRect(mx - w / 2, my - fs * 0.7, w, fs * 1.4)
      ctx.fillStyle = lit ? p.hold : p.ink2
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(label, mx, my)
    }
  }

  return (
    <div ref={wrapRef} className="absolute inset-0">
      {size.w > 0 && (
        <ForceGraph2D<NodeData, LinkData>
          ref={fgRef}
          width={size.w}
          height={size.h}
          graphData={data}
          backgroundColor={p.surface}
          nodeId="id"
          // No hover tooltips: force-graph writes them as HTML, and engine strings render as text only.
          nodeLabel={() => ''}
          linkLabel={() => ''}
          nodeCanvasObject={drawNode}
          nodePointerAreaPaint={paintNodeArea}
          linkCanvasObject={drawLink}
          linkCanvasObjectMode={() => 'replace'}
          linkHoverPrecision={6}
          autoPauseRedraw={false}
          cooldownTicks={120}
          onRenderFramePre={drawGrid}
          onZoom={({ k }) => onZoom?.(k)}
          onEngineStop={() => {
            if (needsFit.current && fgRef.current) {
              needsFit.current = false
              fit(fgRef.current)
            }
          }}
          onNodeClick={(n) => onSelect({ kind: 'node', id: String(n.id) })}
          onLinkClick={(l) => onSelect({ kind: 'edge', edge: l.edge })}
          onBackgroundClick={() => onSelect(null)}
        />
      )}
    </div>
  )
}
