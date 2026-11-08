import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { CheckCircle2, Download, FileArchive, FolderUp, Loader2, Play, RotateCcw, ShieldAlert, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { StatusBadge } from './Badges.tsx'
import { Caption, PageHeader } from './Primitives.tsx'

// Run the frozen engine on a judge data set dropped in as a zip, then download the graded JSON.
// The server runs the same CLI as the terminal (python -m prism run, then validate).

interface UploadDeal {
  folder: string
  legal_name: string | null
  final_stage: string | null
  held: boolean
  flagged: boolean
  stage_at_flag: string | null
  linked_deals: string[]
  issues: string[]
}
interface UploadRunResult {
  run_id: string
  set: string
  ok: boolean
  valid: boolean
  seconds: number
  n_deals: number
  n_held: number
  n_flagged: number
  history_flags: string[]
  deals: UploadDeal[]
  validate_errors: number
  validate_warnings: number
  log_tail: string
}

const RUN_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$/
const STEPS = ['Upload', 'Unpack', 'Read the folder', 'Move every deal', 'Catch the fakes', 'Validate']

type State =
  | { kind: 'idle' }
  | { kind: 'running'; started: number }
  | { kind: 'done'; result: UploadRunResult }
  | { kind: 'error'; message: string }

function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: 'hold' }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <p className="text-sm text-ink">{label}</p>
      <p className={`pt-2 text-title font-semibold tabular-nums ${tone === 'hold' ? 'text-hold' : 'text-ink'}`}>{value}</p>
    </div>
  )
}

export function UploadRun() {
  const [file, setFile] = useState<File | null>(null)
  const [setName, setSetName] = useState('hidden')
  const [drag, setDrag] = useState(false)
  const [state, setState] = useState<State>({ kind: 'idle' })
  const [elapsed, setElapsed] = useState(0)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.title = 'Run judge data · Prism'
  }, [])

  useEffect(() => {
    if (state.kind !== 'running') return
    const id = window.setInterval(() => setElapsed((Date.now() - state.started) / 1000), 250)
    return () => window.clearInterval(id)
  }, [state])

  const pick = (f: File | undefined | null): void => {
    if (!f) return
    setFile(f)
    setState({ kind: 'idle' })
  }

  const run = async (): Promise<void> => {
    if (!file) return
    setElapsed(0)
    setState({ kind: 'running', started: Date.now() })
    try {
      const r = await fetch(`/api/upload-run?set=${encodeURIComponent(setName.trim() || 'hidden')}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/zip' },
        body: file,
      })
      const body: unknown = await r.json().catch(() => null)
      if (!r.ok) {
        const detail = body && typeof body === 'object' && 'detail' in body ? String((body as { detail: unknown }).detail) : `HTTP ${r.status}`
        setState({ kind: 'error', message: r.status === 409 ? 'Another run is in progress. Try again in a minute.' : detail })
        return
      }
      setState({ kind: 'done', result: body as UploadRunResult })
    } catch {
      setState({ kind: 'error', message: 'The API is not reachable. Start the server on port 8000.' })
    }
  }

  // Indeterminate progress: walk the engine's stages on a rough schedule (~40 s for 13 deals).
  const stepAt = state.kind === 'running' ? Math.min(STEPS.length - 1, 1 + Math.floor(elapsed / 8)) : state.kind === 'done' ? STEPS.length : 0
  const result = state.kind === 'done' ? state.result : null
  const downloadHref = result && RUN_ID.test(result.run_id) ? `/api/upload-run/${result.run_id}/submission` : null

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Run judge data" crumbs={['Run', 'Judge data']}>
        <Caption>
          Drop a data set as a zip: a <code>shared_folder/</code> with one folder per deal and an <code>arrivals.jsonl</code>. The frozen engine runs on it exactly as from the terminal and writes the submission JSON that is graded.
        </Caption>
      </PageHeader>

      <div className="grid grid-cols-3 gap-4">
        <label
          onDragOver={(e) => {
            e.preventDefault()
            setDrag(true)
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDrag(false)
            pick(e.dataTransfer.files?.[0])
          }}
          className={`col-span-2 flex cursor-pointer flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed bg-surface p-10 text-center transition-colors ${
            drag ? 'border-ink bg-subtle' : 'border-line hover:border-ink-2'
          }`}
        >
          <input
            ref={input}
            type="file"
            accept=".zip,application/zip"
            className="sr-only"
            onChange={(e) => pick(e.target.files?.[0])}
          />
          <span className="flex size-12 items-center justify-center rounded-full border border-line">
            {file ? <FileArchive className="size-5 text-ink" aria-hidden="true" /> : <FolderUp className="size-5 text-ink" aria-hidden="true" />}
          </span>
          {file ? (
            <>
              <span className="text-sm font-semibold text-ink">{file.name}</span>
              <span className="text-xs text-ink-2">{(file.size / 1024 / 1024).toFixed(1)} MB · click or drop to replace</span>
            </>
          ) : (
            <>
              <span className="text-sm font-semibold text-ink">Drop the data set zip here</span>
              <span className="text-xs text-ink-2">or click to choose a file · up to 300 MB</span>
            </>
          )}
        </label>

        <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface p-4">
          <h2 className="text-base font-semibold text-ink">Run settings</h2>
          <label className="flex flex-col gap-1 text-xs text-ink-2">
            Set name (written into the JSON)
            <Input value={setName} maxLength={32} onChange={(e) => setSetName(e.target.value)} />
          </label>
          <p className="text-xs text-ink-2">
            History, contacts and rules come from the zip when it has them, otherwise from the sample data on this machine.
          </p>
          <Button className="mt-auto" disabled={!file || state.kind === 'running'} onClick={() => void run()}>
            {state.kind === 'running' ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Play aria-hidden="true" />}
            {state.kind === 'running' ? `Running… ${elapsed.toFixed(0)}s` : 'Run the engine'}
          </Button>
        </div>
      </div>

      {/* Stage tracker for the run itself. */}
      {state.kind !== 'idle' && (
        <section aria-label="Run progress" className="rounded-lg border border-line bg-surface p-4">
          <ol className="flex items-center">
            {STEPS.map((s, i) => {
              const done = i < stepAt
              const cur = i === stepAt && state.kind === 'running'
              return (
                <li key={s} className="flex flex-1 items-center last:flex-none">
                  <span className="flex flex-col items-center gap-1">
                    <span
                      className={`flex size-7 items-center justify-center rounded-full text-xs font-semibold ${
                        done ? 'bg-ink text-surface' : cur ? 'bg-navy text-surface ring-4 ring-subtle' : 'border border-line text-ink-2'
                      }`}
                    >
                      {done ? <CheckCircle2 className="size-4" aria-hidden="true" /> : cur ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : i + 1}
                    </span>
                    <span className={`text-xs ${cur || done ? 'font-semibold text-ink' : 'text-ink-2'}`}>{s}</span>
                  </span>
                  {i < STEPS.length - 1 && <span className={`mx-2 mb-5 h-0.5 flex-1 rounded-full transition-colors duration-500 ${done ? 'bg-ink' : 'bg-line'}`} />}
                </li>
              )
            })}
          </ol>
          {state.kind === 'error' && (
            <p role="alert" className="mt-3 flex items-center gap-2 rounded-md border border-line bg-subtle px-3 py-2 text-sm text-ink">
              <Upload className="size-4 text-ink-2" aria-hidden="true" /> {state.message}
            </p>
          )}
        </section>
      )}

      {result && (
        <>
          <div className="flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-4">
            {result.ok && result.valid ? (
              <CheckCircle2 className="size-5 text-ink" aria-hidden="true" />
            ) : (
              <ShieldAlert className="size-5 text-risk" aria-hidden="true" />
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">
                {result.ok
                  ? result.valid
                    ? 'Run complete. The submission JSON is valid.'
                    : `Run complete, but validation reported ${result.validate_errors} errors.`
                  : 'The engine run failed.'}
              </p>
              <p className="text-xs text-ink-2">
                Set “{result.set}” · {result.seconds}s · {result.validate_warnings} warnings · run {result.run_id}
              </p>
            </div>
            {downloadHref && result.ok && (
              <a href={downloadHref} download="submission.json" className="inline-flex h-8 items-center gap-1.5 rounded-md bg-navy px-3 text-sm font-semibold text-surface hover:opacity-90">
                <Download className="size-4" aria-hidden="true" /> Download submission.json
              </a>
            )}
            <Button variant="outline" size="sm" onClick={() => setState({ kind: 'idle' })}>
              <RotateCcw aria-hidden="true" /> New run
            </Button>
          </div>

          <div className="grid grid-cols-4 gap-4">
            <Stat label="Deals read" value={result.n_deals} />
            <Stat label="Held for fraud" value={result.n_held} tone={result.n_held ? 'hold' : undefined} />
            <Stat label="Flagged" value={result.n_flagged} />
            <Stat label="History deals linked" value={result.history_flags.length} />
          </div>

          <section aria-label="Deals in this run" className="rounded-lg border border-line bg-surface">
            <h2 className="p-4 pb-2 text-base font-semibold text-ink">Deals in this run</h2>
            <Table>
              <TableHeader className="bg-subtle [&_th]:text-xs [&_th]:font-semibold [&_th]:tracking-wide [&_th]:text-ink-2 [&_th]:uppercase">
                <TableRow>
                  <TableHead>Deal</TableHead>
                  <TableHead>Final stage</TableHead>
                  <TableHead>Fraud</TableHead>
                  <TableHead>Linked to</TableHead>
                  <TableHead>Open issues</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[...result.deals]
                  .sort((a, b) => Number(b.held) - Number(a.held))
                  .map((d) => (
                    <TableRow key={d.folder}>
                      <TableCell>
                        <span className="block text-sm font-semibold text-ink">{d.legal_name ?? d.folder}</span>
                        <span className="block text-xs text-ink-2">{d.folder}</span>
                      </TableCell>
                      <TableCell className="text-sm">{d.final_stage ?? '—'}</TableCell>
                      <TableCell>
                        {d.held ? (
                          <span className="flex flex-col gap-0.5">
                            <StatusBadge held watch={false} />
                            {d.stage_at_flag && <span className="text-xs text-ink-2">held at {d.stage_at_flag}</span>}
                          </span>
                        ) : (
                          <span className="text-xs text-ink-2">{d.flagged ? 'Flagged, not held' : 'Clear'}</span>
                        )}
                      </TableCell>
                      <TableCell className="max-w-64 text-xs whitespace-normal text-ink-2">{d.linked_deals.join(', ') || '—'}</TableCell>
                      <TableCell className="text-xs tabular-nums text-ink-2">{d.issues.length}</TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </section>
          {result.history_flags.length > 0 && (
            <Caption>History flags: {result.history_flags.join(', ')}</Caption>
          )}
        </>
      )}
    </div>
  )
}
