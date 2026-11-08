// Components import formatters from here only (UI_PLAYBOOK section 3). Pure, no React.
import { fmtSimTime, fmtDuration } from '../time/timeview.ts'

export { fmtSimTime, fmtDuration }

// A missing value renders as this dash, with the tooltip below (see NullValue).
export const DASH = '—'
export const NULL_TOOLTIP = 'Not found in documents'

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
})

export function fmtMoney(n: number): string {
  return money.format(n)
}

// "48h", "1.5h": the limit exactly as given.
export function fmtHours(h: number): string {
  return `${h}h`
}

// Whole percent from a raw fraction, rounded half up: 0.455 -> "46%", 1.13 -> "113%".
export function fmtPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

// "26h 10m / 48h · 55%": duration is built from the raw (unrounded) fraction times the limit.
export function fmtSla(fraction: number, limitHours: number): string {
  return `${fmtDuration(fraction * limitHours * 3600_000)} / ${fmtHours(limitHours)} · ${fmtPct(fraction)}`
}

// "Fri 11/06" from a sim time.
export function fmtSimDay(s: string): string {
  return fmtSimTime(s).split(' ').slice(0, 2).join(' ')
}

export function confidenceWord(c: number): 'High' | 'Medium' | 'Low' {
  if (c >= 0.9) return 'High'
  if (c >= 0.6) return 'Medium'
  return 'Low'
}

// "98% confidence" (UI-SPEC shows the whole percent; the word is available for a tooltip).
export function confidenceTitle(c: number): string {
  return `${Math.round(c * 100)}% confidence`
}
