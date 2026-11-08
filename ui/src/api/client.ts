// Typed fetch wrapper. Runs under Vite and under Node (scripts/smoke.ts), so: relative imports with
// explicit .ts extensions, import type for types, and no import.meta.env.
import type {
  AlertFeedItem,
  Clock,
  DealDetail,
  DealSummary,
  FraudGraph,
  InboxItem,
  InvestigatorTrace,
  RedTeamReport,
} from './types.ts'

let base = ''

// Default is empty so paths stay relative and the Vite proxy handles /api. Node scripts set a host.
export function setApiBase(url: string): void {
  base = url.replace(/\/+$/, '')
}

// status 0 means the API could not be reached at all.
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const NETWORK_MESSAGE = 'The API is not reachable. Start the mock server on port 8000.'

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 503) return 'The engine is not connected.'
    if (e.status === 0) return NETWORK_MESSAGE
    return e.message
  }
  if (e instanceof TypeError) return NETWORK_MESSAGE
  return 'Request failed.'
}

// Never expose a raw body or stack trace: only the API's `detail` string or a fixed message.
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(base + path, init)
  } catch {
    throw new ApiError(0, 'network')
  }
  if (res.ok) return (await res.json()) as T

  const text = await res.text().catch(() => '')
  let detail: unknown
  try {
    detail = (JSON.parse(text) as { detail?: unknown }).detail
  } catch {
    detail = undefined
  }
  // A dev proxy with nothing behind it answers 5xx with an empty body.
  if (res.status >= 500 && text === '' && detail === undefined) throw new ApiError(0, 'network')
  throw new ApiError(
    res.status,
    typeof detail === 'string' ? detail : `Request failed (HTTP ${res.status})`,
  )
}

function apiGet<T>(path: string): Promise<T> {
  return request<T>(path)
}

function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function getClock(): Promise<Clock> {
  return apiGet<Clock>('/api/clock')
}

// Absolute target only, never hours: the mock recomputes hours from its fixed fixture clock and drifts.
export function postAdvance(to: string): Promise<Clock> {
  return apiPost<Clock>('/api/clock/advance', { to })
}

export function postReplay(): Promise<Clock> {
  return apiPost<Clock>('/api/clock/replay', {})
}

export function getDeals(): Promise<DealSummary[]> {
  return apiGet<DealSummary[]>('/api/deals')
}

export async function getDeal(folder: string): Promise<DealDetail> {
  try {
    // Folder names contain apostrophes, ampersands and spaces.
    return await apiGet<DealDetail>(`/api/deals/${encodeURIComponent(folder)}`)
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      throw new ApiError(404, 'This deal is not in the run.')
    }
    throw e
  }
}

export function getAlerts(): Promise<AlertFeedItem[]> {
  return apiGet<AlertFeedItem[]>('/api/alerts')
}

export function getInbox(): Promise<InboxItem[]> {
  return apiGet<InboxItem[]>('/api/inbox')
}

// The body is exactly {option} plus a note only when one was typed (UI-05).
export function postReply(id: string, option: string, note?: string): Promise<InboxItem> {
  const trimmed = note?.trim() ?? ''
  const body: { option: string; note?: string } = trimmed === '' ? { option } : { option, note: trimmed }
  return apiPost<InboxItem>(`/api/inbox/${encodeURIComponent(id)}/reply`, body)
}

// The whole graph, unfiltered: the clock filter runs client-side (D-18), so scrubbing never refetches.
export function getGraph(): Promise<FraudGraph> {
  return apiGet<FraudGraph>('/api/graph')
}

export function getInvestigator(clusterId: string): Promise<InvestigatorTrace> {
  return apiGet<InvestigatorTrace>(`/api/investigator/${encodeURIComponent(clusterId)}`)
}

export function getRedteam(): Promise<RedTeamReport> {
  return apiGet<RedTeamReport>('/api/redteam')
}
