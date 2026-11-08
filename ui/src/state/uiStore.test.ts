import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { syncRouteFromHash, useUiStore } from './uiStore.ts'

// Node has no location: stand one in so the hash synchronisation is exercised.
const fake = { hash: '' }
Object.defineProperty(globalThis, 'location', { value: fake, configurable: true, writable: true })

beforeEach(() => {
  fake.hash = ''
  useUiStore.setState({ route: { view: 'pipeline' } })
})

test('openDeal sets the route and writes the encoded hash', () => {
  useUiStore.getState().openDeal("Mama Rosa's Kitchen")
  assert.deepEqual(useUiStore.getState().route, { view: 'deal', folder: "Mama Rosa's Kitchen" })
  assert.equal(fake.hash, "#/deals/Mama%20Rosa's%20Kitchen")
})

test('showPipeline returns to the pipeline and writes #/pipeline', () => {
  useUiStore.getState().openDeal('Bayou Bistro & Grill')
  useUiStore.getState().showPipeline()
  assert.deepEqual(useUiStore.getState().route, { view: 'pipeline' })
  assert.equal(fake.hash, '#/pipeline')
})

test('a hash changed outside the store (link, Back) updates the route', () => {
  fake.hash = '#/deals/' + encodeURIComponent('Bayou Bistro & Grill')
  syncRouteFromHash()
  assert.deepEqual(useUiStore.getState().route, { view: 'deal', folder: 'Bayou Bistro & Grill' })
  fake.hash = '#/pipeline'
  syncRouteFromHash()
  assert.deepEqual(useUiStore.getState().route, { view: 'pipeline' })
})

test('a malformed hash falls back to the pipeline without throwing', () => {
  useUiStore.getState().openDeal('X')
  fake.hash = '#/deals/%E0%A4%A'
  syncRouteFromHash()
  assert.deepEqual(useUiStore.getState().route, { view: 'pipeline' })
})

test('re-opening the same deal does not rewrite the hash or loop', () => {
  useUiStore.getState().openDeal('X')
  const writes: string[] = []
  Object.defineProperty(fake, 'hash', { get: () => '#/deals/X', set: (v: string) => { writes.push(v) }, configurable: true })
  useUiStore.getState().openDeal('X')
  assert.equal(writes.length, 0)
  Object.defineProperty(fake, 'hash', { value: '', writable: true, configurable: true })
})
