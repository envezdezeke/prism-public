// Pure sim-time view functions. No Date local-time use: every sim time is a
// fixed -06:00 wall-clock string, so UTC arithmetic gives exact results.
export interface StageLogEntry {
  stage: string
  entered_at: string
  exited_at?: string | null
  decision?: string | null
  why: string
}

export interface FraudBlock {
  flagged: boolean
  held: boolean
  flagged_at?: string | null
}

export interface DealLike {
  folder: string
  needed_by?: string | null
  stage: string
  stage_entered_at: string
  sla_hours: number
  sla_pct: number
  held: boolean
  watch: boolean
}

export interface DetailLike extends DealLike {
  stage_log: StageLogEntry[]
  fraud: FraudBlock
}

export interface InboxLike {
  id: string
  deal: string
  type: string
  to: string
  at: string
  options: string[]
  status: 'pending' | 'answered'
  answer?: string | null
  answered_at?: string | null
}

export interface AlertLike {
  id: string
  deal: string
  at: string
  level: 'alert' | 'escalation'
  message: string
}

const SIM_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})-06:00$/
const HOUR_MS = 3600_000

export function simToMs(s: string): number {
  const m = SIM_RE.exec(s)
  if (!m) throw new Error(`not a -06:00 sim time: ${s}`)
  // -06:00 means UTC = wall clock + 6h
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) + 6 * HOUR_MS
}

// Inverse of simToMs: the YYYY-MM-DDTHH:mm-06:00 shape the contract's parse_sim_time accepts.
export function msToSim(ms: number): string {
  const d = new Date(ms - 6 * HOUR_MS)
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}T${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}-06:00`
}

export function clampMs(x: number, lo: number, hi: number): number {
  return x < lo ? lo : x > hi ? hi : x
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

// "Tue 11/03 3:05 PM CT" (house style, docs/MESSAGE_TEMPLATES.md)
export function fmtSimTime(s: string): string {
  const m = SIM_RE.exec(s)
  if (!m) throw new Error(`not a -06:00 sim time: ${s}`)
  const y = +m[1], mo = +m[2], d = +m[3], h = +m[4], mi = +m[5]
  const dow = DOW[new Date(Date.UTC(y, mo - 1, d)).getUTCDay()]
  const h12 = ((h + 11) % 12) + 1
  const ampm = h < 12 ? 'AM' : 'PM'
  return `${dow} ${pad2(mo)}/${pad2(d)} ${h12}:${pad2(mi)} ${ampm} CT`
}

// "26h 10m"
export function fmtDuration(ms: number): string {
  const mins = Math.max(0, Math.round(ms / 60_000))
  return `${Math.floor(mins / 60)}h ${pad2(mins % 60)}m`
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`
}

// Stage entry in force at time t. null = the deal has not arrived yet.
export function stageAt(log: StageLogEntry[], t: number): StageLogEntry | null {
  let cur: StageLogEntry | null = null
  for (const e of log) {
    if (simToMs(e.entered_at) <= t) cur = e
    else break
  }
  return cur
}

// Held deals freeze their SLA at flagged_at (contract: sla_pct freezes while held).
// The end of the deal's final stage entry also ends the clock (held, declined or closed deals stop accruing).
export function slaPctAt(deal: DetailLike, enteredAt: string, t: number): number {
  const f = deal.fraud
  const frozen = f.held && f.flagged_at != null && simToMs(f.flagged_at) <= t
  let end = frozen ? simToMs(f.flagged_at as string) : t
  const last = deal.stage_log[deal.stage_log.length - 1]
  if (last && last.entered_at === enteredAt && last.exited_at != null) {
    const exit = simToMs(last.exited_at)
    if (exit <= t && exit < end) end = exit
  }
  return (end - simToMs(enteredAt)) / HOUR_MS / deal.sla_hours
}

// Display rounding: round half up to 2 decimals, matches fixture values.
export function roundPct(x: number): number {
  return Math.round(x * 100) / 100
}

export function heldAt(deal: DetailLike, t: number): boolean {
  return deal.fraud.held && deal.fraud.flagged_at != null && simToMs(deal.fraud.flagged_at) <= t
}

export function inboxAt(items: InboxLike[], t: number): InboxLike[] {
  return items
    .filter((i) => simToMs(i.at) <= t)
    .map((i) => {
      const answered = i.status === 'answered' && i.answered_at != null && simToMs(i.answered_at) <= t
      return answered
        ? { ...i }
        : { ...i, status: 'pending' as const, answer: null, answered_at: null }
    })
}

// inboxAt plus the in-session answer overlay (D-21): an answer given in this session counts from
// its answered_at, so scrubbing the clock back before it shows the item pending again.
export interface OverlayAnswer {
  answer: string
  answered_at: string
}

export function inboxWithOverlayAt(items: InboxLike[], overlay: Readonly<Record<string, OverlayAnswer>>, t: number): InboxLike[] {
  return inboxAt(items, t).map((i) => {
    const o = Object.hasOwn(overlay, i.id) ? overlay[i.id] : undefined
    if (i.status === 'pending' && o !== undefined && SIM_RE.test(o.answered_at) && simToMs(o.answered_at) <= t) {
      return { ...i, status: 'answered' as const, answer: o.answer, answered_at: o.answered_at }
    }
    return i
  })
}

// Alerts visible at t. Needs only an `at` field, so the detail's alerts (no id or deal) work too.
// Newest first for the feed; pass 'oldest' for the detail list.
export function alertsAt<T extends { at: string }>(items: T[], t: number, order: 'newest' | 'oldest' = 'newest'): T[] {
  const sign = order === 'newest' ? -1 : 1
  return items.filter((a) => simToMs(a.at) <= t).sort((a, b) => sign * (simToMs(a.at) - simToMs(b.at)))
}

// Stage log entries that have started by t. An entry whose exit is after t (or never) is open: its exit
// time and decision are hidden until t reaches them, so a screen can never show the future (D-18).
// An entry that will exit later also hides its `why`, which explains that exit; an entry that never
// exits keeps it, since it then describes where the deal stands.
export function stageLogAt<T extends StageLogEntry>(log: T[], t: number): (T & { open: boolean })[] {
  const out: (T & { open: boolean })[] = []
  for (const e of log) {
    if (simToMs(e.entered_at) > t) continue
    const open = e.exited_at == null || simToMs(e.exited_at) > t
    if (!open) out.push({ ...e, open })
    else out.push({ ...e, exited_at: null, decision: null, why: e.exited_at == null ? e.why : '', open })
  }
  return out
}

// Requests sent by t. The answer and answered_at are shown only once answered_at has been reached.
export function requestsAt<T extends { at: string; answer?: string | null; answered_at?: string | null }>(
  requests: T[],
  t: number,
): T[] {
  return requests
    .filter((r) => simToMs(r.at) <= t)
    .map((r) => {
      const answered = r.answer != null && r.answered_at != null && simToMs(r.answered_at) <= t
      return answered ? { ...r } : { ...r, answer: null, answered_at: null }
    })
}

export interface BoardRow<T extends DetailLike = DetailLike> {
  deal: T
  stage: string
  enteredAt: string
  // Raw, unrounded SLA fraction (1 = 100%). Round only at display time.
  slaFraction: number
  held: boolean
}

// One row per deal that has arrived by time t; deals not yet arrived are omitted.
export function boardAt<T extends DetailLike>(details: T[], t: number): BoardRow<T>[] {
  const rows: BoardRow<T>[] = []
  for (const deal of details) {
    const cur = stageAt(deal.stage_log, t)
    if (cur === null) continue
    rows.push({
      deal,
      stage: cur.stage,
      enteredAt: cur.entered_at,
      slaFraction: slaPctAt(deal, cur.entered_at, t),
      held: heldAt(deal, t),
    })
  }
  return rows
}

// Smallest sim time strictly after t across every timestamp a screen can change on, or null.
// `extra` carries timestamps the caller holds outside the fetched data (for example in-session answers).
// Strings that are not -06:00 sim times are skipped, never thrown on.
export interface EventSource {
  stage_log: { entered_at: string; exited_at?: string | null }[]
  fraud: { flagged_at?: string | null }
  human_requests?: { at: string; answered_at?: string | null }[]
  alerts?: { at: string }[]
}

export function nextEventAfter(details: EventSource[], t: number, extra: string[] = []): string | null {
  let best: number | null = null
  const consider = (s: string | null | undefined): void => {
    if (s == null || !SIM_RE.test(s)) return
    const ms = simToMs(s)
    if (ms > t && (best === null || ms < best)) best = ms
  }
  for (const d of details) {
    for (const e of d.stage_log) {
      consider(e.entered_at)
      consider(e.exited_at)
    }
    consider(d.fraud.flagged_at)
    for (const h of d.human_requests ?? []) {
      consider(h.at)
      consider(h.answered_at)
    }
    for (const a of d.alerts ?? []) consider(a.at)
  }
  for (const s of extra) consider(s)
  return best === null ? null : msToSim(best)
}

// Held first, then soonest needed-by (none last), then longest in stage, then folder name.
export function sortBoardRows<T extends DetailLike>(rows: BoardRow<T>[]): BoardRow<T>[] {
  const nb = (r: BoardRow<T>): number => {
    const s = r.deal.needed_by
    return s != null && SIM_RE.test(s) ? simToMs(s) : Number.POSITIVE_INFINITY
  }
  return [...rows].sort((a, b) => {
    if (a.held !== b.held) return a.held ? -1 : 1
    const na = nb(a), nbb = nb(b)
    if (na !== nbb) return na < nbb ? -1 : 1
    const ea = simToMs(a.enteredAt), eb = simToMs(b.enteredAt)
    if (ea !== eb) return ea - eb
    return a.deal.folder < b.deal.folder ? -1 : a.deal.folder > b.deal.folder ? 1 : 0
  })
}
