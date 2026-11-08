import { useEffect, useState } from 'react'
import { Bell, BellOff, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useBoardDetails } from '../../data/queries.ts'
import { describeError } from '../../api/client.ts'
import type { AlertFeedItem } from '../../api/types.ts'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAlerts } from '../../data/queries.ts'
import { fmtSimTime } from '../../lib/format.ts'
import { alertLevelLabel } from '../../labels.ts'
import { personLabel, roleForAlert } from '../../people.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { alertsAt, nextEventAfter, simToMs } from '../../time/timeview.ts'
import { DealLink, ErrorState, IconEmptyState, LoadingState, PageHeader } from './Primitives.tsx'

// Newest first, only what has fired by the clock time. Escalations (150% and 200%) carry an amber edge.
export function AlertsFeedView({ alerts, now }: { alerts: AlertFeedItem[]; now: string }) {
  const [escOnly, setEscOnly] = useState(false)
  const goTo = useClockStore((s) => s.goTo)
  const { details } = useBoardDetails()
  const all = alertsAt(alerts, simToMs(now), 'newest')
  const visible = escOnly ? all.filter((a) => a.level === 'escalation') : all
  const nextEvent = nextEventAfter(details, simToMs(now))
  return (
    <section aria-label="Alerts" className="flex flex-col gap-4">
      <PageHeader
        title="Alerts"
        crumbs={['Pipeline', 'Alerts']}
        toolbar={
          <>
            <Button variant={escOnly ? 'outline' : 'default'} size="sm" aria-pressed={!escOnly} onClick={() => setEscOnly(false)}>
              <Bell aria-hidden="true" />
              All {all.length}
            </Button>
            <Button variant={escOnly ? 'default' : 'outline'} size="sm" aria-pressed={escOnly} onClick={() => setEscOnly(true)}>
              <TriangleAlert aria-hidden="true" />
              Escalations {all.filter((a) => a.level === 'escalation').length}
            </Button>
          </>
        }
      />
      {visible.length === 0 && escOnly && all.length > 0 ? (
        <IconEmptyState icon={TriangleAlert} heading="No escalations yet." action={{ label: 'Show all alerts', onClick: () => setEscOnly(false) }}>
          Escalations fire at 150% and 200% of a stage's limit. None had fired as of {fmtSimTime(now)}.
        </IconEmptyState>
      ) : visible.length === 0 ? (
        <IconEmptyState
          icon={BellOff}
          heading="No alerts yet."
          action={nextEvent === null ? undefined : { label: 'Jump to next event', onClick: () => goTo(nextEvent) }}
        >
          Alerts fire at 100% of a stage's limit and escalate at 150% and 200%. None had fired as of {fmtSimTime(now)}.
        </IconEmptyState>
      ) : (
        <div className="rounded-md border border-line bg-surface">
          <Table>
            <TableHeader className="bg-subtle [&_th]:text-xs [&_th]:font-semibold [&_th]:tracking-wide [&_th]:text-ink-2 [&_th]:uppercase">
              <TableRow className="border-navy hover:bg-transparent">
                <TableHead className="border-l-2 border-transparent">Time</TableHead>
                <TableHead>Level</TableHead>
                <TableHead>Deal</TableHead>
                <TableHead>Stage</TableHead>
                <TableHead>To</TableHead>
                <TableHead>Message</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visible.map((a) => {
                const who = personLabel(a.to, roleForAlert(a.level))
                const edge = a.level === 'escalation' ? 'border-risk' : 'border-transparent'
                return (
                  <TableRow key={a.id} className="animate-fade-in align-top">
                    <TableCell className={`whitespace-nowrap border-l-2 tabular-nums ${edge}`}>{fmtSimTime(a.at)}</TableCell>
                    <TableCell className={`whitespace-nowrap ${a.level === 'escalation' ? 'font-semibold text-risk' : ''}`}>
                      {alertLevelLabel(a.level, a.message)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <DealLink folder={a.deal}>{a.deal}</DealLink>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{a.stage}</TableCell>
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

export function AlertsFeed() {
  const now = useClockStore((s) => s.now)
  const q = useAlerts()
  useEffect(() => {
    document.title = 'Alerts · Prism'
  }, [])
  if (q.isPending) return <LoadingState>Loading alerts.</LoadingState>
  if (q.isError) return <ErrorState heading="Could not load alerts." message={describeError(q.error)} onRetry={() => void q.refetch()} />
  return <AlertsFeedView alerts={q.data} now={now} />
}
