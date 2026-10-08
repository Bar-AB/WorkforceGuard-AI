import { describe, expect, it } from 'vitest'
import { fakeFetch, jsonResponse } from '../test/fakeFetch.ts'
import { fetchFinding, fetchFindings, ruleLabel } from './findings.ts'

const FINDING_ID = '7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f'

describe('fetchFindings', () => {
  it('sends limit and only set filters as snake_case params', async () => {
    const fetchMock = fakeFetch(() => jsonResponse({ items: [], next_cursor: null }))

    await fetchFindings(
      {
        ruleId: 'overtime_breach',
        severity: 'high',
        occurredFrom: '2026-03-01',
        occurredTo: '2026-03-31',
      },
      'cursor-1',
    )

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/v1/findings?limit=50&rule_id=overtime_breach&severity=high&occurred_from=2026-03-01&occurred_to=2026-03-31&cursor=cursor-1',
    )
  })

  it('omits unset filters and the cursor on the first page', async () => {
    const fetchMock = fakeFetch(() => jsonResponse({ items: [], next_cursor: null }))

    await fetchFindings({ severity: 'low' }, null)

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/v1/findings?limit=50&severity=low')
  })
})

describe('fetchFinding', () => {
  it('requests /api/v1/findings/<id>', async () => {
    const fetchMock = fakeFetch(() => jsonResponse({ id: FINDING_ID }))

    await fetchFinding(FINDING_ID)

    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/v1/findings/${FINDING_ID}`)
  })
})

describe('ruleLabel', () => {
  it('returns label or raw id', () => {
    expect(ruleLabel('overtime_breach')).toBe('Overtime breach')
    expect(ruleLabel('future_rule')).toBe('future_rule')
  })
})
