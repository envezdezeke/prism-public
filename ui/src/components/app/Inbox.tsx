import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, describeError, postReply } from '../../api/client.ts'
import type { InboxItem } from '../../api/types.ts'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useInbox } from '../../data/queries.ts'
import { fmtDuration, fmtSimTime } from '../../lib/format.ts'
import { isClearedAnswer, optionLabel, requestTypeLabel } from '../../labels.ts'
import { personLabel, roleForRequest } from '../../people.ts'
import { useAnswersStore } from '../../state/answersStore.ts'
import { useClockStore } from '../../state/clockStore.ts'
import { inboxWithOverlayAt, nextEventAfter, simToMs } from '../../time/timeview.ts'
import { useBoardDetails } from '../../data/queries.ts'
import { CheckCheck, Hourglass, Inbox as InboxIcon, ListChecks } from 'lucide-react'
import { Caption, DealLink, ErrorState, IconEmptyState, LoadingState, PageHeader } from './Primitives.tsx'

// decline and release_hold confirm inline first (02-UI-SPEC Copywriting). No modal, no toast.
const CONFIRM = new Set(['decline', 'release_hold'])

function Item({ item, now }: { item: InboxItem; now: string }) {
  const record = useAnswersStore((s) => s.record)
  const sessionAnswer = useAnswersStore((s) => s.answers[item.id])
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const who = personLabel(item.to, roleForRequest(item.type))
  const answered = item.status === 'answered'
  // Answered in this session at a later clock time: shown pending, but it cannot be sent twice.
  const sentLater = !answered && sessionAnswer !== undefined

  const send = async (option: string): Promise<void> => {
    setBusy(true)
    setMessage('')
    try {
      await postReply(item.id, option)
      record(item.id, { answer: option, answered_at: now })
      setConfirming(null)
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setMessage('This request was already answered. The list has been refreshed.')
        void qc.invalidateQueries({ queryKey: ['inbox'] })
      } else if (e instanceof ApiError && e.status === 422) {
        setMessage(`The server rejected that option: ${e.message}`)
      } else {
        setMessage(describeError(e))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="flex flex-col gap-2 rounded-md border border-line border-t-4 border-t-navy bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-base font-bold text-navy">{requestTypeLabel(item.type)}</span>
          <DealLink folder={item.deal}>{item.deal}</DealLink>
        </div>
        <Caption>
          Sent {fmtSimTime(item.at)} · to <span title={who.title}>{who.label}</span>
          {!answered && ` · waiting ${fmtDuration(simToMs(now) - simToMs(item.at))}`}
        </Caption>
      </div>
      <p className="text-base">{item.message}</p>
      {answered ? (
        <p role="status" className="flex items-center gap-2 text-sm">
          Answered: {optionLabel(item.answer ?? '')}
          {item.answered_at != null && ` · ${fmtSimTime(item.answered_at)}`}
          {isClearedAnswer(item.answer) && <Badge variant="cleared">Approved</Badge>}
        </p>
      ) : sentLater ? (
        <Caption>Answered in this session at {fmtSimTime(sessionAnswer.answered_at)}. Move the clock forward to see it.</Caption>
      ) : confirming !== null ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">
            {confirming === 'decline' ? `Decline ${item.deal}?` : `Release the fraud hold on ${item.deal}?`}
          </span>
          <Button size="sm" disabled={busy} onClick={() => void send(confirming)}>
            {confirming === 'decline' ? 'Confirm decline' : 'Confirm release hold'}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirming(null)}>
            {confirming === 'decline' ? 'Back to the request' : 'Keep the hold'}
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {item.options.map((o, i) => (
            <Button
              key={o}
              size="sm"
              variant={i === 0 ? 'default' : 'outline'}
              disabled={busy}
              onClick={() => (CONFIRM.has(o) ? setConfirming(o) : void send(o))}
            >
              {optionLabel(o)}
            </Button>
          ))}
        </div>
      )}
      {message !== '' && <p className="text-sm text-ink">{message}</p>}
    </li>
  )
}

export function InboxView({ items, now }: { items: InboxItem[]; now: string }) {
  const answers = useAnswersStore((s) => s.answers)
  const [show, setShow] = useState<'all' | 'pending' | 'answered'>('all')
  const goTo = useClockStore((s) => s.goTo)
  const { details } = useBoardDetails()
  const t = simToMs(now)
  const nextEvent = nextEventAfter(details, t)
  const visible = inboxWithOverlayAt(items, answers, t) as InboxItem[]
  const pending = visible.filter((i) => i.status === 'pending').sort((a, b) => simToMs(a.at) - simToMs(b.at))
  const done = visible
    .filter((i) => i.status === 'answered')
    .sort((a, b) => simToMs(b.answered_at ?? b.at) - simToMs(a.answered_at ?? a.at))
  return (
    <section aria-label="Inbox" className="flex max-w-3xl flex-col gap-4">
      <PageHeader
        title="Inbox"
        crumbs={['Pipeline', 'Inbox']}
        toolbar={
          <>
            <Button variant={show === 'all' ? 'default' : 'outline'} size="sm" aria-pressed={show === 'all'} onClick={() => setShow('all')}>
              <ListChecks aria-hidden="true" />
              All {pending.length + done.length}
            </Button>
            <Button variant={show === 'pending' ? 'default' : 'outline'} size="sm" aria-pressed={show === 'pending'} onClick={() => setShow('pending')}>
              <Hourglass aria-hidden="true" />
              Pending {pending.length}
            </Button>
            <Button variant={show === 'answered' ? 'default' : 'outline'} size="sm" aria-pressed={show === 'answered'} onClick={() => setShow('answered')}>
              <CheckCheck aria-hidden="true" />
              Answered {done.length}
            </Button>
          </>
        }
      />
      {show !== 'answered' && (<>
      <h2 className="text-base font-semibold text-ink">
        Pending <span className="font-normal text-ink-2 tabular-nums">{pending.length}</span>
      </h2>
      {pending.length === 0 ? (
        <IconEmptyState
          icon={InboxIcon}
          heading="No pending requests."
          action={nextEvent === null ? undefined : { label: 'Jump to next event', onClick: () => goTo(nextEvent) }}
        >
          Everything is waiting on borrowers or the clock.
        </IconEmptyState>
      ) : (
        <ul className="flex flex-col gap-2">
          {pending.map((i) => (
            <Item key={i.id} item={i} now={now} />
          ))}
        </ul>
      )}
      </>)}
      {show !== 'pending' && (<>
      <h2 className="pt-4 text-base font-semibold text-ink">
        Answered <span className="font-normal text-ink-2 tabular-nums">{done.length}</span>
      </h2>
      {done.length === 0 ? (
        <Caption>No answered requests as of {fmtSimTime(now)}.</Caption>
      ) : (
        <ul className="flex flex-col gap-2">
          {done.map((i) => (
            <Item key={i.id} item={i} now={now} />
          ))}
        </ul>
      )}
      </>)}
    </section>
  )
}

export function Inbox() {
  const now = useClockStore((s) => s.now)
  const q = useInbox()
  useEffect(() => {
    document.title = 'Inbox · Prism'
  }, [])
  if (q.isPending) return <LoadingState>Loading requests.</LoadingState>
  if (q.isError) return <ErrorState heading="Could not load requests." message={describeError(q.error)} onRetry={() => void q.refetch()} />
  return <InboxView items={q.data} now={now} />
}
