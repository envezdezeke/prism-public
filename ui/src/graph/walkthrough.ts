// A replayable investigation trace built from the engine's own graph evidence, for any cluster that
// has no recorded Nemotron run (another ring, an uploaded data set, Nemotron switched off).
// Pure and deterministic: same graph in, same steps out. Labelled mode "off" so the screen says it
// is a walk-through, not a Nemotron run.
import type { InvestigatorTrace, TraceStep } from '../api/types.ts'
import { FAMILIES, entityKindLabel } from '../labels.ts'
import type { GCluster, GGraph } from './graphview.ts'

const MAX_DEALS = 3
const MAX_ENTITIES = 3
const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`

export function walkthroughTrace(graph: GGraph, cluster: GCluster): InvestigatorTrace {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const ring = new Set(cluster.deals)
  const label = (id: string): string => byId.get(id)?.label ?? id
  const fired = FAMILIES.filter((f) => cluster.families[f.id]?.fired)
  const steps: TraceStep[] = []
  const add = (tool: string, args: Record<string, unknown>, result: string): void => {
    steps.push({ step: steps.length + 1, thought: `Calling ${tool}.`, tool, args, result_summary: result })
  }

  // Entities shared by two or more ring members, rarest first.
  const users = new Map<string, Set<string>>()
  for (const e of graph.edges) {
    if (!ring.has(e.source)) continue
    if (!users.has(e.target)) users.set(e.target, new Set())
    users.get(e.target)?.add(e.source)
  }
  const rarity = new Map<string, number>()
  for (const e of graph.edges) if (ring.has(e.source)) rarity.set(e.target, Math.max(rarity.get(e.target) ?? 0, e.rarity))
  const shared = [...users.entries()]
    .filter(([, s]) => s.size >= 2)
    .sort((a, b) => (rarity.get(b[0]) ?? 0) - (rarity.get(a[0]) ?? 0))

  const current = cluster.deals.filter((d) => byId.get(d)?.type === 'deal')
  const history = cluster.deals.filter((d) => byId.get(d)?.type === 'history_deal')

  add(
    'cluster_summary',
    { cluster_id: cluster.id },
    `${cluster.id}: ${cluster.tier}, ${plural(current.length, 'current deal', 'current deals')}, ${plural(history.length, 'history deal', 'history deals')}, ${plural(shared.length, 'shared value', 'shared values')}; fired: ${fired.map((f) => f.label.toLowerCase()).join(', ') || 'none'}.`,
  )

  for (const d of current.slice(0, MAX_DEALS)) {
    const mine = shared.filter(([, s]) => s.has(d))
    const kinds = [...new Set(mine.map(([id]) => entityKindLabel(byId.get(id)?.kind).toLowerCase()))]
    add('deal_entities', { deal: d }, `${label(d)}: shares ${plural(mine.length, 'value', 'values')} with the ring${kinds.length ? ` (${kinds.join(', ')})` : ''}.`)
  }

  for (const [id, s] of shared.slice(0, MAX_ENTITIES)) {
    const n = byId.get(id)
    const ev = graph.edges.find((e) => e.target === id && ring.has(e.source))?.evidence
    add(
      'entity_deals',
      { kind: n?.kind ?? '', value: n?.label ?? id },
      `${entityKindLabel(n?.kind)} ${label(id)}: used by ${[...s].map(label).join(', ')}; rarity ${(rarity.get(id) ?? 0).toFixed(2)}.${ev ? ` ${ev}` : ''}`,
    )
  }

  for (const h of history.slice(0, 2)) {
    add('history_deal', { deal_id: h }, `${label(h)}: a prior deal on file that shares values with this ring.`)
  }

  const evidence = fired.map((f) => `${f.label}: ${cluster.families[f.id]?.evidence}`).join(' ')
  return {
    cluster_id: cluster.id,
    steps,
    case_summary: `${current.map(label).join(', ')} ${current.length === 1 ? 'is' : 'are'} linked through ${plural(shared.length, 'shared value', 'shared values')}${history.length ? ` and ${plural(history.length, 'prior deal', 'prior deals')}` : ''}. ${evidence || 'No voting family fired.'} ${cluster.tier === 'hold' ? 'The hold comes from the deterministic families, not from this walk-through.' : 'This ring is on watch only: not enough independent signals to hold.'}`,
    mode: 'off',
  }
}
