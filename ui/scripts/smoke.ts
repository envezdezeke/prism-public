// Smoke check of the live API against the client and the time derivation.
// Run with the API up: npm --prefix ui run smoke   (SMOKE_BASE overrides http://localhost:8000).
// Phase 7 reuses this after the mock-to-live flip.
import { getClock, getDeal, getDeals, setApiBase } from '../src/api/client.ts'
import { heldAt, roundPct, simToMs, slaPctAt, stageAt } from '../src/time/timeview.ts'

setApiBase(process.env.SMOKE_BASE ?? 'http://localhost:8000')

const failures: string[] = []
const fail = (msg: string): void => {
  failures.push(msg)
  console.log(`SMOKE FAIL ${msg}`)
}

try {
  const clock = await getClock()
  const t = simToMs(clock.now)
  const deals = await getDeals()

  for (const s of deals) {
    // getDeal URL-encodes the folder, so apostrophes and ampersands resolve.
    const d = await getDeal(s.folder)
    const cur = stageAt(d.stage_log, t)
    if (cur === null) {
      fail(`${s.folder}: not arrived at ${clock.now}`)
      continue
    }
    if (cur.stage !== s.stage) fail(`${s.folder}: stage ${cur.stage} != server ${s.stage}`)
    const pct = roundPct(slaPctAt(d, cur.entered_at, t))
    if (pct !== s.sla_pct) fail(`${s.folder}: sla ${pct} != server ${s.sla_pct}`)
    if (heldAt(d, t) !== s.held) fail(`${s.folder}: held ${heldAt(d, t)} != server ${s.held}`)
  }

  if (failures.length === 0) {
    console.log(`SMOKE OK deals=${deals.length} clock=${clock.now}`)
  }
} catch (e) {
  fail(`request failed: ${e instanceof Error ? e.message : String(e)}`)
}

process.exit(failures.length === 0 ? 0 : 1)
