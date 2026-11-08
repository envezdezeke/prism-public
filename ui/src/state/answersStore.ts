import { create } from 'zustand'
import type { OverlayAnswer } from '../time/timeview.ts'

// In-session inbox answers (D-21). The mock does not persist replies, so the answer lives here, in
// memory only: never in local storage, never in the query cache. A reload restores pending.
interface AnswersState {
  answers: Record<string, OverlayAnswer>
  record: (id: string, a: OverlayAnswer) => void
}

export const useAnswersStore = create<AnswersState>((set) => ({
  answers: {},
  record: (id, a) => set((s) => ({ answers: { ...s.answers, [id]: a } })),
}))
