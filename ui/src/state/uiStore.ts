import { create } from 'zustand'
import { buildHash, parseHash } from '../route.ts'
import type { Route } from '../route.ts'

interface UiState {
  route: Route
  openDeal: (folder: string) => void
  showPipeline: () => void
  go: (route: Route) => void
  // Top-bar deal search: filters the pipeline board and offers jump-to-deal matches.
  query: string
  setQuery: (q: string) => void
  railCollapsed: boolean
  toggleRail: () => void
}

// Node (tests) has no location; the store then simply holds the route in memory.
const hasLocation = (): boolean => typeof location !== 'undefined'

function initialRoute(): Route {
  // An empty hash lands on the Overview (the page that says where to start).
  if (hasLocation() && (location.hash === '' || location.hash === '#')) return { view: 'overview' }
  return hasLocation() ? parseHash(location.hash) : { view: 'pipeline' }
}

// The URL hash is the source of truth (D-02): a link or Back changes the hash, the listener below
// moves the store, and a store action writes the hash. Comparing built strings stops the two loops.
export const useUiStore = create<UiState>((set, get) => {
  const go = (route: Route): void => {
    const hash = buildHash(route)
    if (hasLocation() && location.hash !== hash) location.hash = hash
    if (buildHash(get().route) !== hash) set({ route })
  }
  return {
    route: initialRoute(),
    openDeal: (folder) => go({ view: 'deal', folder }),
    showPipeline: () => go({ view: 'pipeline' }),
    go,
    query: '',
    setQuery: (query) => set({ query }),
    railCollapsed: false,
    toggleRail: () => set({ railCollapsed: !get().railCollapsed }),
  }
})

// Hash changes made outside the store (a link click, Back, a pasted URL) update the route.
export function syncRouteFromHash(): void {
  if (!hasLocation()) return
  const next = parseHash(location.hash)
  if (buildHash(useUiStore.getState().route) !== buildHash(next)) useUiStore.setState({ route: next })
}

if (typeof window !== 'undefined') window.addEventListener('hashchange', syncRouteFromHash)
