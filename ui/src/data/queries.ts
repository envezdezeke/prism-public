import { useMemo } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { getAlerts, getDeal, getDeals, getGraph, getInbox, getInvestigator, getRedteam, describeError } from '../api/client.ts'
import type { DealDetail } from '../api/types.ts'

// One deal's detail. Shares the cache key with the board's per-deal queries, so a deal the board has
// already loaded opens with no extra request, and a failure here belongs to this deal alone (UI-04).
export function useDealDetail(folder: string) {
  return useQuery({
    queryKey: ['deal', folder],
    queryFn: () => getDeal(folder),
  })
}

export function useGraph() {
  return useQuery({ queryKey: ['graph'], queryFn: getGraph })
}

export function useInvestigator(clusterId: string | null) {
  return useQuery({
    queryKey: ['investigator', clusterId],
    queryFn: () => getInvestigator(clusterId as string),
    enabled: clusterId !== null,
    retry: false,
  })
}

export function useRedteam() {
  return useQuery({ queryKey: ['redteam'], queryFn: getRedteam })
}

export function useAlerts() {
  return useQuery({ queryKey: ['alerts'], queryFn: getAlerts })
}

export function useInbox() {
  return useQuery({ queryKey: ['inbox'], queryFn: getInbox })
}

export function useDeals() {
  return useQuery({ queryKey: ['deals'], queryFn: getDeals })
}

// There is no bulk detail endpoint, and the time derivation needs every stage_log.
export function useDealDetails(folders: string[]) {
  return useQueries({
    queries: folders.map((folder) => ({
      queryKey: ['deal', folder],
      queryFn: () => getDeal(folder),
    })),
    combine: (results) => ({
      details: results.flatMap((r) => (r.data ? [r.data as DealDetail] : [])),
      isLoading: results.some((r) => r.isPending),
      errors: results.flatMap((r) => (r.error ? [describeError(r.error)] : [])),
      refetchFailed: () => results.forEach((r) => r.error && void r.refetch()),
    }),
  })
}

// Details for every deal on the board (shared query cache, so callers pay nothing extra).
export function useBoardDetails() {
  const deals = useDeals()
  const folders = useMemo(() => (deals.data ?? []).map((d) => d.folder), [deals.data])
  return useDealDetails(folders)
}
