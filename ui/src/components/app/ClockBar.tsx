import { useMemo, useState } from 'react'
import { ChevronsRight, Pause, Play, RotateCcw, Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useDealDetails, useDeals, useGraph } from '../../data/queries.ts'
import { clusterForDeal, latestHold } from '../../graph/graphview.ts'
import type { GGraph } from '../../graph/graphview.ts'
import { fmtSimTime } from '../../lib/format.ts'
import { buildHash } from '../../route.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { useUiStore } from '../../state/uiStore.ts'
import { nextEventAfter, simToMs } from '../../time/timeview.ts'
import { ErrorState, LoadingState } from './Primitives.tsx'

const HOLD_LINE_MS = 24 * 3600_000
const barClass = 'flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 border-b border-line bg-surface px-6 py-2'
const label = 'text-xs font-semibold uppercase tracking-wide text-ink-2'

function useBoardDetails() {
  const deals = useDeals()
  const folders = useMemo(() => (deals.data ?? []).map((d) => d.folder), [deals.data])
  return useDealDetails(folders)
}

// Deal search: typing filters the pipeline board; Enter or a click opens the matching deal.
function DealSearch() {
  const query = useUiStore((s) => s.query)
  const setQuery = useUiStore((s) => s.setQuery)
  const openDeal = useUiStore((s) => s.openDeal)
  const deals = useDeals()
  const { details } = useBoardDetails()
  const [open, setOpen] = useState(false)
  const q = query.trim().toLowerCase()
  const names = new Map(details.map((d) => [d.folder, d.legal_name ?? d.folder]))
  const matches =
    q === ''
      ? []
      : (deals.data ?? [])
          .map((d) => ({ folder: d.folder, name: names.get(d.folder) ?? d.legal_name ?? d.folder }))
          .filter((d) => d.folder.toLowerCase().includes(q) || d.name.toLowerCase().includes(q))
          .slice(0, 8)
  const jump = (folder: string): void => {
    setOpen(false)
    setQuery('')
    openDeal(folder)
  }
  return (
    <div className="relative w-72">
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-2" aria-hidden="true" />
      <Input
        type="text"
        role="combobox"
        aria-expanded={open && q !== ''}
        aria-label="Search deals"
        placeholder="Search deals"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && matches.length > 0) jump(matches[0].folder)
          if (e.key === 'Escape') setOpen(false)
        }}
        className="pr-8 pl-8"
      />
      {query !== '' && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => setQuery('')}
          className="absolute top-1/2 right-2 -translate-y-1/2 rounded-sm p-0.5 text-ink-2 hover:bg-subtle"
        >
          <X className="size-3.5" aria-hidden="true" />
        </button>
      )}
      {open && q !== '' && (
        <ul role="listbox" aria-label="Matching deals" className="absolute top-full right-0 left-0 z-20 mt-1 rounded-md border border-line bg-surface py-1 shadow-tooltip">
          {matches.length === 0 ? (
            <li className="px-3 py-1.5 text-sm text-ink-2">No deal matches “{query}”.</li>
          ) : (
            matches.map((m) => (
              <li key={m.folder}>
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => jump(m.folder)}
                  className="flex w-full flex-col px-3 py-1.5 text-left hover:bg-subtle"
                >
                  <span className="text-sm font-semibold text-ink">{m.name}</span>
                  {m.name !== m.folder && <span className="text-xs text-ink-2">{m.folder}</span>}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  )
}

// Top bar: deal search on the left, the sim clock and the latest hold line on the right.
export function ClockBar() {
  const { status, now, error, syncError, load } = useClockStore()
  const { details, isLoading } = useBoardDetails()
  const graph = useGraph()

  if (status === 'idle' || status === 'loading') {
    return (
      <div className={barClass}>
        <LoadingState>Loading clock.</LoadingState>
      </div>
    )
  }
  if (status === 'error') {
    return (
      <div className={barClass}>
        <ErrorState heading="Could not load the clock." message={error} onRetry={() => void load()} />
      </div>
    )
  }

  const nowMs = simToMs(now)
  const hold = isLoading ? null : latestHold(details, nowMs)
  const freshHold = hold !== null && nowMs - hold.ms <= HOLD_LINE_MS ? hold : null
  const holdCluster =
    freshHold && graph.data ? (clusterForDeal(graph.data as GGraph, freshHold.folder)?.id ?? null) : null

  return (
    <div className={barClass}>
      <DealSearch />
      {freshHold !== null && (
        <p key={freshHold.folder} role="status" className="flex animate-fade-in flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-semibold text-hold">Held {fmtSimTime(freshHold.at)}</span>
          <span className="text-ink">
            {freshHold.name}, linked to {freshHold.linked} {freshHold.linked === 1 ? 'deal' : 'deals'}
          </span>
          {holdCluster !== null && (
            <a href={buildHash({ view: 'graph', clusterId: holdCluster })} className="font-semibold text-hold underline-offset-2 hover:underline">
              Investigate ring →
            </a>
          )}
        </p>
      )}
      <div className="ml-auto flex items-baseline gap-2">
        <span className={label}>Sim time (CT)</span>
        <span className="text-base font-semibold text-navy tabular-nums">{fmtSimTime(now)}</span>
      </div>
      {syncError !== '' && (
        <p className="basis-full text-right text-xs text-ink-2" title={syncError}>
          The clock could not be set on the server. The screen shows {fmtSimTime(now)}.
        </p>
      )}
    </div>
  )
}

// The rail's Run group: every control updates the client clock first (clamped), then the store
// confirms an absolute target to the API (D-21).
export function ClockControls({ collapsed }: { collapsed: boolean }) {
  const { status, now, start, end, playing, advanceHours, goTo, replay, play, pause } = useClockStore()
  const { details, isLoading } = useBoardDetails()
  const [draft, setDraft] = useState<string | null>(null)
  const [rangeMessage, setRangeMessage] = useState(false)
  if (status !== 'ready') return null

  const nowMs = simToMs(now)
  const atEnd = nowMs >= simToMs(end)
  const nextEvent = isLoading ? null : nextEventAfter(details, nowMs)
  const value = draft ?? now.slice(0, 16)
  const submitGoTo = (): void => {
    const sim = `${value}-06:00`
    let ok = false
    try {
      const ms = simToMs(sim)
      ok = ms >= simToMs(start) && ms <= simToMs(end)
    } catch {
      ok = false
    }
    if (!ok) {
      setRangeMessage(true)
      return
    }
    setRangeMessage(false)
    setDraft(null)
    goTo(sim)
  }

  const btn = 'w-full justify-start border-surface/25 bg-transparent text-surface hover:bg-surface/10 disabled:border-surface/15 disabled:bg-transparent disabled:text-surface/50'
  const icon = 'size-8 border-surface/25 bg-transparent text-surface hover:bg-surface/10 disabled:border-surface/15 disabled:bg-transparent disabled:text-surface/50'

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-2 px-2">
        {playing ? (
          <Button variant="outline" size="icon" className={icon} aria-label="Pause" title="Pause" onClick={pause}>
            <Pause aria-hidden="true" />
          </Button>
        ) : (
          <Button variant="outline" size="icon" className={icon} aria-label="Play" title="Play" disabled={atEnd} onClick={play}>
            <Play aria-hidden="true" />
          </Button>
        )}
        <Button
          variant="outline"
          size="icon"
          className={icon}
          aria-label="Next event"
          title={nextEvent === null ? 'No later event' : 'Next event'}
          disabled={nextEvent === null}
          onClick={() => nextEvent !== null && goTo(nextEvent)}
        >
          <ChevronsRight aria-hidden="true" />
        </Button>
        <Button size="icon" className="size-8 bg-sky text-navy hover:bg-sky/85" aria-label="Replay from start" title="Replay from start" onClick={replay}>
          <RotateCcw aria-hidden="true" />
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 px-3">
      <div className="grid grid-cols-3 gap-1">
        {[
          [1, '+1h'],
          [6, '+6h'],
          [24, '+1 day'],
        ].map(([h, text]) => (
          <Button key={h} variant="outline" size="sm" className="border-surface/25 bg-transparent px-1 text-surface hover:bg-surface/10 disabled:border-surface/15 disabled:bg-transparent disabled:text-surface/50" disabled={atEnd} onClick={() => advanceHours(h as number)}>
            {text}
          </Button>
        ))}
      </div>
      <Button variant="outline" size="sm" className={btn} disabled={nextEvent === null} onClick={() => nextEvent !== null && goTo(nextEvent)}>
        <ChevronsRight aria-hidden="true" />
        Next event
      </Button>
      {nextEvent === null && !isLoading && <span className="text-xs text-surface/75">No later event</span>}
      {playing ? (
        <Button variant="outline" size="sm" className={btn} onClick={pause}>
          <Pause aria-hidden="true" />
          Pause
        </Button>
      ) : (
        <Button variant="outline" size="sm" className={btn} disabled={atEnd} onClick={play}>
          <Play aria-hidden="true" />
          Play
        </Button>
      )}
      <Button size="sm" onClick={replay} className="w-full justify-start bg-sky font-bold text-navy hover:bg-sky/85 focus-visible:outline-sky">
        <RotateCcw aria-hidden="true" />
        Replay from start
      </Button>
      <label className="flex flex-col gap-1">
        <span className="text-xs font-semibold tracking-wide text-surface/75 uppercase">Go to</span>
        <Input
          type="datetime-local"
          step={60}
          min={start.slice(0, 16)}
          max={end.slice(0, 16)}
          value={value}
          onChange={(e) => setDraft(e.target.value)}
          className="w-full bg-surface text-xs"
        />
      </label>
      <Button variant="outline" size="sm" className={btn} onClick={submitGoTo}>
        Go
      </Button>
      {rangeMessage && (
        <span className="text-xs text-surface/75">
          Enter a time between {fmtSimTime(start)} and {fmtSimTime(end)}.
        </span>
      )}
    </div>
  )
}
