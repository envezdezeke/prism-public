// Stable node and link objects for the force layout. force-graph mutates these (x, y, and link ends),
// so reusing them across clock ticks keeps the picture still while new edges grow out of it.
import { edgeKey } from './graphview.ts'
import type { GEdge, GNode, GraphView } from './graphview.ts'

export interface LayoutNode {
  id: string
  node: GNode
  x?: number
  y?: number
}

export interface LayoutLink {
  key: string
  edge: GEdge
  source: string | LayoutNode
  target: string | LayoutNode
}

// Deterministic jitter from the id, so a replay seeds the same positions (UI_PLAYBOOK fixed seeding).
function jitter(id: string, salt: number): number {
  let h = 2166136261 ^ salt
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619)
  return ((h >>> 0) % 1000) / 1000 - 0.5
}

export class LayoutCache {
  private nodes = new Map<string, LayoutNode>()
  private links = new Map<string, LayoutLink>()
  // Real-time birth of each link, for the fade-in. 0 means it was there at first paint.
  private born = new Map<string, number>()
  private painted = false

  sync(view: GraphView): { nodes: LayoutNode[]; links: LayoutLink[] } {
    const liveNodes = new Set(view.nodes.map((n) => n.id))
    for (const k of [...this.nodes.keys()]) if (!liveNodes.has(k)) this.nodes.delete(k)
    const liveLinks = new Set(view.edges.map(edgeKey))
    for (const k of [...this.links.keys()]) {
      if (!liveLinks.has(k)) {
        this.links.delete(k)
        this.born.delete(k)
      }
    }

    const nodes = view.nodes.map((n) => {
      let obj = this.nodes.get(n.id)
      if (!obj) {
        obj = { id: n.id, node: n }
        // Seed next to a neighbour already on screen so the node grows out of the graph.
        const e = view.edges.find((x) => x.source === n.id || x.target === n.id)
        const other = e ? this.nodes.get(e.source === n.id ? e.target : e.source) : undefined
        if (other?.x !== undefined && other.y !== undefined) {
          obj.x = other.x + jitter(n.id, 1) * 24
          obj.y = other.y + jitter(n.id, 2) * 24
        }
        this.nodes.set(n.id, obj)
      }
      return obj
    })
    const links = view.edges.map((e) => {
      const key = edgeKey(e)
      let obj = this.links.get(key)
      if (!obj) {
        obj = { key, edge: e, source: e.source, target: e.target }
        this.links.set(key, obj)
        // -1: revealed by the clock, stamped at its first draw. 0: there at first paint, no fade.
        this.born.set(key, this.painted ? -1 : 0)
      }
      return obj
    })
    this.painted = true
    return { nodes, links }
  }

  // 0 to 1 opacity factor for a link that the clock just revealed.
  fade(key: string, nowReal: number, ms: number): number {
    let b = this.born.get(key) ?? 0
    if (b === -1) {
      b = nowReal
      this.born.set(key, b)
    }
    return b === 0 ? 1 : Math.min(1, (nowReal - b) / ms)
  }
}
