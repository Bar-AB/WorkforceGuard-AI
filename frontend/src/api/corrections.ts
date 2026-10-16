import {
  useIsMutating,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
  type MutationState,
  type QueryClient,
} from '@tanstack/react-query'
import { apiGet, apiPost, isCutRequest } from './client.ts'

export type Verdict = 'approve' | 'reject'

export interface Correction {
  id: string
  finding_id: string
  target_table: string
  target_id: string
  patch: Record<string, unknown>
  current: Record<string, unknown> | null
  before: Record<string, unknown> | null
  reason: string
  status: string
  proposed_by: string
  created_at: string
  decided_by_user_id: string | null
  decided_at: string | null
  judge_supported: boolean | null
  judge_reason: string | null
  judged_by: string | null
}

export interface SuggestOutcome {
  proposal: Correction | null
  reason: string | null
}

export type SuggestState = MutationState<SuggestOutcome, Error, void, unknown>

const SUGGEST_KEY = ['suggest'] as const
const SUGGEST_TIMEOUT_MS = 12 * 60 * 1000
const SUGGEST_LOST =
  'Lost the connection. The fix may still be in progress. Open the finding again in a few minutes.'

function suggestKey(findingId: string): readonly string[] {
  return [...SUGGEST_KEY, findingId]
}

function findingCorrectionsPath(findingId: string): string {
  return `/api/v1/findings/${encodeURIComponent(findingId)}/corrections`
}

export function fetchCorrections(findingId: string): Promise<Correction[]> {
  return apiGet<Correction[]>(findingCorrectionsPath(findingId))
}

export async function suggestCorrection(findingId: string): Promise<SuggestOutcome> {
  try {
    return await apiPost<SuggestOutcome>(findingCorrectionsPath(findingId), {
      signal: AbortSignal.timeout(SUGGEST_TIMEOUT_MS),
    })
  } catch (error) {
    throw isCutRequest(error) ? new Error(SUGGEST_LOST, { cause: error }) : error
  }
}

export function decideCorrection(correctionId: string, verdict: Verdict): Promise<Correction> {
  return apiPost<Correction>(`/api/v1/corrections/${encodeURIComponent(correctionId)}/${verdict}`, {
    asUser: true,
  })
}

export function useCorrections(findingId: string) {
  return useQuery({
    queryKey: ['corrections', findingId],
    queryFn: () => fetchCorrections(findingId),
  })
}

export function useSuggestCorrection(findingId: string): () => void {
  const queryClient = useQueryClient()
  const suggest = useMutation({
    mutationKey: suggestKey(findingId),
    mutationFn: () => suggestCorrection(findingId),
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ['corrections', findingId] }),
        queryClient.invalidateQueries({ queryKey: ['finding', findingId] }),
        queryClient.invalidateQueries({ queryKey: ['findings'] }),
      ]),
  })
  return () => {
    if (queryClient.isMutating({ mutationKey: SUGGEST_KEY }) === 0) {
      suggest.mutate()
    }
  }
}

export function useAnySuggestRunning(): boolean {
  return useIsMutating({ mutationKey: SUGGEST_KEY }) > 0
}

function forgetSuggestOutcomes(queryClient: QueryClient, findingId: string): void {
  const cache = queryClient.getMutationCache()
  cache
    .findAll({ mutationKey: suggestKey(findingId) })
    .filter((mutation) => mutation.state.status !== 'pending')
    .forEach((mutation) => cache.remove(mutation))
}

export function useLatestSuggest(findingId: string): SuggestState | undefined {
  const states = useMutationState({
    filters: { mutationKey: suggestKey(findingId) },
    select: (mutation) => mutation.state as SuggestState,
  })
  return states.at(-1)
}

export function useDecideCorrection(findingId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ correctionId, verdict }: { correctionId: string; verdict: Verdict }) =>
      decideCorrection(correctionId, verdict),
    onSettled: () => {
      forgetSuggestOutcomes(queryClient, findingId)
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: ['corrections', findingId] }),
        queryClient.invalidateQueries({ queryKey: ['finding', findingId] }),
        queryClient.invalidateQueries({ queryKey: ['findings'] }),
      ])
    },
  })
}
