// Hash routes, no router library (D-02). Folder names contain spaces, ampersands and apostrophes,
// so the folder (and a cluster id) is always passed through encodeURIComponent.
export type Route =
  | { view: 'overview' }
  | { view: 'upload' }
  | { view: 'pipeline' }
  | { view: 'deal'; folder: string }
  | { view: 'inbox' }
  | { view: 'alerts' }
  // The fraud graph (D-26). clusterId null shows the whole graph focused on the first held cluster.
  | { view: 'graph'; clusterId: string | null }

const DEAL_PREFIX = '#/deals/'
const GRAPH_PREFIX = '#/graph/'

export function buildHash(route: Route): string {
  switch (route.view) {
    case 'deal':
      return `${DEAL_PREFIX}${encodeURIComponent(route.folder)}`
    case 'overview':
      return '#/overview'
    case 'upload':
      return '#/upload'
    case 'inbox':
      return '#/inbox'
    case 'alerts':
      return '#/alerts'
    case 'graph':
      return route.clusterId == null ? '#/graph' : `${GRAPH_PREFIX}${encodeURIComponent(route.clusterId)}`
    default:
      return '#/pipeline'
  }
}

function decodeTail(hash: string, prefix: string): string | null {
  try {
    const v = decodeURIComponent(hash.slice(prefix.length))
    return v === '' ? null : v
  } catch {
    return null // malformed escape sequence
  }
}

// Anything unknown or malformed goes to the pipeline; this never throws (T-02-10).
export function parseHash(hash: string): Route {
  if (hash.startsWith(DEAL_PREFIX)) {
    const folder = decodeTail(hash, DEAL_PREFIX)
    if (folder !== null) return { view: 'deal', folder }
    return { view: 'pipeline' }
  }
  if (hash === '#/overview') return { view: 'overview' }
  if (hash === '#/upload') return { view: 'upload' }
  if (hash === '#/inbox') return { view: 'inbox' }
  if (hash === '#/alerts') return { view: 'alerts' }
  if (hash === '#/graph' || hash === GRAPH_PREFIX) return { view: 'graph', clusterId: null }
  if (hash.startsWith(GRAPH_PREFIX)) {
    const clusterId = decodeTail(hash, GRAPH_PREFIX)
    return { view: 'graph', clusterId }
  }
  return { view: 'pipeline' }
}
