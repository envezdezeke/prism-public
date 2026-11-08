import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { simToMs } from '../time/timeview.ts'
import {
  clusterForDeal, clusterHoldAt, defaultClusterId, graphAt, kindGroup, latestHold, redteamTotals, sharersOf,
} from './graphview.ts'

const FX = new URL('../../../fixtures/', import.meta.url)
const load = (f: string): any => JSON.parse(readFileSync(new URL(f, FX), 'utf8'))
const graph: any = load('graph.json')
const details: any[] = Object.values(load('deal_detail.json'))
const t = (s: string): number => simToMs(s)
const ring = graph.clusters.find((c: any) => c.id === 'C-001')

test('default cluster is the first held cluster; deals map to their cluster', () => {
  assert.equal(defaultClusterId(graph), 'C-001')
  assert.equal(clusterForDeal(graph, 'Juniper Creek Nursery')?.id, 'C-001')
  assert.equal(clusterForDeal(graph, 'Cedar Ridge Welding')?.id, 'C-002')
  assert.equal(clusterForDeal(graph, 'Lone Star Dental'), null)
})

test('edges appear at first_visible_at; entities appear with their first edge', () => {
  const before = graphAt(graph, details, t('2026-11-03T08:19-06:00'), ring)
  assert.ok(!before.nodes.some((n) => n.id === 'ent-phone-01'))
  assert.ok(before.edges.every((e) => e.source.startsWith('H-')))
  const after = graphAt(graph, details, t('2026-11-03T08:20-06:00'), ring)
  assert.ok(after.nodes.some((n) => n.id === 'ent-phone-01'))
  assert.ok(after.edges.some((e) => e.source === 'Bayou Bistro & Grill'))
  // Juniper's edges are not yet visible, so the phone is shared by one ring deal only: not lit.
  assert.ok(!after.ringEntities.has('ent-phone-01'))
})

test('at end of run the ring lights rare shared entities only and leaves clean deals in the background', () => {
  const v = graphAt(graph, details, t('2026-11-06T18:00-06:00'), ring)
  assert.deepEqual([...v.ringEntities].sort(), ['ent-pdfprod-01', 'ent-phone-01', 'ent-preparer-01'])
  assert.ok(!v.ringEntities.has('ent-bank-01'), 'a common bank is never lit')
  assert.ok(v.focus.has('Bayou Bistro & Grill') && v.focus.has('H-0000-001') && v.focus.has('ent-bank-01'))
  assert.deepEqual(v.backgroundDeals.sort(), ['Cedar Ridge Welding', 'Lone Star Dental', "Mama Rosa's Kitchen"])
  assert.equal(sharersOf(v, 'ent-preparer-01').length, 3)
})

test('the ring cluster turns red at the first hold; the watch cluster never does', () => {
  assert.deepEqual(clusterHoldAt(ring, details, t('2026-11-03T11:29-06:00')), { held: false, at: null })
  assert.deepEqual(clusterHoldAt(ring, details, t('2026-11-03T11:30-06:00')), { held: true, at: '2026-11-03T11:30-06:00' })
  const watch = graph.clusters.find((c: any) => c.id === 'C-002')
  assert.equal(clusterHoldAt(watch, details, t('2026-11-06T18:00-06:00')).held, false)
})

test('latest hold for the clock-bar line', () => {
  assert.equal(latestHold(details, t('2026-11-03T11:00-06:00')), null)
  assert.equal(latestHold(details, t('2026-11-03T12:00-06:00'))?.folder, 'Bayou Bistro & Grill')
  const late = latestHold(details, t('2026-11-03T15:00-06:00'))
  assert.equal(late?.folder, 'Juniper Creek Nursery')
  assert.equal(late?.linked, 2)
})

test('red-team totals sum attempts, bypasses and false flags', () => {
  const r = load('redteam.json')
  assert.deepEqual(redteamTotals(r.tricks), { attempts: 30, bypasses: 5, falseFlags: 1 })
})

test('entity kinds group into three muted tints', () => {
  assert.equal(kindGroup('preparer'), 'document')
  assert.equal(kindGroup('bank'), 'money')
  assert.equal(kindGroup('phone'), 'identity')
})
