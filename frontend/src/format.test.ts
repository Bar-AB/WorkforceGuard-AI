import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime, shortId } from './format.ts'

describe('formatDate', () => {
  it('shows an ISO date as a short month, day and year', () => {
    expect(formatDate('2026-02-09')).toBe('Feb 9, 2026')
  })

  it('keeps the calendar day at the edges of the day', () => {
    expect(formatDate('2026-12-31')).toBe('Dec 31, 2026')
  })

  it('returns text that is not a date unchanged', () => {
    expect(formatDate('not-a-date')).toBe('not-a-date')
  })
})

describe('formatDateTime', () => {
  it('shows a timestamp in UTC with a 24-hour clock', () => {
    expect(formatDateTime('2026-03-03T18:05:00Z')).toBe('Mar 3, 2026, 18:05 UTC')
  })

  it('converts an offset timestamp to UTC', () => {
    expect(formatDateTime('2026-03-03T01:30:00+02:00')).toBe('Mar 2, 2026, 23:30 UTC')
  })

  it('returns text that is not a timestamp unchanged', () => {
    expect(formatDateTime('')).toBe('')
  })
})

describe('shortId', () => {
  it('keeps the first 8 and last 5 characters of a long id', () => {
    expect(shortId('fa02eaec-1b2c-4d3e-8f4a-5b6c7d88adfe')).toBe('fa02eaec…8adfe')
  })

  it('keeps a short id whole', () => {
    expect(shortId('emp-42')).toBe('emp-42')
  })
})
