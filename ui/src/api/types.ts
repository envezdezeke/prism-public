// Aliases over the generated schema (src/api/schema.ts). Never hand-edit the schema.
import type { components } from './schema.ts'

type Schemas = components['schemas']

export type Clock = Schemas['Clock']
export type DealSummary = Schemas['DealSummary']
export type DealDetail = Schemas['DealDetail']
export type StageLogEntry = Schemas['StageLogEntry']
export type Fact = Schemas['Fact']
export type InboxItem = Schemas['InboxItem']
export type AlertFeedItem = Schemas['AlertFeedItem']
export type FraudGraph = Schemas['FraudGraph']
export type GraphNode = Schemas['GraphNode']
export type GraphEdge = Schemas['GraphEdge']
export type Cluster = Schemas['Cluster']
export type InvestigatorTrace = Schemas['InvestigatorTrace']
export type TraceStep = Schemas['TraceStep']
export type RedTeamReport = Schemas['RedTeamReport']

export type Stage = StageLogEntry['stage']

// The six lending stages in contract order (prism/contract.py).
export const STAGES: readonly Stage[] = [
  'Intake',
  'Credit',
  'Committee',
  'Product',
  'Committed',
  'Closed',
]
