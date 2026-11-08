import { useMemo } from 'react'
import { useInbox } from '../data/queries.ts'
import { inboxWithOverlayAt, simToMs } from '../time/timeview.ts'
import { useAnswersStore } from './answersStore.ts'
import { useClockStore } from './clockStore.ts'

// The rail's pending count, at the clock time with the session overlay applied.
export function usePendingCount(): number {
  const now = useClockStore((s) => s.now)
  const answers = useAnswersStore((s) => s.answers)
  const q = useInbox()
  return useMemo(() => {
    if (!q.data || now === '') return 0
    return inboxWithOverlayAt(q.data, answers, simToMs(now)).filter((i) => i.status === 'pending').length
  }, [q.data, answers, now])
}
