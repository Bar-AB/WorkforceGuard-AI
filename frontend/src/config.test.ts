import { describe, expect, it, vi } from 'vitest'
import { readCompanyId, readUserId } from './config.ts'

describe('readCompanyId', () => {
  it('returns the id for a valid UUID', () => {
    vi.stubEnv('VITE_COMPANY_ID', '3f2b9c1e-8a4d-4e6f-9b2a-1c3d5e7f9a0b')

    expect(readCompanyId()).toBe('3f2b9c1e-8a4d-4e6f-9b2a-1c3d5e7f9a0b')
  })

  it('returns null when empty', () => {
    vi.stubEnv('VITE_COMPANY_ID', '')

    expect(readCompanyId()).toBeNull()
  })

  it('returns null when missing', () => {
    vi.stubEnv('VITE_COMPANY_ID', undefined)

    expect(readCompanyId()).toBeNull()
  })

  it('returns null when not UUID-shaped', () => {
    vi.stubEnv('VITE_COMPANY_ID', 'acme-corp')

    expect(readCompanyId()).toBeNull()
  })
})

describe('readUserId', () => {
  it('returns the id for a valid UUID', () => {
    vi.stubEnv('VITE_USER_ID', '9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a')

    expect(readUserId()).toBe('9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a')
  })

  it.each([undefined, '', 'admin'])('returns null for missing or non-UUID value %j', (value) => {
    vi.stubEnv('VITE_USER_ID', value)

    expect(readUserId()).toBeNull()
  })
})
