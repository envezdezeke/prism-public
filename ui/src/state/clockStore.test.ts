import { afterEach, beforeEach, mock, test } from 'node:test'
import assert from 'node:assert/strict'
import { useClockStore } from './clockStore.ts'

interface Call {
  url: string
  method: string
  body: unknown
}

const flush = () => new Promise<void>((r) => setImmediate(r))

const realFetch = globalThis.fetch
let calls: Call[] = []
let failWrites = false
// What an advance answers while the mock refuses a rewind: 422, like the real API for a `to` before now.
let advanceStatus = 200

const CLOCK = { now: '2026-11-04T18:00-06:00', start: '2026-11-02T08:00-06:00', end: '2026-11-06T18:00-06:00' }

beforeEach(async () => {
  calls = []
  failWrites = false
  advanceStatus = 200
  mock.timers.enable({ apis: ['setTimeout', 'setInterval'] })
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (method === 'POST' && failWrites) {
      return new Response(JSON.stringify({ detail: 'engine is busy' }), { status: 500 })
    }
    if (method === 'POST' && advanceStatus !== 200 && String(url).endsWith('/advance')) {
      return new Response(
        JSON.stringify({ detail: "'to' is earlier than the current clock; use /api/clock/replay to go back" }),
        { status: advanceStatus },
      )
    }
    return new Response(JSON.stringify(CLOCK), { status: 200 })
  }) as typeof fetch
  useClockStore.setState({ now: '', start: '', end: '', status: 'idle', error: '', playing: false, syncError: '' })
  await useClockStore.getState().load()
})

afterEach(async () => {
  useClockStore.getState().pause()
  // Let any pending debounced write fire so no module-level timer handle outlives its mock clock.
  mock.timers.tick(1000)
  await flush()
  mock.timers.reset()
  globalThis.fetch = realFetch
})

test('the clock endpoint is read exactly once, at load', async () => {
  await useClockStore.getState().load()
  assert.equal(calls.filter((c) => c.method === 'GET').length, 1)
})

test('advanceHours moves the local clock at once and posts one debounced {to}', async () => {
  const s = useClockStore.getState()
  s.advanceHours(1)
  s.advanceHours(6)
  assert.equal(useClockStore.getState().now, '2026-11-05T01:00-06:00')
  assert.equal(calls.filter((c) => c.method === 'POST').length, 0)
  mock.timers.tick(300)
  await flush()
  const posts = calls.filter((c) => c.method === 'POST')
  assert.equal(posts.length, 1)
  assert.equal(posts[0].url, '/api/clock/advance')
  assert.deepEqual(posts[0].body, { to: '2026-11-05T01:00-06:00' })
})

test('setNow clamps to start and end and ignores a bad shape', () => {
  const s = useClockStore.getState()
  s.setNow('2026-12-31T00:00-06:00', false)
  assert.equal(useClockStore.getState().now, CLOCK.end)
  s.setNow('2020-01-01T00:00-06:00', false)
  assert.equal(useClockStore.getState().now, CLOCK.start)
  s.setNow('not a time', false)
  assert.equal(useClockStore.getState().now, CLOCK.start)
})

test('a failed write sets syncError and never reverts the local clock; a later success clears it', async () => {
  const s = useClockStore.getState()
  failWrites = true
  s.goTo('2026-11-03T09:00-06:00')
  mock.timers.tick(300)
  await flush()
  assert.equal(useClockStore.getState().now, '2026-11-03T09:00-06:00')
  assert.equal(useClockStore.getState().syncError, 'engine is busy')
  failWrites = false
  s.goTo('2026-11-03T10:00-06:00')
  mock.timers.tick(300)
  await flush()
  assert.equal(useClockStore.getState().syncError, '')
})

test('replay returns to start, posts {} and plays one sim hour per second without syncing ticks', async () => {
  const s = useClockStore.getState()
  s.replay()
  await flush()
  assert.equal(useClockStore.getState().now, CLOCK.start)
  assert.equal(useClockStore.getState().playing, true)
  const posts = () => calls.filter((c) => c.method === 'POST')
  assert.equal(posts().length, 1)
  assert.equal(posts()[0].url, '/api/clock/replay')
  assert.deepEqual(posts()[0].body, {})
  mock.timers.tick(1000)
  assert.equal(useClockStore.getState().now, '2026-11-02T09:00-06:00')
  mock.timers.tick(3000)
  assert.equal(useClockStore.getState().now, '2026-11-02T12:00-06:00')
  mock.timers.tick(299)
  await flush()
  assert.equal(posts().length, 1, 'ticks do not write to the API')
})

test('pause stops playback and syncs the current value once; playback stops at the end', async () => {
  const s = useClockStore.getState()
  s.setNow('2026-11-06T16:00-06:00', false)
  s.play()
  mock.timers.tick(1000)
  mock.timers.tick(1000)
  assert.equal(useClockStore.getState().now, CLOCK.end)
  assert.equal(useClockStore.getState().playing, false)
  mock.timers.tick(300)
  await flush()
  const posts = calls.filter((c) => c.method === 'POST')
  assert.equal(posts.length, 1)
  assert.deepEqual(posts[0].body, { to: CLOCK.end })
})

// WR-04: the server never rewinds on advance, so a backward move must not leave a sticky banner.
test('WR-04 a backward Go to replays first, then advances to the absolute target', async () => {
  advanceStatus = 422 // a stateless mock still sits at its fixture time and refuses the earlier target
  useClockStore.getState().goTo('2026-11-03T09:00-06:00')
  mock.timers.tick(300)
  await flush()
  const posts = calls.filter((c) => c.method === 'POST')
  assert.deepEqual(posts.map((c) => c.url), ['/api/clock/replay', '/api/clock/advance'])
  assert.deepEqual(posts[1].body, { to: '2026-11-03T09:00-06:00' })
  assert.equal(useClockStore.getState().now, '2026-11-03T09:00-06:00')
  assert.equal(useClockStore.getState().syncError, '', 'a refused rewind is the server clock being ahead, not a failure')
})

test('WR-04 a forward move needs no replay, and a 422 on it does not set syncError', async () => {
  useClockStore.getState().goTo('2026-11-05T09:00-06:00')
  mock.timers.tick(300)
  await flush()
  assert.deepEqual(calls.filter((c) => c.method === 'POST').map((c) => c.url), ['/api/clock/advance'])
  advanceStatus = 422
  useClockStore.getState().goTo('2026-11-05T10:00-06:00')
  mock.timers.tick(300)
  await flush()
  assert.equal(useClockStore.getState().syncError, '')
})

test('WR-04 Pause during a Replay syncs without an error banner', async () => {
  advanceStatus = 422
  const s = useClockStore.getState()
  s.replay()
  await flush()
  mock.timers.tick(3000)
  s.pause()
  mock.timers.tick(300)
  await flush()
  assert.equal(useClockStore.getState().now, '2026-11-02T11:00-06:00')
  assert.equal(useClockStore.getState().syncError, '')
  const posts = calls.filter((c) => c.method === 'POST')
  assert.deepEqual(posts.map((c) => c.url), ['/api/clock/replay', '/api/clock/advance'])
})

test('WR-04 a real failure of the write still shows the banner text', async () => {
  failWrites = true
  useClockStore.getState().goTo('2026-11-03T09:00-06:00')
  mock.timers.tick(300)
  await flush()
  assert.equal(useClockStore.getState().syncError, 'engine is busy')
})
