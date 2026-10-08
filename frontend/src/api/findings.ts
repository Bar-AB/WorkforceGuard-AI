import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { apiGet } from './client.ts'

export const SEVERITIES = ['low', 'medium', 'high'] as const
export type Severity = (typeof SEVERITIES)[number]

export const RULE_LABELS: Readonly<Record<string, string>> = { overtime_breach: 'Overtime breach' }

export const PAGE_SIZE = 50

export interface FindingFilters {
  ruleId?: string
  severity?: Severity
  occurredFrom?: string
  occurredTo?: string
}

export interface FindingSummary {
  id: string
  employee_id: string
  rule_id: string
  rule_version: string
  severity: string
  status: string
  summary: string
  occurred_on: string
  detected_at: string
}

export interface FindingsPage {
  items: FindingSummary[]
  next_cursor: string | null
}

export interface FindingDetail extends FindingSummary {
  evidence: Record<string, unknown>
  explanation: string | null
  explanation_source: string | null
  explanation_prompt_version: string | null
}

export function ruleLabel(ruleId: string): string {
  return RULE_LABELS[ruleId] ?? ruleId
}

export function filtersToSearchParams(filters: FindingFilters): URLSearchParams {
  const entries: [string, string | undefined][] = [
    ['rule_id', filters.ruleId],
    ['severity', filters.severity],
    ['occurred_from', filters.occurredFrom],
    ['occurred_to', filters.occurredTo],
  ]
  return new URLSearchParams(
    entries.filter((entry): entry is [string, string] => entry[1] !== undefined),
  )
}

export function fetchFindings(
  filters: FindingFilters,
  cursor: string | null,
): Promise<FindingsPage> {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) })
  filtersToSearchParams(filters).forEach((value, name) => params.append(name, value))
  if (cursor !== null) {
    params.append('cursor', cursor)
  }
  return apiGet<FindingsPage>('/api/v1/findings', params)
}

export function fetchFinding(id: string): Promise<FindingDetail> {
  return apiGet<FindingDetail>(`/api/v1/findings/${encodeURIComponent(id)}`)
}

export function useFindings(filters: FindingFilters) {
  return useInfiniteQuery({
    queryKey: ['findings', filters],
    queryFn: ({ pageParam }) => fetchFindings(filters, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    placeholderData: keepPreviousData,
  })
}

export function useFinding(id: string) {
  return useQuery({ queryKey: ['finding', id], queryFn: () => fetchFinding(id) })
}
