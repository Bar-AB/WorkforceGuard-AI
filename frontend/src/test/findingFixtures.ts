import type { FindingDetail, FindingSummary } from '../api/findings.ts'

export function findingSummary(overrides: Partial<FindingSummary> = {}): FindingSummary {
  return {
    id: '7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f',
    employee_id: 'b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e',
    rule_id: 'overtime_breach',
    rule_version: '1',
    severity: 'high',
    status: 'open',
    summary: 'Worked 14h on 2026-03-02',
    occurred_on: '2026-03-02',
    detected_at: '2026-03-03T08:00:00Z',
    ...overrides,
  }
}

export function findingDetail(overrides: Partial<FindingDetail> = {}): FindingDetail {
  return {
    ...findingSummary(),
    evidence: { worked_hours: 14, shift_date: '2026-03-02' },
    explanation: null,
    explanation_source: null,
    explanation_prompt_version: null,
    ...overrides,
  }
}
