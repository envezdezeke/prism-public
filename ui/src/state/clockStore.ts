import { create } from 'zustand'
import { ApiError, describeError, getClock, postAdvance, postReplay } from '../api/client.ts'
import { clampMs, msToSim, simToMs } from '../time/timeview.ts'

type ClockStatus = 'idle' | 'loading' | 'ready' | 'error'

interface ClockState {
  now: string
  start: string
  end: string
  status: ClockStatus
  error: string
  // Playback (Replay from start, Play) moves the clock one sim hour per real second.
  playing: boolean
  // describeError text of the last failed write to the API, or '' when in sync. The local clock never reverts.
  syncError: string
  load: () => Promise<void>
  setNow: (sim: string, sync?: boolean) => void
  advanceHours: (h: number) => void
  goTo: (sim: string) => void
  replay: () => void
  play: () => void
  pause: () => void
}

// Timer handles live outside the store state (T-02-06).
let syncTimer: ReturnType<typeof setTimeout> | null = null
let playTimer: ReturnType<typeof setInterval> | null = null
// Only the newest write's outcome may touch syncError.
let syncSeq = 0
// The server clock as far as this browser knows it (ms): the time read at load, then each confirmed write.
// A target before it is a rewind, which the API only accepts through replay.
let serverMs = 0

const SYNC_DEBOUNCE_MS = 300
const TICK_MS = 1000
const HOUR_MS = 3600_000

function clearSyncTimer(): void {
  if (syncTimer !== null) clearTimeout(syncTimer)
  syncTimer = null
}

function clearPlayTimer(): void {
  if (playTimer !== null) clearInterval(playTimer)
  playTimer = null
}

// The mock clock is stateless: GET /api/clock always returns the fixture time. The browser therefore
// owns the clock. It is read once here and never again (D-21); it is never a query. Writes send only
// an absolute target ({to}) or {} for replay, and their responses are never used for the displayed time.
export const useClockStore = create<ClockState>((set, get) => {
  const report = (seq: number, p: Promise<unknown>): void => {
    p.then(
      () => {
        if (seq === syncSeq) set({ syncError: '' })
      },
      (e: unknown) => {
        if (seq === syncSeq) set({ syncError: describeError(e) })
      },
    )
  }

  // Sends one absolute target (D-21). The API never rewinds on advance, so a backward move replays to the
  // start first. A 422 from advance means the server clock is already ahead of the target (a stateless mock
  // never moves off its fixture time): the browser owns the clock, so that is not a failure to report.
  const pushClock = async (target: string): Promise<void> => {
    const ms = simToMs(target)
    if (ms < serverMs) {
      await postReplay()
      serverMs = simToMs(get().start)
    }
    try {
      await postAdvance(target)
      serverMs = ms
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) return
      throw e
    }
  }

  const scheduleSync = (): void => {
    clearSyncTimer()
    syncTimer = setTimeout(() => {
      syncTimer = null
      const seq = ++syncSeq
      report(seq, pushClock(get().now))
    }, SYNC_DEBOUNCE_MS)
  }

  // Stops playback without writing to the API; user controls and the end of the week use it.
  const stopPlayback = (): void => {
    clearPlayTimer()
    if (get().playing) set({ playing: false })
  }

  const setNow = (sim: string, sync = true): void => {
    const { status, start, end } = get()
    if (status !== 'ready') return
    let ms: number
    try {
      ms = simToMs(sim)
    } catch {
      return // not a -06:00 sim time: ignore (T-02-05)
    }
    const next = msToSim(clampMs(ms, simToMs(start), simToMs(end)))
    set({ now: next })
    if (sync) scheduleSync()
  }

  return {
    now: '',
    start: '',
    end: '',
    status: 'idle',
    error: '',
    playing: false,
    syncError: '',
    load: async () => {
      const { status } = get()
      if (status === 'loading' || status === 'ready') return
      set({ status: 'loading', error: '' })
      try {
        const c = await getClock()
        serverMs = simToMs(c.now)
        set({ now: c.now, start: c.start, end: c.end, status: 'ready' })
      } catch (e) {
        set({ status: 'error', error: describeError(e) })
      }
    },
    setNow,
    advanceHours: (h) => {
      const { status, now } = get()
      if (status !== 'ready') return
      stopPlayback()
      setNow(msToSim(simToMs(now) + h * HOUR_MS))
    },
    goTo: (sim) => {
      stopPlayback()
      setNow(sim)
    },
    replay: () => {
      const { status, start } = get()
      if (status !== 'ready') return
      stopPlayback()
      clearSyncTimer()
      setNow(start, false)
      serverMs = simToMs(start)
      report(++syncSeq, postReplay())
      get().play()
    },
    play: () => {
      const { status, now, end } = get()
      if (status !== 'ready' || simToMs(now) >= simToMs(end)) return
      clearPlayTimer()
      set({ playing: true })
      playTimer = setInterval(() => {
        const s = get()
        const next = simToMs(s.now) + HOUR_MS
        setNow(msToSim(next), false)
        if (simToMs(get().now) >= simToMs(s.end)) get().pause()
      }, TICK_MS)
    },
    pause: () => {
      if (!get().playing && playTimer === null) return
      stopPlayback()
      scheduleSync()
    },
  }
})
