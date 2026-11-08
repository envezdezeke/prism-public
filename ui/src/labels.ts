// Plain-words labels. Display only: the wire value is never changed, and a string that matches no
// rule falls through unchanged (D-01). Wording follows 02-UI-SPEC "Formatting and Labels".
import { fmtMoney, fmtSimTime } from './lib/format.ts'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const DOC_LABELS: Record<string, string> = {
  application: 'application',
  guarantor_id: 'guarantor ID',
  certificate_of_formation: 'certificate of formation',
  ein_letter: 'EIN letter',
  financial_statements: 'financial statements',
  tax_return: 'tax return',
  equipment_quote: 'equipment quote',
}

const MISMATCH_LABELS: Record<string, string> = {
  legal_name: 'legal name',
  ein: 'EIN',
  amount_requested: 'amount requested',
  guarantor_name: 'guarantor name',
  guarantor_dob: 'guarantor date of birth',
  guarantor_address: 'guarantor address',
  annual_revenue: 'annual revenue',
  bank_balance: 'bank balance',
}

const OLD_TRICK_LABELS: Record<string, string> = {
  STMT_TAMPER: 'statement tampering check',
  REV_INFLATION: 'revenue inflation check',
  ID_MISMATCH: 'ID mismatch check',
  DUP_EIN: 'duplicate EIN check',
  SHELL_ADDRESS: 'shell address check',
}

function docLabel(code: string): string | null {
  const stmt = /^bank_statement_(\d{4})-(\d{2})$/.exec(code)
  if (stmt) {
    const month = MONTHS[Number(stmt[2]) - 1]
    return month ? `bank statement, ${month} ${stmt[1]}` : null
  }
  return DOC_LABELS[code] ?? null
}

// "MISSING_DOC:tax_return" -> "Missing document: tax return". The grammar is prism/contract.py ISSUE_RE.
export function issueLabel(issue: string): string {
  const i = issue.indexOf(':')
  if (i < 0) return issue
  const kind = issue.slice(0, i)
  const arg = issue.slice(i + 1)
  if (kind === 'MISSING_DOC' || kind === 'MISSING_PAGE') {
    const doc = docLabel(arg)
    return doc === null ? issue : `${kind === 'MISSING_DOC' ? 'Missing document' : 'Missing page'}: ${doc}`
  }
  if (kind === 'MISMATCH') {
    const field = MISMATCH_LABELS[arg]
    return field === undefined ? issue : `Mismatch: ${field}`
  }
  if (kind === 'OLD_TRICK') {
    const check = OLD_TRICK_LABELS[arg]
    return check === undefined ? issue : `Known-fraud check failed: ${check}`
  }
  return issue
}

const REQUEST_TYPE_LABELS: Record<string, string> = {
  credit_decision: 'Credit decision',
  committee_approval: 'Committee approval',
  commitment_signoff: 'Commitment sign-off',
  funding_release: 'Funding release',
  fraud_hold: 'Fraud hold',
  fraud_release: 'Fraud release',
  info_request: 'Borrower information request',
}

export function requestTypeLabel(type: string): string {
  return REQUEST_TYPE_LABELS[type] ?? type
}

const OPTION_LABELS: Record<string, string> = {
  approve: 'Approve',
  decline: 'Decline',
  request_info: 'Request more information',
  confirm_hold: 'Confirm hold',
  release_hold: 'Release hold',
}

export function optionLabel(option: string): string {
  return OPTION_LABELS[option] ?? option
}

// The nine contract fact keys (prism/contract.py ExtractedFacts), in contract order.
export const factFields = [
  { key: 'legal_name', label: 'Legal name' },
  { key: 'ein', label: 'EIN' },
  { key: 'amount_requested', label: 'Amount requested' },
  { key: 'purpose_category', label: 'Purpose' },
  { key: 'product', label: 'Product' },
  { key: 'term_months', label: 'Term' },
  { key: 'guarantor_name', label: 'Guarantor' },
  { key: 'annual_revenue_stated', label: 'Annual revenue (stated)' },
  { key: 'needed_by', label: 'Needed by' },
] as const

export type FactKey = (typeof factFields)[number]['key']

const PURPOSE_LABELS: Record<string, string> = {
  working_capital: 'Working capital',
  equipment: 'Equipment',
  inventory: 'Inventory',
  payroll: 'Payroll',
  expansion: 'Expansion',
  refinance: 'Refinance',
}

const SIM_SHAPE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}-06:00$/

// Money for the two amount facts, "60 months" for the term, house-style time for needed_by,
// a label for the purpose; every other value, and any value of an unexpected shape, as the string itself.
export function factValueText(key: string, value: string | number): string {
  if ((key === 'amount_requested' || key === 'annual_revenue_stated') && typeof value === 'number') {
    return fmtMoney(value)
  }
  if (key === 'term_months' && typeof value === 'number') return `${value} months`
  if (key === 'needed_by' && typeof value === 'string' && SIM_SHAPE.test(value)) return fmtSimTime(value)
  if (key === 'purpose_category' && typeof value === 'string') return PURPOSE_LABELS[value] ?? value
  return String(value)
}

// MESSAGE_TEMPLATES opens the 200 percent alert with "Second escalation:".
export function alertLevelLabel(level: string, message: string): string {
  if (level === 'alert') return 'Alert'
  return message.startsWith('Second escalation') ? 'Second escalation' : 'Escalation'
}

// Green is for approvals only (02-UI-SPEC Color): a released hold is not an approval.
export function isClearedAnswer(answer: string | null | undefined): boolean {
  return answer === 'approve'
}

const STAGE_OWNER_ROLES: Record<string, string> = {
  Intake: 'Intake team',
  Credit: 'Credit officer',
  Committee: 'Committee approver',
  Product: 'Product specialist',
  Committed: 'Relationship manager',
  Closed: 'Closing and funding',
}

export function stageOwnerRole(stage: string): string {
  return STAGE_OWNER_ROLES[stage] ?? 'Stage owner'
}

// Mirrors FAMILY_LABELS in prism/contract.py (the API does not serve it). Order is F1 to F6.
export const FAMILIES: readonly { id: string; label: string }[] = [
  { id: 'F1', label: 'Shared rare entity' },
  { id: 'F2', label: 'Novelty or burst' },
  { id: 'F3', label: 'Lookalike vendor or account' },
  { id: 'F4', label: 'Document forensics' },
  { id: 'F5', label: 'Deposit pattern' },
  { id: 'F6', label: 'Terms shape' },
]

const ENTITY_KIND_LABELS: Record<string, string> = {
  preparer: 'Tax preparer',
  phone: 'Phone',
  registered_agent: 'Registered agent',
  address: 'Address',
  vendor: 'Vendor',
  remit_account: 'Remittance account',
  pdf_producer: 'PDF producer',
  bank: 'Bank',
  guarantor: 'Guarantor',
}

export function entityKindLabel(kind: string | null | undefined): string {
  if (kind == null) return 'Entity'
  return ENTITY_KIND_LABELS[kind] ?? kind
}

// "rarity 0.96": the engine's 0-to-1 value with two decimals, never re-binned into a word.
export function rarityText(r: number): string {
  return `rarity ${r.toFixed(2)}`
}
