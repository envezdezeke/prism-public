import { useMemo, useState } from 'react'
import { describeError } from '../../api/client.ts'
import { STAGES } from '../../api/types.ts'
import type { DealDetail, Stage } from '../../api/types.ts'
import { useDealDetails, useDeals, useGraph } from '../../data/queries.ts'
import { clusterForDeal } from '../../graph/graphview.ts'
import type { GGraph } from '../../graph/graphview.ts'
import { buildHash } from '../../route.ts'
import { fmtHours, fmtMoney, fmtPct, fmtSimTime, fmtSla } from '../../lib/format.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { boardAt, simToMs, sortBoardRows } from '../../time/timeview.ts'
import type { BoardRow } from '../../time/timeview.ts'
import { NeededByBadge, SlaBar, StatusBadge } from './Badges.tsx'
import { FastForward, Inbox, Network, SearchX, X } from 'lucide-react'
import { Caption, DealLink, ErrorState, IconEmptyState, LoadingState, NullValue, PageHeader } from './Primitives.tsx'
import { Button } from '@/components/ui/button'
import { useUiStore } from '../../state/uiStore.ts'

function DealCard({ row, clusterId, pulse }: { row: BoardRow<DealDetail>; clusterId: string | null; pulse: boolean }) {
  const { deal } = row
  const name = deal.legal_name ?? deal.folder
  // Amber follows the displayed value: above 100 percent once rounded.
  const over = Math.round(row.slaFraction * 100) > 100
  const showFolder = deal.legal_name != null && deal.legal_name !== deal.folder
  const slaText = fmtSla(row.slaFraction, deal.sla_hours)
  const linked = row.held ? (deal.fraud.linked_deals ?? []).length : 0
  // Held cards carry a red left edge (D-31) and pulse once when the hold lands during this session.
  const frame = row.held ? 'border-l-2 border-l-hold' : ''
  return (
    <li className={`flex flex-col overflow-hidden rounded-md border border-line bg-surface ${frame} ${pulse ? 'animate-hold-pulse' : ''}`}>
      <DealLink
        folder={deal.folder}
        className="flex flex-col gap-1 px-3 py-2 text-ink transition-colors duration-150 hover:bg-subtle"
      >
        <div className="flex items-start justify-between gap-2">
          <span className="line-clamp-2 min-w-0 text-sm font-semibold" title={name}>
            {name}
          </span>
          <span className="shrink-0 text-right text-sm tabular-nums">
            {deal.amount_requested == null ? <NullValue /> : fmtMoney(deal.amount_requested)}
          </span>
        </div>
        {showFolder && (
          <span className="truncate text-xs text-ink-2" title={deal.folder}>
            {deal.folder}
          </span>
        )}
        <div className="flex flex-wrap items-start gap-1">
          <StatusBadge
            held={row.held}
            watch={deal.watch}
            reason={row.held ? deal.fraud.reason : null}
            cleared={row.stage === 'Closed'}
          />
          <NeededByBadge badge={deal.needed_by_badge} neededBy={deal.needed_by ?? null} />
        </div>
        <span
          className={`text-xs tabular-nums ${over ? 'text-risk' : 'text-ink-2'}`}
          title={row.held ? 'SLA clock stopped at the hold' : undefined}
        >
          {slaText}
        </span>
        <SlaBar pct={row.slaFraction} label={`SLA ${fmtPct(row.slaFraction)} of the stage limit`} />
      </DealLink>
      {row.held && (
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1 border-t border-line px-3 py-2 text-xs whitespace-nowrap">
          <span className="text-ink-2 tabular-nums">
            Linked to {linked} {linked === 1 ? 'deal' : 'deals'}
          </span>
          {clusterId !== null && (
            <a href={buildHash({ view: 'graph', clusterId })} className="font-semibold text-hold underline-offset-2 hover:underline">
              Investigate ring →
            </a>
          )}
        </div>
      )}
    </li>
  )
}

// Folders that became held while this board was watching: they pulse once (D-31). A deal already
// held when the board first renders does not pulse, and leaving the hold (scrubbing back) resets it.
function useHoldPulse(rows: BoardRow<DealDetail>[]): Set<string> {
  const [state, setState] = useState<{ seen: Set<string> | null; pulsed: Set<string> }>({ seen: null, pulsed: new Set() })
  const held = new Set(rows.filter((r) => r.held).map((r) => r.deal.folder))
  const key = [...held].sort().join('\u0000')
  const prevKey = state.seen === null ? null : [...state.seen].sort().join('\u0000')
  if (rows.length > 0 && key !== prevKey) {
    const pulsed = new Set([...state.pulsed].filter((f) => held.has(f)))
    if (state.seen !== null) for (const f of held) if (!state.seen.has(f)) pulsed.add(f)
    setState({ seen: held, pulsed })
  }
  return state.pulsed
}

// The stage limit is shown only when every deal in the column shares it (UI-SPEC row 8).
function stageLimit(col: BoardRow<DealDetail>[]): string | null {
  if (col.length === 0) return null
  const first = col[0].deal.sla_hours
  return col.every((r) => r.deal.sla_hours === first) ? fmtHours(first) : null
}

export function BoardView({
  rows,
  now,
  failedCount,
  onRetryFailed,
  clusterOf = () => null,
  pulsed = new Set<string>(),
  filter = null,
}: {
  filter?: { query: string; total: number; clear: () => void } | null
  rows: BoardRow<DealDetail>[]
  now: string
  failedCount: number
  onRetryFailed: () => void
  clusterOf?: (folder: string) => string | null
  pulsed?: Set<string>
}) {
  const when = fmtSimTime(now)
  const sorted = sortBoardRows(rows)
  const end = useClockStore((s) => s.end)
  const advanceHours = useClockStore((s) => s.advanceHours)
  const atEnd = end !== '' && simToMs(now) >= simToMs(end)
  const advance = (): void => advanceHours(24)
  const byStage = (stage: Stage) => sorted.filter((r) => r.stage === stage)

  return (
    <section aria-label="Pipeline board" className="flex flex-col gap-2">
      <PageHeader
        title="Pipeline"
        crumbs={['Pipeline', 'Board']}
        toolbar={
          <>
            <Button variant="outline" size="sm" onClick={advance} disabled={atEnd}>
              <FastForward aria-hidden="true" />
              Advance 1 day
            </Button>
            <a href={buildHash({ view: 'graph', clusterId: null })} className="inline-flex h-7 items-center gap-1 rounded-sm border border-primary bg-surface px-2 text-sm font-semibold text-primary hover:bg-subtle">
              <Network className="size-4" aria-hidden="true" />
              Fraud graph
            </a>
            {filter && (
              <span className="flex items-center gap-2 text-sm text-ink">
                Showing {rows.length} of {filter.total} matching “{filter.query}”
                <Button variant="outline" size="sm" onClick={filter.clear}>
                  <X aria-hidden="true" />
                  Clear filter
                </Button>
              </span>
            )}
            <Caption className="ml-auto">
              {rows.length} {rows.length === 1 ? 'deal' : 'deals'} as of {when} · As of end of run: amount, needed-by and Watch
            </Caption>
          </>
        }
      />
      {failedCount > 0 && (
        <p className="text-sm text-ink">
          {failedCount} {failedCount === 1 ? 'deal' : 'deals'} could not be loaded.{' '}
          <Button variant="link" size="sm" className="h-auto px-0" onClick={onRetryFailed}>
            Retry
          </Button>
        </p>
      )}
      {rows.length === 0 && filter && (
        <IconEmptyState icon={SearchX} heading="No deals match your search." action={{ label: 'Clear filter', onClick: filter.clear }}>
          Nothing on the board matches “{filter.query}” as of {when}.
        </IconEmptyState>
      )}
      {rows.length === 0 && !filter && (
        <IconEmptyState icon={Inbox} heading="No deals have arrived yet." action={atEnd ? undefined : { label: 'Advance 1 day', onClick: advance }}>
          No deals have arrived as of {when}. Advance the clock to bring files in.
        </IconEmptyState>
      )}
      <div className="overflow-x-auto">
        <div className="grid grid-cols-[repeat(6,minmax(10rem,1fr))] gap-2">
          {STAGES.map((stage) => {
            const col = byStage(stage)
            const limit = stageLimit(col)
            return (
              <div key={stage} className="flex min-w-0 flex-col gap-2 bg-page">
                <h2 className="flex items-baseline justify-between gap-2 rounded-md border border-line bg-surface px-2.5 py-2 text-xs font-semibold uppercase tracking-wide text-ink">
                  <span>{stage}</span>
                  <span className="font-bold tabular-nums text-sky">
                    {col.length}
                    {limit ? ` · ${limit}` : ''}
                  </span>
                </h2>
                {col.length === 0 ? (
                  <Caption>
                    No deals in {stage} as of {when}.
                  </Caption>
                ) : (
                  <ul className="flex max-h-[70vh] flex-col gap-2 overflow-y-auto">
                    {col.map((r) => (
                      <DealCard key={r.deal.folder} row={r} clusterId={clusterOf(r.deal.folder)} pulse={pulsed.has(r.deal.folder)} />
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}

export function Board() {
  const now = useClockStore((s) => s.now)
  const deals = useDeals()
  const folders = useMemo(() => (deals.data ?? []).map((d) => d.folder), [deals.data])
  const { details, isLoading, errors, refetchFailed } = useDealDetails(folders)

  const query = useUiStore((s) => s.query)
  const setQuery = useUiStore((s) => s.setQuery)
  const allRows = useMemo(() => boardAt(details, simToMs(now)), [details, now])
  const q = query.trim().toLowerCase()
  const rows = q === '' ? allRows : allRows.filter((r) => r.deal.folder.toLowerCase().includes(q) || (r.deal.legal_name ?? '').toLowerCase().includes(q))
  const pulsed = useHoldPulse(isLoading ? [] : rows)
  const graph = useGraph()
  const clusterOf = (folder: string): string | null =>
    graph.data ? (clusterForDeal(graph.data as GGraph, folder)?.id ?? null) : null

  if (deals.isPending) return <LoadingState>Loading deals.</LoadingState>
  if (deals.isError) {
    return (
      <ErrorState heading="Could not load deals." message={describeError(deals.error)} onRetry={() => void deals.refetch()} />
    )
  }
  return (
    <>
      {isLoading && <LoadingState>Loading deals.</LoadingState>}
      <BoardView
        rows={rows}
        now={now}
        failedCount={errors.length}
        onRetryFailed={refetchFailed}
        clusterOf={clusterOf}
        pulsed={pulsed}
        filter={q === '' ? null : { query, total: allRows.length, clear: () => setQuery('') }}
      />
    </>
  )
}
