import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  alertLevelLabel, factFields, factValueText, isClearedAnswer, issueLabel, optionLabel,
  requestTypeLabel, stageOwnerRole,
} from './labels.ts'

const FX = new URL('../../fixtures/', import.meta.url)
const load = (f: string): any => JSON.parse(readFileSync(new URL(f, FX), 'utf8'))

test('issueLabel: documents, pages, mismatches and known-fraud checks in plain words', () => {
  assert.equal(issueLabel('MISSING_DOC:ein_letter'), 'Missing document: EIN letter')
  assert.equal(issueLabel('MISSING_DOC:guarantor_id'), 'Missing document: guarantor ID')
  assert.equal(issueLabel('MISSING_DOC:tax_return'), 'Missing document: tax return')
  assert.equal(issueLabel('MISSING_PAGE:bank_statement_2026-09'), 'Missing page: bank statement, Sep 2026')
  assert.equal(issueLabel('MISSING_PAGE:bank_statement_2026-12'), 'Missing page: bank statement, Dec 2026')
  assert.equal(issueLabel('MISMATCH:guarantor_address'), 'Mismatch: guarantor address')
  assert.equal(issueLabel('MISMATCH:ein'), 'Mismatch: EIN')
  assert.equal(issueLabel('MISMATCH:guarantor_dob'), 'Mismatch: guarantor date of birth')
  assert.equal(issueLabel('OLD_TRICK:SHELL_ADDRESS'), 'Known-fraud check failed: shell address check')
})

test('issueLabel: all five known-fraud checks have a label and unknown strings pass through unchanged', () => {
  for (const code of ['STMT_TAMPER', 'REV_INFLATION', 'ID_MISMATCH', 'DUP_EIN', 'SHELL_ADDRESS']) {
    const out = issueLabel(`OLD_TRICK:${code}`)
    assert.match(out, /^Known-fraud check failed: .+ check$/, code)
    assert.ok(!out.includes('_'), code)
  }
  assert.equal(issueLabel('OLD_TRICK:STMT_TAMPER'), 'Known-fraud check failed: statement tampering check')
  assert.equal(issueLabel('SOMETHING_ELSE:x'), 'SOMETHING_ELSE:x')
  assert.equal(issueLabel('MISSING_DOC:unheard_of'), 'MISSING_DOC:unheard_of')
  assert.equal(issueLabel(''), '')
})

test('issueLabel covers every issue string in the fixtures', () => {
  const detail = load('deal_detail.json')
  for (const d of Object.values<any>(detail)) {
    for (const s of [...d.issues, ...d.resolved_issues]) assert.notEqual(issueLabel(s), s, s)
  }
})

test('requestTypeLabel maps all seven request types', () => {
  assert.equal(requestTypeLabel('credit_decision'), 'Credit decision')
  assert.equal(requestTypeLabel('committee_approval'), 'Committee approval')
  assert.equal(requestTypeLabel('commitment_signoff'), 'Commitment sign-off')
  assert.equal(requestTypeLabel('funding_release'), 'Funding release')
  assert.equal(requestTypeLabel('fraud_hold'), 'Fraud hold')
  assert.equal(requestTypeLabel('fraud_release'), 'Fraud release')
  assert.equal(requestTypeLabel('info_request'), 'Borrower information request')
  assert.equal(requestTypeLabel('mystery'), 'mystery')
})

test('optionLabel maps the five options and returns an unknown option unchanged', () => {
  assert.equal(optionLabel('approve'), 'Approve')
  assert.equal(optionLabel('decline'), 'Decline')
  assert.equal(optionLabel('request_info'), 'Request more information')
  assert.equal(optionLabel('confirm_hold'), 'Confirm hold')
  assert.equal(optionLabel('release_hold'), 'Release hold')
  assert.equal(optionLabel('escalate'), 'escalate')
})

test('factFields lists the nine contract fact keys in contract order with labels', () => {
  assert.deepEqual(
    factFields.map((f) => f.key),
    ['legal_name', 'ein', 'amount_requested', 'purpose_category', 'product', 'term_months',
      'guarantor_name', 'annual_revenue_stated', 'needed_by'],
  )
  assert.deepEqual(
    factFields.map((f) => f.label),
    ['Legal name', 'EIN', 'Amount requested', 'Purpose', 'Product', 'Term', 'Guarantor',
      'Annual revenue (stated)', 'Needed by'],
  )
  // The keys are exactly the keys the fixtures carry (no invented fields).
  const fixtureKeys = Object.keys(Object.values<any>(load('deal_detail.json'))[0].facts)
  assert.deepEqual([...factFields.map((f) => f.key)].sort(), [...fixtureKeys].sort())
})

test('factValueText formats money, term, needed_by and purpose; anything else is the string itself', () => {
  assert.equal(factValueText('amount_requested', 410000), '$410,000')
  assert.equal(factValueText('annual_revenue_stated', 1850000), '$1,850,000')
  assert.equal(factValueText('term_months', 60), '60 months')
  assert.equal(factValueText('needed_by', '2026-11-06T15:00-06:00'), 'Fri 11/06 3:00 PM CT')
  assert.equal(factValueText('purpose_category', 'working_capital'), 'Working capital')
  assert.equal(factValueText('purpose_category', 'refinance'), 'Refinance')
  assert.equal(factValueText('purpose_category', 'something_new'), 'something_new')
  assert.equal(factValueText('legal_name', 'Bayou Bistro & Grill LLC'), 'Bayou Bistro & Grill LLC')
  assert.equal(factValueText('ein', '12-0000004'), '12-0000004')
  // A needed_by that is not a sim time is shown as given, never thrown on.
  assert.equal(factValueText('needed_by', 'soon'), 'soon')
  // A non-numeric money value is shown as given.
  assert.equal(factValueText('amount_requested', 'n/a'), 'n/a')
})

test('alertLevelLabel', () => {
  assert.equal(alertLevelLabel('alert', 'Mama Rosa: approval has waited 48 hours.'), 'Alert')
  assert.equal(alertLevelLabel('escalation', 'Second escalation: committee approval has waited 100 hours.'), 'Second escalation')
  assert.equal(alertLevelLabel('escalation', 'Mama Rosa: waited 52 hours. Please step in.'), 'Escalation')
})

test('isClearedAnswer is true for approve only (green is reserved for approvals)', () => {
  assert.equal(isClearedAnswer('approve'), true)
  assert.equal(isClearedAnswer('release_hold'), false)
  assert.equal(isClearedAnswer('decline'), false)
  assert.equal(isClearedAnswer(null), false)
  assert.equal(isClearedAnswer(undefined), false)
})

test('stageOwnerRole covers the six stages', () => {
  assert.equal(stageOwnerRole('Intake'), 'Intake team')
  assert.equal(stageOwnerRole('Credit'), 'Credit officer')
  assert.equal(stageOwnerRole('Committee'), 'Committee approver')
  assert.equal(stageOwnerRole('Closed'), 'Closing and funding')
  assert.equal(stageOwnerRole('Elsewhere'), 'Stage owner')
})
