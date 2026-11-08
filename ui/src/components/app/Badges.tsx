import { Badge } from '@/components/ui/badge'
import { fmtSimTime } from '../../lib/format.ts'

export const HELD_LABEL = 'Held: fraud review'

// One status slot: Held wins over Watch; a Closed-stage deal with neither shows Cleared; otherwise nothing.
export function StatusBadge({
  held,
  watch,
  reason,
  cleared = false,
}: {
  held: boolean
  watch: boolean
  reason?: string | null
  cleared?: boolean
}) {
  if (held) {
    return (
      <Badge variant="hold" title={reason ?? 'The deal is frozen for fraud review.'}>
        {HELD_LABEL}
      </Badge>
    )
  }
  if (watch) {
    return (
      <Badge variant="outline" title="Shown for review. Not held.">
        Watch
      </Badge>
    )
  }
  if (cleared) return <Badge variant="cleared">Cleared</Badge>
  return null
}

type NeededBy = 'none' | 'ok' | 'at_risk' | 'overdue'

// Amber (never red) for at-risk and overdue. The time is as of end of run (D-18).
export function NeededByBadge({ badge, neededBy }: { badge: NeededBy; neededBy: string | null }) {
  // A date with no badge value is still shown, neutral (UI-SPEC); no date and no badge shows nothing.
  if (badge === 'none' && neededBy == null) return null
  const label = badge === 'at_risk' ? 'At risk: needed by' : badge === 'overdue' ? 'Overdue: needed by' : 'Needed by'
  const when = neededBy == null ? '' : ` ${fmtSimTime(neededBy)}`
  const title = neededBy == null ? 'As of end of run' : `${fmtSimTime(neededBy)}, as of end of run`
  const tone =
    badge === 'ok' || badge === 'none'
      ? 'border-line bg-surface font-normal text-ink-2'
      : 'border-transparent bg-risk-soft font-semibold text-risk'
  return (
    <span title={title} className={`inline-block rounded-sm border px-2 py-1 text-xs ${tone}`}>
      {label}
      {when}
    </span>
  )
}

// 2px track spanning 0 to 200 percent. Ticks sit at 100, 150 and 200 percent (50, 75 and 100 percent of the width).
export function SlaBar({ pct, label }: { pct: number; label: string }) {
  const width = Math.min(Math.max(pct, 0) / 2, 1) * 100
  const over = Math.round(pct * 100) > 100
  return (
    <div role="img" aria-label={label} className="relative h-0.5 w-full bg-line">
      <div className={`h-full ${over ? 'bg-risk' : 'bg-tertiary'}`} style={{ width: `${width}%` }} />
      <span aria-hidden="true" className="absolute -top-0.5 left-1/2 h-1.5 w-px bg-tertiary" />
      <span aria-hidden="true" className="absolute -top-0.5 left-3/4 h-1.5 w-px bg-tertiary" />
      <span aria-hidden="true" className="absolute -top-0.5 right-0 h-1.5 w-px bg-tertiary" />
    </div>
  )
}
