import { describe, expect, it } from 'vitest'
import { ApiError } from './client.ts'
import { createQueryClient, shouldRetry } from './queryClient.ts'

describe('shouldRetry', () => {
  it('does not retry a 404 ApiError', () => {
    expect(shouldRetry(0, new ApiError(404, 'Finding not found.'))).toBe(false)
  })

  it('does not retry an unknown company 404', () => {
    expect(shouldRetry(0, new ApiError(404, 'Unknown company.'))).toBe(false)
  })

  it('does not retry an unexpected 200 response', () => {
    expect(shouldRetry(0, new ApiError(200, 'Unexpected response from the API.'))).toBe(false)
  })

  it('does not retry a 422 ApiError', () => {
    expect(shouldRetry(0, new ApiError(422, 'Unknown rule_id.'))).toBe(false)
  })

  it('retries a 500 ApiError while failureCount is below 2', () => {
    expect(
      shouldRetry(
        0,
        new ApiError(500, 'The API is unavailable (500). Check that the backend is running.'),
      ),
    ).toBe(true)
    expect(
      shouldRetry(
        1,
        new ApiError(500, 'The API is unavailable (500). Check that the backend is running.'),
      ),
    ).toBe(true)
  })

  it('retries a plain Error while failureCount is below 2', () => {
    expect(shouldRetry(1, new Error('Failed to fetch'))).toBe(true)
  })

  it('stops retrying at 2 failures', () => {
    expect(
      shouldRetry(
        2,
        new ApiError(500, 'The API is unavailable (500). Check that the backend is running.'),
      ),
    ).toBe(false)
    expect(shouldRetry(2, new Error('Failed to fetch'))).toBe(false)
  })
})

describe('createQueryClient', () => {
  it('uses shouldRetry as the default query retry', () => {
    expect(createQueryClient().getDefaultOptions().queries?.retry).toBe(shouldRetry)
  })
})
