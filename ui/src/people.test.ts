import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { DIRECTORY, personLabel, roleForAlert, roleForRequest } from './people.ts'

test('DIRECTORY is empty: no staff names live in the UI or in git (D-22)', () => {
  assert.deepEqual(Object.keys(DIRECTORY), [])
})

test('personLabel on any id gives the role label with the raw id only in the title', () => {
  const a = personLabel('credit.nakamura', 'Credit officer')
  assert.equal(a.label, 'Credit officer')
  assert.equal(a.title, 'Staff id credit.nakamura')
  const b = personLabel('mgr.ibarra', 'Escalation contact')
  assert.equal(b.label, 'Escalation contact')
  assert.ok(!b.label.includes('ibarra'))
  assert.equal(b.title, 'Staff id mgr.ibarra')
})

test('personLabel uses a name only when the supplied directory has the id', () => {
  const dir = { 'test.person': 'Test Person' }
  assert.deepEqual(personLabel('test.person', 'Fraud Operations', dir), { label: 'Test Person', title: 'Staff id test.person' })
  assert.equal(personLabel('someone.else', 'Fraud Operations', dir).label, 'Fraud Operations')
})

test('personLabel never exposes an id as label even when the id equals a role word', () => {
  assert.equal(personLabel('borrower', 'Borrower').label, 'Borrower')
})

test('roleForRequest covers all seven request types', () => {
  assert.equal(roleForRequest('credit_decision'), 'Credit officer')
  assert.equal(roleForRequest('committee_approval'), 'Committee approver')
  assert.equal(roleForRequest('commitment_signoff'), 'Relationship manager')
  assert.equal(roleForRequest('funding_release'), 'Closing and funding')
  assert.equal(roleForRequest('fraud_hold'), 'Fraud Operations')
  assert.equal(roleForRequest('fraud_release'), 'Fraud Operations')
  assert.equal(roleForRequest('info_request'), 'Borrower')
  assert.equal(roleForRequest('unknown_type'), 'Staff')
})

test('roleForAlert: stage owner for an alert, escalation contact for an escalation', () => {
  assert.equal(roleForAlert('alert'), 'Stage owner')
  assert.equal(roleForAlert('escalation'), 'Escalation contact')
})

test('people.ts holds no path into the gitignored data folder and no contact details', () => {
  const src = readFileSync(new URL('./people.ts', import.meta.url), 'utf8')
  assert.ok(!/data\//.test(src))
  assert.ok(!/teams\.json|contacts|@[a-z]+\./i.test(src))
})
