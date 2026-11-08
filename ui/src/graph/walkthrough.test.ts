import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { walkthroughTrace } from './walkthrough.ts'
import type { GGraph } from './graphview.ts'

const graph = JSON.parse(readFileSync(new URL('../../../fixtures/graph.json', import.meta.url), 'utf-8')) as GGraph

test('every cluster gets a replayable walk-through', () => {
  for (const c of graph.clusters) {
    const t = walkthroughTrace(graph, c)
    assert.equal(t.cluster_id, c.id)
    assert.equal(t.mode, 'off')
    assert.ok(t.steps.length >= 1)
    assert.equal(t.steps[0].tool, 'cluster_summary')
    t.steps.forEach((s, i) => assert.equal(s.step, i + 1))
  }
})

test('the walk-through is deterministic', () => {
  const c = graph.clusters[0]
  assert.deepEqual(walkthroughTrace(graph, c), walkthroughTrace(graph, c))
})
