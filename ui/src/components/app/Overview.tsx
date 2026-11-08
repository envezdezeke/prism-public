import { useEffect, useMemo } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, ArrowRight, Bot, ChevronRight, Inbox as InboxIcon, Layers, Play, ShieldAlert } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { STAGES } from '../../api/types.ts'
import { useAlerts, useDealDetails, useDeals, useGraph, useInbox } from '../../data/queries.ts'
import type { GGraph } from '../../graph/graphview.ts'
import { requestTypeLabel } from '../../labels.ts'
import { fmtDuration, fmtMoney, fmtSimTime } from '../../lib/format.ts'
import { buildHash } from '../../route.ts'
import type { Route } from '../../route.ts'
import { useAnswersStore } from '../../state/answersStore.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { alertsAt, boardAt, inboxWithOverlayAt, simToMs } from '../../time/timeview.ts'
import { Caption, LoadingState, PageHeader } from './Primitives.tsx'

// Landing page: answers "where are the loans, and what do I do first?" at a glance.
// Display only; every number is a count over the same data the other screens show.

const STAGE_HINT: Record<string, string> = {
  Intake: 'Files arrive, facts are read',
  Credit: 'Credit officer decides',
  Committee: 'Committee approves',
  Product: 'Terms are set',
  Committed: 'Signed, waiting to fund',
  Closed: 'Funded',
}

function Kpi({ label, value, icon: Icon, link, route, tone }: {
  label: string
  value: ReactNode
  icon: LucideIcon
  link: string
  route: Route
  tone?: 'hold' | 'risk'
}) {
  return (
    <a
      href={buildHash(route)}
      className="group flex flex-col rounded-lg border border-line bg-surface transition-colors hover:border-ink-2"
    >
      <div className="flex items-start justify-between p-4 pb-2">
        <span className="text-sm text-ink">{label}</span>
        <span className="flex size-8 items-center justify-center rounded-md border border-line">
          <Icon className={`size-4 ${tone === 'hold' ? 'text-hold' : tone === 'risk' ? 'text-risk' : 'text-ink'}`} aria-hidden="true" />
        </span>
      </div>
      <span className="px-4 pb-3 text-title font-semibold tabular-nums text-ink">{value}</span>
      <span className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-ink-2 group-hover:text-ink">
        {link}
        <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
      </span>
    </a>
  )
}

function StartHere({ pending, held }: { pending: number; held: number }) {
  const play = useClockStore((s) => s.replay)
  const steps: { n: number; title: string; body: string; action: ReactNode }[] = [
    {
      n: 1,
      title: 'Run the week',
      body: 'Replay the clock and watch loan files arrive and move.',
      action: (
        <button type="button" onClick={() => void play()} className="inline-flex h-7 items-center gap-1 rounded-md bg-navy px-2.5 text-xs font-semibold text-surface hover:opacity-90">
          <Play className="size-3.5" aria-hidden="true" /> Replay from start
        </button>
      ),
    },
    {
      n: 2,
      title: 'Answer sign-offs',
      body: `${pending} ${pending === 1 ? 'request is' : 'requests are'} waiting on staff right now.`,
      action: <StepLink route={{ view: 'inbox' }}>Open inbox</StepLink>,
    },
    {
      n: 3,
      title: 'Investigate holds',
      body: held > 0 ? `${held} ${held === 1 ? 'deal is' : 'deals are'} held: see the ring and watch Nemotron work it.` : 'No deal is held yet at this time.',
      action: <StepLink route={{ view: 'graph', clusterId: null }}>Open fraud graph</StepLink>,
    },
  ]
  return (
    <section aria-label="Start here" className="rounded-lg border border-line bg-surface p-4">
      <h2 className="mb-3 text-base font-semibold text-ink">Start here</h2>
      <ol className="grid grid-cols-3 gap-4">
        {steps.map((s, i) => (
          <li key={s.n} className="relative flex gap-3">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-navy text-xs font-semibold text-surface">{s.n}</span>
            <div className="flex min-w-0 flex-col gap-1.5">
              <span className="text-sm font-semibold text-ink">{s.title}</span>
              <span className="text-xs text-ink-2">{s.body}</span>
              <span>{s.action}</span>
            </div>
            {i < steps.length - 1 && <ChevronRight className="absolute top-1 -right-3 size-4 text-ink-2" aria-hidden="true" />}
          </li>
        ))}
      </ol>
    </section>
  )
}

function StepLink({ route, children }: { route: Route; children: ReactNode }) {
  return (
    <a href={buildHash(route)} className="inline-flex h-7 items-center gap-1 rounded-md border border-line px-2.5 text-xs font-semibold text-ink hover:bg-subtle">
      {children} <ArrowRight className="size-3.5" aria-hidden="true" />
    </a>
  )
}

export function Overview() {
  const now = useClockStore((s) => s.now)
  const t = simToMs(now)
  const deals = useDeals()
  const folders = useMemo(() => (deals.data ?? []).map((d) => d.folder), [deals.data])
  const { details, isLoading } = useDealDetails(folders)
  const inbox = useInbox()
  const answers = useAnswersStore((s) => s.answers)
  const alerts = useAlerts()
  const graph = useGraph()

  useEffect(() => {
    document.title = 'Overview · Prism'
  }, [])

  const rows = useMemo(() => boardAt(details, t), [details, t])
  const pending = useMemo(
    () => (inbox.data ? inboxWithOverlayAt(inbox.data, answers, t).filter((i) => i.status === 'pending') : []),
    [inbox.data, answers, t],
  )
  const firedAlerts = useMemo(() => (alerts.data ? alertsAt(alerts.data, t) : []), [alerts.data, t])
  const held = rows.filter((r) => r.held)
  const inFlight = rows.filter((r) => r.stage !== 'Closed')
  const escalations = firedAlerts.filter((a) => a.level === 'escalation').length
  const names = new Map(details.map((d) => [d.folder, d.legal_name ?? d.folder]))
  const heldClusters = ((graph.data as GGraph | undefined)?.clusters ?? []).filter((c) => c.tier === 'hold')

  if (isLoading && rows.length === 0) return <LoadingState>Loading the overview.</LoadingState>

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Overview" crumbs={['Overview']}>
        <Caption>As of {fmtSimTime(now)}. Every loan moves left to right through six stages; red means held for fraud.</Caption>
      </PageHeader>

      <StartHere pending={pending.length} held={held.length} />

      <div className="grid grid-cols-4 gap-4">
        <Kpi label="Loans in flight" value={inFlight.length} icon={Layers} link="View pipeline" route={{ view: 'pipeline' }} />
        <Kpi label="Waiting on you" value={pending.length} icon={InboxIcon} link="Answer sign-offs" route={{ view: 'inbox' }} />
        <Kpi label="Held for fraud" value={held.length} icon={ShieldAlert} link="Investigate ring" route={{ view: 'graph', clusterId: null }} tone={held.length ? 'hold' : undefined} />
        <Kpi label="Alerts fired" value={firedAlerts.length} icon={AlertTriangle} link={`${escalations} escalated · view alerts`} route={{ view: 'alerts' }} tone={escalations ? 'risk' : undefined} />
      </div>

      {/* Pipeline strip: the six stages as a flow, each with its loans. */}
      <section aria-label="Pipeline flow" className="rounded-lg border border-line bg-surface p-4">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-base font-semibold text-ink">Where every loan is</h2>
          <a href={buildHash({ view: 'pipeline' })} className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-xs text-ink hover:bg-subtle">
            Open board <ArrowRight className="size-3.5" aria-hidden="true" />
          </a>
        </div>
        <ol className="grid grid-cols-6 gap-2">
          {STAGES.map((stage, i) => {
            const here = rows.filter((r) => r.stage === stage)
            const stuck = here.filter((r) => !r.held && r.slaFraction > 1).length
            const filled = here.length > 0
            return (
              <li key={stage} className="flex flex-col gap-2">
                <div className="flex items-center gap-1">
                  <span className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${filled ? 'bg-navy text-surface' : 'border border-line text-ink-2'}`}>
                    {i + 1}
                  </span>
                  <span className={`h-0.5 flex-1 rounded-full ${i < STAGES.length - 1 ? (filled ? 'bg-ink' : 'bg-line') : 'bg-transparent'}`} />
                </div>
                <div>
                  <p className="flex items-baseline justify-between text-sm font-semibold text-ink">
                    {stage}
                    <span className="text-xs font-normal tabular-nums text-ink-2">{here.length}</span>
                  </p>
                  <p className="text-xs text-ink-2">{STAGE_HINT[stage]}</p>
                  {stuck > 0 && <p className="text-xs font-semibold text-risk">{stuck} past SLA</p>}
                </div>
                <ul className="flex flex-col gap-1">
                  {here.slice(0, 4).map((r) => (
                    <li key={r.deal.folder}>
                      <a
                        href={buildHash({ view: 'deal', folder: r.deal.folder })}
                        className={`block truncate rounded-md border px-2 py-1 text-xs hover:bg-subtle ${r.held ? 'border-hold text-hold' : 'border-line text-ink'}`}
                        title={names.get(r.deal.folder)}
                      >
                        {names.get(r.deal.folder)}
                      </a>
                    </li>
                  ))}
                  {here.length > 4 && <li className="text-xs text-ink-2">+{here.length - 4} more</li>}
                </ul>
              </li>
            )
          })}
        </ol>
      </section>

      <div className="grid grid-cols-3 gap-4">
        <section aria-label="Needs you now" className="col-span-2 rounded-lg border border-line bg-surface">
          <div className="flex items-baseline justify-between p-4 pb-2">
            <h2 className="text-base font-semibold text-ink">Needs you now</h2>
            <StepLink route={{ view: 'inbox' }}>View all</StepLink>
          </div>
          {pending.length === 0 ? (
            <p className="px-4 pb-4 text-sm text-ink-2">No pending requests. Everything is waiting on borrowers or the clock.</p>
          ) : (
            <ul>
              {[...pending]
                .sort((a, b) => simToMs(a.at) - simToMs(b.at))
                .slice(0, 5)
                .map((it) => (
                  <li key={it.id} className="border-t border-line first:border-t-0">
                    <a href={buildHash({ view: 'inbox' })} className="flex items-center gap-3 px-4 py-3 hover:bg-subtle">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-line">
                        <InboxIcon className="size-4 text-ink" aria-hidden="true" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold text-ink">{names.get(it.deal) ?? it.deal}</span>
                        <span className="block text-xs text-ink-2">{requestTypeLabel(it.type)}</span>
                      </span>
                      <span className="text-right text-xs text-ink-2 tabular-nums">
                        waiting {fmtDuration(Math.max(0, t - simToMs(it.at)))}
                      </span>
                    </a>
                  </li>
                ))}
            </ul>
          )}
        </section>

        <section aria-label="Nemotron agents" className="flex flex-col rounded-lg border border-line bg-surface p-4">
          <h2 className="mb-1 flex items-center gap-2 text-base font-semibold text-ink">
            <Bot className="size-4" aria-hidden="true" /> Nemotron agents
          </h2>
          <p className="mb-3 text-xs text-ink-2">The investigator works each held ring and writes up the case. Advisory: the engine makes the hold.</p>
          {heldClusters.length === 0 ? (
            <p className="text-sm text-ink-2">No held ring to investigate yet.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {heldClusters.map((c) => (
                <li key={c.id}>
                  <a href={buildHash({ view: 'graph', clusterId: c.id })} className="group flex items-center gap-3 rounded-md border border-line px-3 py-2 hover:bg-subtle">
                    <span className="relative flex size-2.5">
                      <span className="absolute inline-flex size-full animate-ping rounded-full bg-agent-entities opacity-60" />
                      <span className="relative inline-flex size-2.5 rounded-full bg-agent-entities" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-semibold text-ink">Ring {c.id}</span>
                      <span className="block truncate text-xs text-ink-2">{c.deals.length} deals · {c.deals.map((d) => names.get(d) ?? d).join(', ')}</span>
                    </span>
                    <span className="text-xs font-semibold text-ink">Watch</span>
                    <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden="true" />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <Caption>Amounts: {fmtMoney(rows.reduce((s, r) => s + (r.deal.amount_requested ?? 0), 0))} requested across loans on screen.</Caption>
    </div>
  )
}
