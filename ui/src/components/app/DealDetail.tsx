import { STAGES } from '../../api/types.ts'
import { useEffect } from 'react'
import { describeError } from '../../api/client.ts'
import type { DealDetail as DealDetailData, Fact } from '../../api/types.ts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useDealDetail, useDeals, useGraph } from '../../data/queries.ts'
import { clusterForDeal } from '../../graph/graphview.ts'
import type { GGraph } from '../../graph/graphview.ts'
import { buildHash } from '../../route.ts'
import { fmtDuration, fmtHours, fmtMoney, fmtPct, fmtSimTime } from '../../lib/format.ts'
import { alertLevelLabel, factFields, factValueText, isClearedAnswer, issueLabel, optionLabel, requestTypeLabel } from '../../labels.ts'
import { personLabel, roleForAlert, roleForRequest } from '../../people.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { useUiStore } from '../../state/uiStore.ts'
import { alertsAt, boardAt, heldAt, requestsAt, simToMs, stageLogAt } from '../../time/timeview.ts'
import type { BoardRow } from '../../time/timeview.ts'
import { StatusBadge } from './Badges.tsx'
import { ArrowLeft, Clock, Network } from 'lucide-react'
import { Caption, DealLink, EmptyState, ErrorState, IconEmptyState, LoadingState, NullValue, PageHeader, PipelineLink } from './Primitives.tsx'

const HOUR_MS = 3600_000

function Breadcrumb({ name }: { name: string | null }) {
  return (
    <nav aria-label="Breadcrumb" className="text-xs text-ink-2">
      <PipelineLink>Pipeline</PipelineLink>
      {name !== null && <span> / {name}</span>}
    </nav>
  )
}

const TOOL_LINK =
  'inline-flex h-7 items-center gap-1 rounded-sm border border-primary bg-surface px-2 text-sm font-semibold text-primary hover:bg-subtle'

function ArrivalEmpty({ name, when }: { name: string; when: string }) {
  const showPipeline = useUiStore((s) => s.showPipeline)
  return (
    <IconEmptyState icon={Clock} heading={`${name} has not arrived as of ${when}.`} action={{ label: 'Back to pipeline', onClick: showPipeline }}>
      Move the clock forward, or open a deal that is already on the board.
    </IconEmptyState>
  )
}

function Row({ label, children, title }: { label: string; children: React.ReactNode; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-xs font-semibold text-ink-2">{label}</dt>
      <dd className="min-w-0 text-right text-sm tabular-nums" title={title}>
        {children}
      </dd>
    </div>
  )
}

// Side panel. Stage, time in stage and SLA follow the clock; the rows below the caption are static (D-18).
function SummaryPanel({ row }: { row: BoardRow<DealDetailData> }) {
  const { deal } = row
  const over = Math.round(row.slaFraction * 100) > 100
  const rm = deal.rm == null ? null : personLabel(deal.rm, 'Relationship manager')
  return (
    <section aria-label="Summary" className="flex flex-col gap-2 rounded-md border border-line border-t-4 border-t-navy bg-surface p-4">
      <h2 className="text-base font-semibold text-ink">Summary</h2>
      <dl className="flex flex-col gap-2">
        <Row label="Stage">{row.stage}</Row>
        <Row label="Stage entered">{fmtSimTime(row.enteredAt)}</Row>
        <Row label="Time in stage">{fmtDuration(row.slaFraction * deal.sla_hours * HOUR_MS)}</Row>
        <Row label="SLA" title={row.held ? 'SLA clock stopped at the hold' : undefined}>
          <span className={over ? 'text-risk' : undefined}>
            {fmtPct(row.slaFraction)} of {fmtHours(deal.sla_hours)}
          </span>
        </Row>
      </dl>
      <Caption>As of end of run</Caption>
      <dl className="flex flex-col gap-2">
        <Row label="Needed by" title={deal.needed_by == null ? undefined : fmtSimTime(deal.needed_by)}>
          {deal.needed_by == null ? (
            <NullValue title="No date in the documents" />
          ) : (
            <>
              {fmtSimTime(deal.needed_by)}
              {deal.needed_by_badge === 'at_risk' && <span className="font-semibold text-risk"> · At risk</span>}
              {deal.needed_by_badge === 'overdue' && <span className="font-semibold text-risk"> · Overdue</span>}
            </>
          )}
        </Row>
        <Row label="Amount requested">{deal.amount_requested == null ? <NullValue /> : fmtMoney(deal.amount_requested)}</Row>
        <Row label="Relationship manager" title={rm?.title}>
          {rm === null ? <NullValue /> : rm.label}
        </Row>
      </dl>
    </section>
  )
}

function FactsSection({ deal }: { deal: DealDetailData }) {
  const facts = deal.facts as unknown as Record<string, Fact | null | undefined>
  const anyFact = factFields.some((f) => facts[f.key] != null)
  return (
    <section aria-label="Facts" className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <h2 className="text-base font-semibold text-ink">Facts</h2>
        <Caption>As of end of run</Caption>
      </div>
      {!anyFact ? (
        <EmptyState>No facts were extracted for this deal.</EmptyState>
      ) : (
        <div className="rounded-md border border-line bg-surface">
          <Table>
            <TableHeader className="bg-subtle [&_th]:text-xs [&_th]:font-semibold [&_th]:tracking-wide [&_th]:text-ink-2 [&_th]:uppercase">
              <TableRow className="border-navy hover:bg-transparent">
                <TableHead>Field</TableHead>
                <TableHead>Value</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Confidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {factFields.map(({ key, label }) => {
                const fact = facts[key]
                const money = key === 'amount_requested' || key === 'annual_revenue_stated'
                return (
                  <TableRow key={key}>
                    <TableCell className="text-xs font-semibold text-ink-2">{label}</TableCell>
                    {fact == null ? (
                      <>
                        <TableCell>
                          <NullValue />
                        </TableCell>
                        <TableCell>
                          <NullValue />
                        </TableCell>
                        <TableCell>
                          <NullValue />
                        </TableCell>
                      </>
                    ) : (
                      <>
                        <TableCell className={`tabular-nums${money ? ' text-right' : ''}`}>
                          {factValueText(key, fact.value)}
                        </TableCell>
                        <TableCell>
                          <Badge variant="chip">{fact.source_doc}</Badge>
                        </TableCell>
                        <TableCell className="tabular-nums">{fmtPct(fact.confidence)}</TableCell>
                      </>
                    )}
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  )
}

function IssueList({ issues, empty, muted }: { issues: string[]; empty: string; muted?: boolean }) {
  if (issues.length === 0) return <p className="text-sm text-ink-2">{empty}</p>
  return (
    <ul className="flex flex-col gap-1">
      {issues.map((code) => (
        <li key={code} className={`text-sm ${muted ? 'text-ink-2' : 'text-ink'}`}>
          {issueLabel(code)}
          <span className="text-xs text-ink-2"> · {code}</span>
        </li>
      ))}
    </ul>
  )
}

// Issues have no timestamps: they are the end-of-run lists (D-18).
function IssuesSection({ deal }: { deal: DealDetailData }) {
  return (
    <section aria-label="Issues" className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <h2 className="text-base font-semibold text-ink">Issues</h2>
        <Caption>As of end of run</Caption>
      </div>
      <IssueList issues={deal.issues} empty="No open issues." />
      <h3 className="pt-2 text-xs font-bold tracking-wide text-navy uppercase">Resolved</h3>
      <IssueList issues={deal.resolved_issues} empty="No resolved issues." muted />
    </section>
  )
}

const sentence = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1)

function StageLogSection({ deal, nowMs }: { deal: DealDetailData; nowMs: number }) {
  const entries = stageLogAt(deal.stage_log, nowMs)
  return (
    <section aria-label="Stage log" className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-ink">Stage log</h2>
      <ol className="flex flex-col gap-4 border-l-2 border-navy pl-4">
        {entries.map((e) => (
          <li key={`${e.stage}-${e.entered_at}`} className="flex flex-col gap-1">
            <div className="flex items-baseline gap-2">
              <h3 className="text-base font-bold text-navy">{e.stage}</h3>
              {e.open && <Caption>Current</Caption>}
            </div>
            <p className="text-sm tabular-nums text-ink-2">
              Entered {fmtSimTime(e.entered_at)} ·{' '}
              {e.exited_at == null
                ? `Open for ${fmtDuration(nowMs - simToMs(e.entered_at))}`
                : `Exited ${fmtSimTime(e.exited_at)}`}
            </p>
            <p className="text-base">
              <span className="font-semibold">Decision:</span>{' '}
              {e.decision == null ? <NullValue title="No decision recorded yet" /> : sentence(e.decision)}
            </p>
            <p className="text-base">
              <span className="font-semibold">Why:</span>{' '}
              {e.why === '' ? <NullValue title="Recorded when the stage exits" /> : e.why}
            </p>
          </li>
        ))}
      </ol>
    </section>
  )
}

function RequestsSection({ deal, nowMs, when }: { deal: DealDetailData; nowMs: number; when: string }) {
  const requests = requestsAt(deal.human_requests, nowMs)
  return (
    <section aria-label="Human requests" className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-ink">Human requests</h2>
      {requests.length === 0 ? (
        <p className="text-sm text-ink-2">No requests on this deal as of {when}.</p>
      ) : (
        <div className="rounded-md border border-line bg-surface">
          <Table>
            <TableHeader className="bg-subtle [&_th]:text-xs [&_th]:font-semibold [&_th]:tracking-wide [&_th]:text-ink-2 [&_th]:uppercase">
              <TableRow className="border-navy hover:bg-transparent">
                <TableHead>Sent</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>To</TableHead>
                <TableHead>Message</TableHead>
                <TableHead>Answer</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {requests.map((r) => {
                const who = personLabel(r.to, roleForRequest(r.type))
                return (
                  <TableRow key={`${r.type}-${r.at}`} className="align-top">
                    <TableCell className="whitespace-nowrap tabular-nums">{fmtSimTime(r.at)}</TableCell>
                    <TableCell className="whitespace-nowrap">{requestTypeLabel(r.type)}</TableCell>
                    <TableCell className="whitespace-nowrap" title={who.title}>
                      {who.label}
                    </TableCell>
                    <TableCell className="whitespace-normal">{r.message}</TableCell>
                    <TableCell className="tabular-nums">
                      {r.answer == null ? (
                        'Pending'
                      ) : (
                        <span className={isClearedAnswer(r.answer) ? 'text-ok' : undefined}>
                          {optionLabel(r.answer)}
                          {r.answered_at != null && ` · ${fmtSimTime(r.answered_at)}`}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  )
}

function AlertsSection({ deal, nowMs, when }: { deal: DealDetailData; nowMs: number; when: string }) {
  const alerts = alertsAt(deal.alerts, nowMs, 'oldest')
  return (
    <section aria-label="Alerts" className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-ink">Alerts</h2>
      {alerts.length === 0 ? (
        <p className="text-sm text-ink-2">No alerts on this deal as of {when}.</p>
      ) : (
        <div className="rounded-md border border-line bg-surface">
          <Table>
            <TableHeader className="bg-subtle [&_th]:text-xs [&_th]:font-semibold [&_th]:tracking-wide [&_th]:text-ink-2 [&_th]:uppercase">
              <TableRow className="border-navy hover:bg-transparent">
                <TableHead className="border-l-2 border-transparent">Time</TableHead>
                <TableHead>Level</TableHead>
                <TableHead>To</TableHead>
                <TableHead>Message</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {alerts.map((a) => {
                const who = personLabel(a.to, roleForAlert(a.level))
                const edge = a.level === 'escalation' ? 'border-risk' : 'border-transparent'
                return (
                  <TableRow key={`${a.at}-${a.level}`} className="align-top">
                    <TableCell className={`whitespace-nowrap border-l-2 tabular-nums ${edge}`}>{fmtSimTime(a.at)}</TableCell>
                    <TableCell className="whitespace-nowrap">{alertLevelLabel(a.level, a.message)}</TableCell>
                    <TableCell className="whitespace-nowrap" title={who.title}>
                      {who.label}
                    </TableCell>
                    <TableCell className="whitespace-normal">{a.message}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  )
}

// Sober on purpose (D-16): the red badge is the only red, and nothing is shown that the contract does not
// give. A held deal's reason, stage at flag and links appear only once the hold has happened at the clock time.
function FraudPanel({
  deal,
  now,
  boardFolders,
  clusterId,
}: {
  deal: DealDetailData
  now: string
  boardFolders: string[]
  clusterId: string | null
}) {
  const f = deal.fraud
  const held = heldAt(deal, simToMs(now))
  const linked = f.linked_deals ?? []
  return (
    <section aria-label="Fraud status" className="flex flex-col gap-2 rounded-md border border-line border-t-4 border-t-navy bg-surface p-4">
      <h2 className="text-base font-semibold text-ink">Fraud status</h2>
      {held ? (
        <>
          <StatusBadge held watch={false} />
          <p className="text-sm text-ink-2">
            Frozen at {f.stage_at_flag ?? 'its stage'}
            {f.flagged_at != null && ` as of ${fmtSimTime(f.flagged_at)}`}
          </p>
          {f.reason != null && <p className="text-base">{f.reason}</p>}
          {linked.length > 0 && (
            <div className="flex flex-col gap-1">
              <h3 className="text-xs font-bold tracking-wide text-navy uppercase">Linked</h3>
              <ul className="flex flex-col gap-1 text-sm">
                {linked.map((name) => (
                  <li key={name}>{boardFolders.includes(name) ? <DealLink folder={name}>{name}</DealLink> : name}</li>
                ))}
              </ul>
            </div>
          )}
          {clusterId !== null && (
            <a
              href={buildHash({ view: 'graph', clusterId })}
              className="pt-1 text-sm font-semibold text-navy underline-offset-2 hover:underline"
            >
              Investigate ring →
            </a>
          )}
        </>
      ) : deal.watch ? (
        <>
          <StatusBadge held={false} watch />
          <p className="text-sm text-ink-2">Shown for review. Not held.</p>
          <Caption>As of end of run</Caption>
        </>
      ) : f.flagged && !f.held ? (
        <>
          <Badge variant="outline">Flagged</Badge>
          <p className="text-sm text-ink-2">Flagged, not held.</p>
        </>
      ) : (
        <p className="text-sm text-ink-2">No hold or watch flag.</p>
      )}
    </section>
  )
}

// The screen as a pure function of (fetched detail, clock now): the same boardAt the board uses decides
// the stage, SLA and held state, so the two screens agree at every clock time and one clock change
// can never show a mixed state.
export function DealDetailView({
  detail,
  now,
  boardFolders,
  clusterId = null,
}: {
  detail: DealDetailData
  now: string
  boardFolders: string[]
  clusterId?: string | null
}) {
  const name = detail.legal_name ?? detail.folder
  const nowMs = simToMs(now)
  const when = fmtSimTime(now)
  const row = boardAt([detail], nowMs)[0]
  const showFolder = detail.legal_name != null && detail.legal_name !== detail.folder

  if (row === undefined) {
    return (
      <div className="flex flex-col gap-4">
        <Breadcrumb name={name} />
        <ArrivalEmpty name={name} when={fmtSimTime(now)} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title={name}
        crumbs={['Pipeline', 'Deal', detail.folder]}
        toolbar={
          <>
            <PipelineLink className={TOOL_LINK}>
              <ArrowLeft className="size-4" aria-hidden="true" />
              Back to pipeline
            </PipelineLink>
            {clusterId !== null && (
              <a href={buildHash({ view: 'graph', clusterId })} className={TOOL_LINK}>
                <Network className="size-4" aria-hidden="true" />
                Open in fraud graph
              </a>
            )}
          </>
        }
      >
        {showFolder && <p className="pl-4 text-sm text-ink-2">{detail.folder}</p>}
        <div className="flex flex-wrap items-center gap-2 pl-4">
          <Badge variant="secondary">{row.stage}</Badge>
          <StatusBadge
            held={row.held}
            watch={detail.watch}
            reason={row.held ? detail.fraud.reason : null}
            cleared={row.stage === 'Closed'}
          />
        </div>
        <StageStepper stage={row.stage} held={row.held} />
      </PageHeader>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex min-w-0 flex-col gap-8">
          <FactsSection deal={detail} />
          <IssuesSection deal={detail} />
          <StageLogSection deal={detail} nowMs={nowMs} />
          <RequestsSection deal={detail} nowMs={nowMs} when={when} />
          <AlertsSection deal={detail} nowMs={nowMs} when={when} />
        </div>
        <div className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
          <SummaryPanel row={row} />
          <FraudPanel deal={detail} now={now} boardFolders={boardFolders} clusterId={clusterId} />
        </div>
      </div>
    </div>
  )
}

export function DealDetail({ folder }: { folder: string }) {
  const now = useClockStore((s) => s.now)
  const showPipeline = useUiStore((s) => s.showPipeline)
  const q = useDealDetail(folder)
  const deals = useDeals()
  const graph = useGraph()
  const name = q.data ? (q.data.legal_name ?? q.data.folder) : null

  useEffect(() => {
    document.title = `${name ?? folder} · Prism`
  }, [name, folder])

  if (q.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <Breadcrumb name={null} />
        <LoadingState>Loading deal.</LoadingState>
      </div>
    )
  }
  if (q.isError) {
    return (
      <div className="flex flex-col gap-4">
        <Breadcrumb name={null} />
        <ErrorState heading="Could not load this deal." message={describeError(q.error)} onRetry={() => void q.refetch()} />
        <div>
          <Button variant="outline" size="sm" onClick={showPipeline}>
            Back to pipeline
          </Button>
        </div>
      </div>
    )
  }
  const clusterId = graph.data ? (clusterForDeal(graph.data as GGraph, folder)?.id ?? null) : null
  return (
    <DealDetailView detail={q.data} now={now} boardFolders={(deals.data ?? []).map((d) => d.folder)} clusterId={clusterId} />
  )
}

// Where this loan sits in the six-stage flow: done stages filled, the current one ringed.
function StageStepper({ stage, held }: { stage: string; held: boolean }) {
  const idx = STAGES.indexOf(stage as (typeof STAGES)[number])
  return (
    <ol aria-label="Stage progress" className="flex items-center gap-0 pl-4 pt-1">
      {STAGES.map((s, i) => {
        const done = i < idx
        const cur = i === idx
        return (
          <li key={s} className="flex flex-1 items-center last:flex-none">
            <span className="flex flex-col items-center gap-1">
              <span
                aria-current={cur ? 'step' : undefined}
                className={`flex size-6 items-center justify-center rounded-full text-xs font-semibold ${
                  cur ? (held ? 'border-2 border-hold text-hold' : 'bg-navy text-surface ring-4 ring-subtle') : done ? 'bg-ink text-surface' : 'border border-line text-ink-2'
                }`}
              >
                {i + 1}
              </span>
              <span className={`text-xs ${cur ? 'font-semibold text-ink' : 'text-ink-2'}`}>{s}{cur && held ? ' · held' : ''}</span>
            </span>
            {i < STAGES.length - 1 && <span className={`mx-2 mb-5 h-0.5 flex-1 rounded-full ${done ? 'bg-ink' : 'bg-line'}`} />}
          </li>
        )
      })}
    </ol>
  )
}
