import { describe, expect, it } from 'vitest'
import { filtersToSearchParams } from '../../api/findings.ts'
import { filtersFromSearchParams, isInvertedRange, toIsoDate } from './filterParams.ts'

describe('filter params', () => {
  it('round-trips every filter through the URL', () => {
    const filters = {
      ruleId: 'overtime_breach',
      severity: 'medium' as const,
      occurredFrom: '2026-03-01',
      occurredTo: '2026-03-31',
    }

    const params = filtersToSearchParams(filters)

    expect(params.toString()).toBe(
      'rule_id=overtime_breach&severity=medium&occurred_from=2026-03-01&occurred_to=2026-03-31',
    )
    expect(filtersFromSearchParams(params)).toEqual(filters)
  })

  it('drops unknown values', () => {
    const params = new URLSearchParams(
      'rule_id=made_up&severity=critical&occurred_from=2026-3-1&occurred_to=yesterday',
    )

    expect(filtersFromSearchParams(params)).toEqual({})
  })

  it('maps empty filters to empty params', () => {
    expect(filtersToSearchParams({}).toString()).toBe('')
    expect(filtersFromSearchParams(new URLSearchParams())).toEqual({})
  })

  it.each(['2026-02-30', '2026-13-01', '2026-04-31', '2025-02-29', '0000-01-01'])(
    'drops the non-calendar date %s',
    (date) => {
      expect(toIsoDate(date)).toBeUndefined()
    },
  )

  it.each(['2024-02-29', '2026-12-31', '0001-01-01'])('keeps the calendar date %s', (date) => {
    expect(toIsoDate(date)).toBe(date)
  })

  it('keeps an inverted date range so the inputs show what was typed', () => {
    const params = new URLSearchParams(
      'severity=high&occurred_from=2026-03-31&occurred_to=2026-03-01',
    )

    expect(filtersFromSearchParams(params)).toEqual({
      severity: 'high',
      occurredFrom: '2026-03-31',
      occurredTo: '2026-03-01',
    })
  })

  it.each([
    [{ occurredFrom: '2026-03-31', occurredTo: '2026-03-01' }, true],
    [{ occurredFrom: '2026-03-01', occurredTo: '2026-03-01' }, false],
    [{ occurredFrom: '2026-03-01', occurredTo: '2026-03-31' }, false],
    [{ occurredFrom: '2026-03-31' }, false],
    [{ occurredTo: '0002-10-08' }, false],
    [{}, false],
  ])('isInvertedRange(%o) is %s', (filters, inverted) => {
    expect(isInvertedRange(filters)).toBe(inverted)
  })

  it('keeps a single-day range', () => {
    const params = new URLSearchParams('occurred_from=2026-03-01&occurred_to=2026-03-01')

    expect(filtersFromSearchParams(params)).toEqual({
      occurredFrom: '2026-03-01',
      occurredTo: '2026-03-01',
    })
  })
})
