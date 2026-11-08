import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Bot, Crosshair, Maximize2, Minus, Network, Plus } from 'lucide-react'
import { describeError } from '../../api/client.ts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useDealDetails, useDeals, useGraph, useInvestigator } from '../../data/queries.ts'
import { AgentReplay } from './AgentReplay.tsx'
import { walkthroughTrace } from '../../graph/walkthrough.ts'
import { clusterHoldAt, defaultClusterId, graphAt } from '../../graph/graphview.ts'
import type { GGraph } from '../../graph/graphview.ts'
import { readPalette } from '../../graph/palette.ts'
import type { GraphPalette } from '../../graph/palette.ts'
import { fmtSimTime } from '../../lib/format.ts'
import { buildHash } from '../../route.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { useUiStore } from '../../state/uiStore.ts'
import { simToMs } from '../../time/timeview.ts'
import { EvidenceDrawer } from './EvidenceDrawer.tsx'
import { GraphCanvas } from './GraphCanvas.tsx'
import type { AgentFocus, GraphControls, Selection } from './GraphCanvas.tsx'
import { Caption, ErrorState, IconEmptyState, LoadingState, PageHeader } from './Primitives.tsx'

// The graph shares the light navy/sky palette with every other tab; the canvas reads it from the tokens.
function useInvestigationMode(): GraphPalette | null {
  const [palette, setPalette] = useState<GraphPalette | null>(null)
  useLayoutEffect(() => {
    const root = document.documentElement
    setPalette(readPalette(root))
  }, [])
  return palette
}

function Legend() {
  const item = 'flex items-center gap-2'
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      <li className={item}>
        <span aria-hidden="true" className="size-2.5 rounded-full bg-graph-deal" />
        Deal
      </li>
      <li className={item}>
        <span aria-hidden="true" className="size-2.5 rounded-full border-2 border-graph-history" />
        Prior deal
      </li>
      <li className={item}>
        <span aria-hidden="true" className="size-2 rounded-full bg-graph-document" />
        Document party
      </li>
      <li className={item}>
        <span aria-hidden="true" className="size-2 rounded-full bg-graph-identity" />
        Identity
      </li>
      <li className={item}>
        <span aria-hidden="true" className="size-2 rounded-full bg-graph-money" />
        Money
      </li>
      <li className={item}>
        <span aria-hidden="true" className="h-0.5 w-4 bg-hold" />
        Held ring (rare shared value)
      </li>
    </ul>
  )
}

export function FraudGraphView({
  graph,
  clusterId,
  palette,
}: {
  graph: GGraph
  clusterId: string | null
  palette: GraphPalette | null
}) {
  const now = useClockStore((s) => s.now)
  const deals = useDeals()
  const boardFolders = useMemo(() => (deals.data ?? []).map((d) => d.folder), [deals.data])
  const { details } = useDealDetails(boardFolders)
  const [selection, setSelection] = useState<Selection>(null)
  const [fitMode, setFitMode] = useState<'focus' | 'all'>('focus')
  const [fitToken, setFitToken] = useState(0)
  const controls = useRef<GraphControls | null>(null)
  const [zoom, setZoom] = useState(1)
  const [replaying, setReplaying] = useState(false)
  const [agentFocus, setAgentFocus] = useState<AgentFocus | null>(null)
  // The replay takes over the window (and the browser's full screen when allowed). Esc exits.
  const startReplay = (): void => {
    setReplaying(true)
    try {
      void document.documentElement.requestFullscreen?.().catch(() => undefined)
    } catch {
      /* full screen refused: the in-app takeover still applies */
    }
  }
  const stopReplay = (): void => {
    setReplaying(false)
    setAgentFocus(null)
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined)
  }
  useEffect(() => {
    if (!replaying) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') stopReplay()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const activeId = clusterId ?? defaultClusterId(graph)
  const cluster = graph.clusters.find((c) => c.id === activeId) ?? null
  const nowMs = simToMs(now)
  const view = useMemo(() => graphAt(graph, details, nowMs, cluster), [graph, details, nowMs, cluster])
  const hold = cluster ? clusterHoldAt(cluster, details, nowMs) : { held: false, at: null }
  const ringDeals = useMemo(() => new Set(cluster?.deals ?? []), [cluster])
  const labels = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.label])), [graph])
  const nodeLabel = (id: string): string => labels.get(id) ?? id
  const trace = useInvestigator(activeId ?? null)
  // A recorded Nemotron run when one exists for this ring; otherwise a walk-through built from the
  // engine's own evidence, so the replay works for every ring and every data set.
  const recorded = trace.isSuccess && trace.data.steps.length > 0 ? trace.data : null
  const replayTrace = useMemo(
    () => recorded ?? (cluster && !trace.isPending ? walkthroughTrace(graph, cluster) : null),
    [recorded, cluster, graph, trace.isPending],
  )
  const hasTrace = replayTrace !== null && replayTrace.steps.length > 0

  // Auto-play the agent replay once per browser session, the first time a traced ring opens.
  useEffect(() => {
    if (!hasTrace) return
    let seen = false
    try {
      seen = sessionStorage.getItem('prism.agentReplaySeen') === '1'
      sessionStorage.setItem('prism.agentReplaySeen', '1')
    } catch {
      seen = false
    }
    if (!seen) {
      const t = window.setTimeout(() => setReplaying(true), 1200)
      return () => window.clearTimeout(t)
    }
  }, [hasTrace])

  useEffect(() => {
    document.title = 'Fraud graph · Prism'
  }, [])

  if (cluster === null && graph.clusters.length > 0 && clusterId !== null) {
    return (
      <ErrorState heading="This cluster is not in the run." message={`No cluster with id ${clusterId}.`} />
    )
  }

  const ringVisible = cluster?.deals.filter((d) => view.nodes.some((n) => n.id === d)) ?? []

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={cluster ? `Ring ${cluster.id}` : 'Fraud graph'}
        crumbs={['Fraud', 'Graph', ...(cluster ? [cluster.id] : [])]}
        toolbar={
          <>
            {hasTrace && (
              <Button size="sm" variant={replaying ? 'outline' : 'default'} onClick={() => (replaying ? stopReplay() : startReplay())}>
                <Bot aria-hidden="true" />
                {replaying ? 'Stop agent replay' : recorded ? 'Watch Nemotron investigate' : 'Walk through the evidence'}
              </Button>
            )}
            <Button
              variant={fitMode === 'focus' ? 'default' : 'outline'}
              size="sm"
              aria-pressed={fitMode === 'focus'}
              onClick={() => {
                setFitMode('focus')
                setFitToken((n) => n + 1)
              }}
            >
              <Crosshair aria-hidden="true" />
              Focus ring
            </Button>
            <Button
              variant={fitMode === 'all' ? 'default' : 'outline'}
              size="sm"
              aria-pressed={fitMode === 'all'}
              onClick={() => {
                setFitMode('all')
                setFitToken((n) => n + 1)
              }}
            >
              <Network aria-hidden="true" />
              Whole graph
            </Button>
            {graph.clusters.length > 1 && (
              <nav aria-label="Clusters" className="flex gap-2">
                {graph.clusters.map((c) => (
                  <a
                    key={c.id}
                    href={buildHash({ view: 'graph', clusterId: c.id })}
                    aria-current={c.id === activeId ? 'page' : undefined}
                    className={`inline-flex h-7 items-center rounded-sm border px-2 text-xs ${
                      c.id === activeId ? 'border-navy font-semibold text-navy' : 'border-line text-ink-2 hover:bg-subtle'
                    }`}
                  >
                    {c.id} · {c.tier}
                  </a>
                ))}
              </nav>
            )}
            <div role="group" aria-label="Zoom" className="ml-auto flex items-center gap-1">
              <Button variant="outline" size="icon" className="size-7" aria-label="Zoom in" title="Zoom in" onClick={() => controls.current?.zoomIn()}>
                <Plus aria-hidden="true" />
              </Button>
              <span className="w-12 text-center text-xs text-ink-2 tabular-nums" aria-live="polite">
                {Math.round(zoom * 100)}%
              </span>
              <Button variant="outline" size="icon" className="size-7" aria-label="Zoom out" title="Zoom out" onClick={() => controls.current?.zoomOut()}>
                <Minus aria-hidden="true" />
              </Button>
              <Button variant="outline" size="icon" className="size-7" aria-label="Fit to view" title="Fit to view" onClick={() => controls.current?.fit()}>
                <Maximize2 aria-hidden="true" />
              </Button>
            </div>
          </>
        }
      >
        {cluster && (
          <div className="flex flex-wrap items-center gap-2 pl-4">
            {hold.held ? (
              <span key="held" className="animate-fade-in">
                <Badge variant="hold">Held: fraud review</Badge>
              </span>
            ) : (
              <Badge variant="outline">{cluster.tier === 'watch' ? 'Watch' : cluster.tier === 'hold' ? 'Forming: not held yet' : 'No flag'}</Badge>
            )}
            <span className="text-sm text-ink-2">
              {hold.held && hold.at
                ? `Held since ${fmtSimTime(hold.at)} · ${ringVisible.length} of ${cluster.deals.length} ring deals on screen`
                : `${ringVisible.length} of ${cluster.deals.length} ring deals on screen as of ${fmtSimTime(now)}`}
            </span>
          </div>
        )}
      </PageHeader>

      <div className="flex h-[calc(100vh-14rem)] min-h-[32rem] gap-4">
        <div className={replaying ? 'fixed inset-0 z-50 overflow-hidden bg-surface' : 'relative min-w-0 flex-1 overflow-hidden rounded-md border border-line bg-surface'}>
          {palette && (
            <GraphCanvas
              view={view}
              ringDeals={ringDeals}
              ringHeld={hold.held}
              palette={palette}
              selection={selection}
              onSelect={setSelection}
              fitMode={fitMode}
              fitToken={fitToken}
              controlsRef={controls}
              onZoom={setZoom}
              agentFocus={replaying ? agentFocus : null}
            />
          )}
          {replaying && replayTrace && (
            <AgentReplay
              trace={replayTrace}
              nodes={view.nodes}
              edges={view.edges}
              clusterDeals={cluster?.deals ?? []}
              controls={controls}
              onClose={stopReplay}
              onFocus={setAgentFocus}
            />
          )}
          <div className={`pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-4 p-3 ${replaying ? 'invisible' : ''}`}>
            <Caption>
              {view.edges.length} shared values visible as of {fmtSimTime(now)}. Replay the clock to watch the ring form.
            </Caption>
          </div>
          <div className={`pointer-events-none absolute inset-x-0 bottom-0 flex flex-col gap-2 p-3 ${replaying ? 'hidden' : ''}`}>
            {view.backgroundDeals.length > 0 && (
              <p className="text-sm text-ink">
                <span className="font-semibold tabular-nums">{view.backgroundDeals.length}</span>{' '}
                {view.backgroundDeals.length === 1 ? 'deal is' : 'deals are'} outside this ring: not linked, not held.{' '}
                <span className="text-ink-2">{view.backgroundDeals.map(nodeLabel).join(', ')}</span>
              </p>
            )}
            <Legend />
          </div>
        </div>
        <EvidenceDrawer
          selection={selection}
          cluster={cluster}
          view={view}
          ringDeals={ringDeals}
          ringHeld={hold.held}
          boardFolders={boardFolders}
          nodeLabel={nodeLabel}
          onSelect={setSelection}
        />
      </div>
    </div>
  )
}

function GraphEmpty() {
  const showPipeline = useUiStore((s) => s.showPipeline)
  return (
    <IconEmptyState icon={Network} heading="No deals in the graph yet." action={{ label: 'Back to pipeline', onClick: showPipeline }}>
      The engine has not linked any deals in this run.
    </IconEmptyState>
  )
}

export function FraudGraph({ clusterId }: { clusterId: string | null }) {
  const palette = useInvestigationMode()
  const g = useGraph()
  if (g.isPending) return <LoadingState>Loading the fraud graph.</LoadingState>
  if (g.isError) {
    return <ErrorState heading="Could not load the fraud graph." message={describeError(g.error)} onRetry={() => void g.refetch()} />
  }
  if (g.data.nodes.length === 0) {
    return <GraphEmpty />
  }
  // Keyed by cluster: switching clusters starts with a clean drawer and a fresh fit.
  return <FraudGraphView key={clusterId ?? ''} graph={g.data as GGraph} clusterId={clusterId} palette={palette} />
}
