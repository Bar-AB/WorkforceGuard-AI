import { describe, expect, it, vi } from 'vitest'
import { fakeFetch, jsonResponse } from '../test/fakeFetch.ts'
import { ApiError, apiGet, isUnknownCompany } from './client.ts'

const COMPANY_ID = '3f2b9c1e-8a4d-4e6f-9b2a-1c3d5e7f9a0b'

async function rejectionOf(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise
  } catch (error) {
    return error
  }
  throw new Error('Expected the promise to reject')
}

describe('apiGet', () => {
  it('sends X-Company-Id from config', async () => {
    vi.stubEnv('VITE_COMPANY_ID', COMPANY_ID)
    const fetchMock = fakeFetch(() => jsonResponse({}))

    await apiGet('/api/v1/findings')

    const headers = new Headers(fetchMock.mock.calls[0][1]?.headers)
    expect(headers.get('X-Company-Id')).toBe(COMPANY_ID)
  })

  it('appends query params', async () => {
    const fetchMock = fakeFetch(() => jsonResponse({}))

    await apiGet('/api/v1/findings', new URLSearchParams({ severity: 'high', limit: '50' }))

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/v1/findings?severity=high&limit=50')
  })

  it('returns parsed JSON on 200', async () => {
    fakeFetch(() => jsonResponse({ items: [], next_cursor: null }))

    await expect(apiGet('/api/v1/findings')).resolves.toEqual({ items: [], next_cursor: null })
  })

  it('throws ApiError with detail string on 404', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Finding not found.' }, 404))

    const error = await rejectionOf(apiGet('/api/v1/findings/missing'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, message: 'Finding not found.' })
  })

  it('throws ApiError with generic message when detail is a list (422)', async () => {
    fakeFetch(() =>
      jsonResponse({ detail: [{ loc: ['query', 'severity'], msg: 'Input should be' }] }, 422),
    )

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 422, message: 'Request failed (422)' })
  })

  it('tells the user to check the backend when a 5xx error body is not JSON', async () => {
    fakeFetch(() => new Response('<html>Bad Gateway</html>', { status: 502 }))

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 502,
      message: 'The API is unavailable (502). Check that the backend is running.',
    })
  })

  it('tells the user to check the backend on a 5xx without a string detail', async () => {
    fakeFetch(() => jsonResponse({ error: 'boom' }, 500))

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toMatchObject({
      status: 500,
      message: 'The API is unavailable (500). Check that the backend is running.',
    })
  })

  it.each(['', '   '])('treats a blank detail %j as missing', async (detail) => {
    fakeFetch(() => jsonResponse({ detail }, 404))

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toMatchObject({ status: 404, message: 'Request failed (404)' })
  })

  it('throws ApiError for unknown company 404 with the API detail', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Unknown company.' }, 404))

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, message: 'Unknown company.' })
  })

  it('throws ApiError when a 200 body is not JSON', async () => {
    fakeFetch(
      () =>
        new Response('<!doctype html><html></html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' },
        }),
    )

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 200, message: 'Unexpected response from the API.' })
  })

  it('throws ApiError when a 200 JSON body cannot be parsed', async () => {
    fakeFetch(
      () =>
        new Response('{not json', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    )

    const error = await rejectionOf(apiGet('/api/v1/findings'))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 200, message: 'Unexpected response from the API.' })
    expect((error as ApiError).cause).toBeInstanceOf(SyntaxError)
  })

  it('refuses to call the API without a company id', async () => {
    vi.stubEnv('VITE_COMPANY_ID', '')
    const fetchMock = fakeFetch(() => jsonResponse({}))

    await expect(apiGet('/api/v1/findings')).rejects.toThrow(
      'VITE_COMPANY_ID is missing or not a UUID. Set it in frontend/.env.local.',
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('lets a network failure propagate unchanged', async () => {
    const networkError = new TypeError('Failed to fetch')
    fakeFetch(() => Promise.reject(networkError))

    await expect(apiGet('/api/v1/findings')).rejects.toBe(networkError)
  })
})

describe('isUnknownCompany', () => {
  it('matches only the unknown company 404', () => {
    expect(isUnknownCompany(new ApiError(404, 'Unknown company.'))).toBe(true)
    expect(isUnknownCompany(new ApiError(404, 'Finding not found.'))).toBe(false)
    expect(isUnknownCompany(new Error('Unknown company.'))).toBe(false)
  })
})
