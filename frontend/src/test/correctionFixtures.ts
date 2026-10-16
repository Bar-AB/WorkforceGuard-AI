import type { Correction } from '../api/corrections.ts'
import { findingDetail } from './findingFixtures.ts'

export function correction(overrides: Partial<Correction> = {}): Correction {
  return {
    id: '5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d',
    finding_id: findingDetail().id,
    target_table: 'shifts',
    target_id: 'e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b',
    patch: { ends_at: '2026-03-02T17:00:00Z' },
    current: { ends_at: '2026-03-02T23:00:00Z' },
    before: { ends_at: '2026-03-02T23:00:00Z' },
    reason: 'The clock-out time looks like a typo.',
    status: 'pending',
    proposed_by: 'llm:qwen3:4b:propose_correction.v1',
    created_at: '2026-03-04T09:30:00Z',
    decided_by_user_id: null,
    decided_at: null,
    judge_supported: null,
    judge_reason: null,
    judged_by: null,
    ...overrides,
  }
}
