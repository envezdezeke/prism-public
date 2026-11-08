import { afterEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { ApiError, describeError, getDeal, getDeals, postAdvance, postReplay, setApiBase } from './client.ts'

interface Call {
  url: string
  init: RequestInit | undefined
}

const realFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = realFetch
  setApiBase('')
})

function stub(respond: (call: Call) => Response | Promise<Response>): Call[] {
  const calls: Call[] = []
  globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
    const call = { url: String(url), init }
    calls.push(call)
    return respond(call)
  }) as typeof fetch
  return calls
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

test('getDeal encodes folder names with apostrophes and ampersands', async () => {
  const calls = stub(() => json({ folder: 'x' }))
  await getDeal('Bayou Bistro & Grill')
  await getDeal("Mama Rosa's Kitchen")
  assert.equal(calls[0].url, `/api/deals/${encodeURIComponent('Bayou Bistro & Grill')}`)
  assert.equal(calls[0].url, '/api/deals/Bayou%20Bistro%20%26%20Grill')
  assert.equal(calls[1].url, `/api/deals/${encodeURIComponent("Mama Rosa's Kitchen")}`)
})

test('postAdvance sends POST, JSON content-type and a body with the single key to', async () => {
  const calls = stub(() => json({ now: 'a', start: 'b', end: 'c' }))
  await postAdvance('2026-11-03T15:05-06:00')
  assert.equal(calls[0].url, '/api/clock/advance')
  assert.equal(calls[0].init?.method, 'POST')
  const headers = new Headers(calls[0].init?.headers)
  assert.equal(headers.get('content-type'), 'application/json')
  const body = JSON.parse(String(calls[0].init?.body))
  assert.deepEqual(body, { to: '2026-11-03T15:05-06:00' })
  assert.deepEqual(Object.keys(body), ['to'])
})

test('postReplay sends POST with the empty object', async () => {
  const calls = stub(() => json({ now: 'a', start: 'b', end: 'c' }))
  await postReplay()
  assert.equal(calls[0].url, '/api/clock/replay')
  assert.equal(calls[0].init?.method, 'POST')
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {})
})

async function errorFrom(p: Promise<unknown>): Promise<unknown> {
  try {
    await p
  } catch (e) {
    return e
  }
  throw new Error('expected a rejection')
}

test('describeError: 503 maps to the engine-not-connected text', async () => {
  stub(() => json({ detail: 'engine not wired yet' }, 503))
  assert.equal(describeError(await errorFrom(getDeals())), 'The engine is not connected.')
})

test('describeError: a 422 detail string is shown exactly', async () => {
  stub(() => json({ detail: 'option must be one of: approve, decline' }, 422))
  const e = await errorFrom(postAdvance('2026-11-03T15:05-06:00'))
  assert.ok(e instanceof ApiError)
  assert.equal(describeError(e), 'option must be one of: approve, decline')
})

test('describeError: a thrown network TypeError is "not reachable"', async () => {
  stub(() => {
    throw new TypeError('fetch failed')
  })
  assert.equal(
    describeError(await errorFrom(getDeals())),
    'The API is not reachable. Start the mock server on port 8000.',
  )
})

test('describeError: a non-JSON 500 body is a fixed HTTP message, never the raw body', async () => {
  stub(() => new Response('<html>Traceback boom</html>', { status: 500 }))
  assert.equal(describeError(await errorFrom(getDeals())), 'Request failed (HTTP 500)')
})
