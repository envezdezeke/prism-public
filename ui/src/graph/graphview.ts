// Pure fraud-graph view functions (D-26 to D-30). No React, no canvas: the graph screen renders only
// what these return, so the clock rules are tested here with node --test.
import { simToMs } from '../time/timeview.ts'

export interface GNode {
  id: string
  type: 'deal' | 'history_deal' | 'entity'
  label: string
  kind?: string | null
}

export interface GEdge {
  source: string
  target: string
  entity_kind: string
  rarity: number
  weight: number
  first_visible_at: string
  evidence: string
}

export interface GCluster {
  id: string
  deals: string[]
  families: Record<string, { fired: boolean; evidence: string }>
  tier: 'hold' | 'watch' | 'none'
}

export interface GGraph {
  nodes: GNode[]
  edges: GEdge[]
  clusters: GCluster[]
}

// The slice of a deal detail the graph needs: arrival and hold time.
export interface DealClock {
  folder: string
  stage_log: { entered_at: string }[]
  fraud: { held: boolean; flagged_at?: string | null }
}

// Display rule, not an engine rule: an edge into an entity shared by two or more ring deals is lit
// in alarm red only when the entity is at least this rare. Common shared values (a local bank) stay
// neutral so the picture does not overstate the case. The engine's own counting is in the families.
export const RING_RARITY = 0.5

// The entity-kind groups that tint entity squares. Muted stone, navy, amber and green only (D-30).
export type KindGroup = 'document' | 'identity' | 'money'
export function kindGroup(kind: string | null | undefined): KindGroup {
  if (kind === 'preparer' || kind === 'pdf_producer') return 'document'
  if (kind === 'vendor' || kind === 'remit_account' || kind === 'bank') return 'money'
  return 'identity'
}

const safeMs = (s: string | null | undefined): number | null => {
  if (s == null) return null
  try {
    return simToMs(s)
  } catch {
    return null
  }
}

export function edgeVisibleAt(e: GEdge, t: number): boolean {
  const ms = safeMs(e.first_visible_at)
  return ms !== null && ms <= t
}

export function arrivedAt(d: DealClock, t: number): boolean {
  const first = d.stage_log[0]
  const ms = first ? safeMs(first.entered_at) : null
  return ms !== null && ms <= t
}

export function heldAtMs(d: DealClock, t: number): boolean {
  const ms = safeMs(d.fraud.flagged_at)
  return d.fraud.held && ms !== null && ms <= t
}

// The cluster turns red once its first deal is held at t (D-29). Watch and none tiers never do.
export function clusterHoldAt(c: GCluster, details: DealClock[], t: number): { held: boolean; at: string | null } {
  if (c.tier !== 'hold') return { held: false, at: null }
  let best: { ms: number; at: string } | null = null
  for (const d of details) {
    if (!c.deals.includes(d.folder) || !d.fraud.held) continue
    const ms = safeMs(d.fraud.flagged_at)
    if (ms === null || ms > t) continue
    if (best === null || ms < best.ms) best = { ms, at: d.fraud.flagged_at as string }
  }
  return best === null ? { held: false, at: null } : { held: true, at: best.at }
}

export function defaultClusterId(g: GGraph): string | null {
  return (g.clusters.find((c) => c.tier === 'hold') ?? g.clusters[0])?.id ?? null
}

export function clusterForDeal(g: GGraph, folder: string): GCluster | null {
  return g.clusters.find((c) => c.deals.includes(folder)) ?? null
}

export interface GraphView {
  nodes: GNode[]
  edges: GEdge[]
  // Ring deals plus one hop: the entities they touch and every deal touching those entities.
  focus: Set<string>
  // Entities shared by two or more ring deals and at least RING_RARITY rare.
  ringEntities: Set<string>
  ringEdges: Set<GEdge>
  // Deals on screen that are not in the focus: drawn dim, the visible proof of no false flags.
  backgroundDeals: string[]
}

export const edgeKey = (e: GEdge): string => `${e.source}\u0000${e.target}`

// Everything that exists at t. A deal node shows once it has arrived (or has a visible edge);
// history deals and entities show once an edge to them is visible.
export function graphAt(g: GGraph, details: DealClock[], t: number, cluster: GCluster | null): GraphView {
  const edges = g.edges.filter((e) => edgeVisibleAt(e, t))
  const touched = new Set<string>()
  for (const e of edges) {
    touched.add(e.source)
    touched.add(e.target)
  }
  const arrived = new Set(details.filter((d) => arrivedAt(d, t)).map((d) => d.folder))
  const nodes = g.nodes.filter((n) => touched.has(n.id) || (n.type === 'deal' && arrived.has(n.id)))

  const ringDeals = new Set(cluster?.deals ?? [])
  const focus = new Set<string>()
  const ringTouch = new Map<string, Set<string>>()
  for (const e of edges) {
    if (!ringDeals.has(e.source)) continue
    focus.add(e.source)
    focus.add(e.target)
    const s = ringTouch.get(e.target) ?? new Set<string>()
    s.add(e.source)
    ringTouch.set(e.target, s)
  }
  for (const e of edges) if (focus.has(e.target)) focus.add(e.source)
  for (const id of ringDeals) if (nodes.some((n) => n.id === id)) focus.add(id)

  const ringEntities = new Set<string>()
  const ringEdges = new Set<GEdge>()
  for (const e of edges) {
    const sharers = ringTouch.get(e.target)
    if (ringDeals.has(e.source) && sharers !== undefined && sharers.size >= 2 && e.rarity >= RING_RARITY) {
      ringEntities.add(e.target)
      ringEdges.add(e)
    }
  }
  const backgroundDeals = nodes.filter((n) => n.type === 'deal' && !focus.has(n.id)).map((n) => n.id)
  return { nodes, edges, focus, ringEntities, ringEdges, backgroundDeals }
}

// Deals (and history deals) that share one entity, rarest-first edges into it.
export function sharersOf(view: GraphView, entityId: string): GEdge[] {
  return view.edges.filter((e) => e.target === entityId).sort((a, b) => b.rarity - a.rarity)
}

export function edgesOfNode(view: GraphView, id: string): GEdge[] {
  return view.edges.filter((e) => e.source === id || e.target === id).sort((a, b) => b.rarity - a.rarity)
}

export interface RedteamTotals {
  attempts: number
  bypasses: number
  falseFlags: number
}

// Bypasses are synthetic fake deals the engine did not catch, summed over every trick.
export function redteamTotals(tricks: { n_deals: number; caught: number; false_flags: number }[]): RedteamTotals {
  let attempts = 0
  let bypasses = 0
  let falseFlags = 0
  for (const t of tricks) {
    attempts += t.n_deals
    bypasses += Math.max(0, t.n_deals - t.caught)
    falseFlags += t.false_flags
  }
  return { attempts, bypasses, falseFlags }
}

// The latest hold at or before t, for the clock-bar alert line (D-31).
export function latestHold(details: (DealClock & { legal_name?: string | null; fraud: DealClock['fraud'] & { linked_deals?: string[] } })[], t: number) {
  let best: { folder: string; name: string; at: string; ms: number; linked: number } | null = null
  for (const d of details) {
    if (!heldAtMs(d, t)) continue
    const ms = safeMs(d.fraud.flagged_at) as number
    if (best === null || ms > best.ms) {
      best = { folder: d.folder, name: d.legal_name ?? d.folder, at: d.fraud.flagged_at as string, ms, linked: d.fraud.linked_deals?.length ?? 0 }
    }
  }
  return best
}
