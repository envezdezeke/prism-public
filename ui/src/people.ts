// Staff display (D-14, D-17, D-22). There are no staff names in the UI or in git: every id shows a
// role label with the raw id in a tooltip, and real names arrive from the engine at the Phase 7 live
// switch. DIRECTORY stays empty on purpose; the fixtures' unknown ids are worked around, never changed.
export const DIRECTORY: Readonly<Record<string, string>> = {}

export interface PersonLabel {
  label: string
  title: string
}

// A name when the directory has the id, otherwise the role. The raw id only ever goes in the title.
export function personLabel(
  id: string,
  fallbackRole: string,
  directory: Readonly<Record<string, string>> = DIRECTORY,
): PersonLabel {
  const name = Object.hasOwn(directory, id) ? directory[id] : undefined
  return { label: name ?? fallbackRole, title: `Staff id ${id}` }
}

const REQUEST_ROLES: Record<string, string> = {
  credit_decision: 'Credit officer',
  committee_approval: 'Committee approver',
  commitment_signoff: 'Relationship manager',
  funding_release: 'Closing and funding',
  fraud_hold: 'Fraud Operations',
  fraud_release: 'Fraud Operations',
  info_request: 'Borrower',
}

export function roleForRequest(type: string): string {
  return REQUEST_ROLES[type] ?? 'Staff'
}

// Alerts carry no request type: an alert goes to the stage owner, an escalation to the escalation contact.
export function roleForAlert(level: string): string {
  return level === 'escalation' ? 'Escalation contact' : 'Stage owner'
}
