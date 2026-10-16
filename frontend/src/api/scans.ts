import {
  useMutation,
  useMutationState,
  useQueryClient,
  type MutationState,
} from '@tanstack/react-query'
import { apiPost, isCutRequest } from './client.ts'

export interface ScanResult {
  scan_id: string
  findings_detected: number
  findings_created: number
  rules_skipped: { rule_id: string; reason: string }[]
  findings_explained: number
  explanations_fallback: number
  explanations_fallback_by_reason: Record<string, number>
  explanations_deferred: number
}

export type ScanState = MutationState<ScanResult, Error, void, unknown>

const SCAN_KEY = ['scan'] as const
const SCAN_TIMEOUT_MS = 45 * 60 * 1000
const SCAN_LOST =
  'Lost the connection. The scan may still be running on the server. Check the list again in a few minutes.'

export async function runScan(): Promise<ScanResult> {
  try {
    return await apiPost<ScanResult>('/api/v1/scans', {
      signal: AbortSignal.timeout(SCAN_TIMEOUT_MS),
    })
  } catch (error) {
    throw isCutRequest(error) ? new Error(SCAN_LOST, { cause: error }) : error
  }
}

export function useRunScan(): () => void {
  const queryClient = useQueryClient()
  const scan = useMutation({
    mutationKey: SCAN_KEY,
    mutationFn: () => runScan(),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['findings'] }),
  })
  return () => {
    if (queryClient.isMutating({ mutationKey: SCAN_KEY }) === 0) {
      scan.mutate()
    }
  }
}

export function useLatestScan(): ScanState | undefined {
  const states = useMutationState({
    filters: { mutationKey: SCAN_KEY },
    select: (mutation) => mutation.state as ScanState,
  })
  return states.at(-1)
}
