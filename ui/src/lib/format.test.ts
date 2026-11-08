import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  DASH,
  NULL_TOOLTIP,
  confidenceTitle,
  confidenceWord,
  fmtHours,
  fmtMoney,
  fmtPct,
  fmtSimDay,
  fmtSla,
} from './format.ts'

test('fmtMoney: whole dollars with grouping, no decimals', () => {
  assert.equal(fmtMoney(1200000), '$1,200,000')
  assert.equal(fmtMoney(410000), '$410,000')
  assert.equal(fmtMoney(85000), '$85,000')
})

test('fmtSla: duration over limit with percent, middle-dot separator', () => {
  assert.equal(fmtSla(0.545139, 48), '26h 10m / 48h · 55%')
  assert.equal(fmtSla(1.125, 48), '54h 00m / 48h · 113%')
  assert.equal(fmtSla(0.4375, 8), '3h 30m / 8h · 44%')
})

test('fmtHours and fmtPct round the way the fixtures do', () => {
  assert.equal(fmtHours(48), '48h')
  assert.equal(fmtHours(1.5), '1.5h')
  assert.equal(fmtPct(0.455), '46%')
  assert.equal(fmtPct(1.13), '113%')
})

test('confidenceWord bins at 0.9 and 0.6; confidenceTitle is a whole percent', () => {
  assert.equal(confidenceWord(0.98), 'High')
  assert.equal(confidenceWord(0.9), 'High')
  assert.equal(confidenceWord(0.89), 'Medium')
  assert.equal(confidenceWord(0.6), 'Medium')
  assert.equal(confidenceWord(0.59), 'Low')
  assert.equal(confidenceTitle(0.98), '98% confidence')
})

test('fmtSimDay is the day part of the house style; null is a dash with a tooltip', () => {
  assert.equal(fmtSimDay('2026-11-06T15:00-06:00'), 'Fri 11/06')
  assert.equal(DASH, '—')
  assert.equal(NULL_TOOLTIP, 'Not found in documents')
})
