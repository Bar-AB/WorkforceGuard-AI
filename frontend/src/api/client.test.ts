import { describe, expect, it, vi } from 'vitest'
import { fakeFetch, jsonResponse } from '../test/fakeFetch.ts'
import { ApiError, apiGet, apiPost, isUnknownCompany } from './client.ts'

const COMPANY_ID = '3f2b9c1e-8a4d-4e6f-9b2a-1c3d5e7f9a0b'
const USER_ID = '9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a'

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

describe('apiPost', () => {
  it('sends company and user headers', async () => {
    vi.stubEnv('VITE_COMPANY_ID', COMPANY_ID)
    vi.stubEnv('VITE_USER_ID', USER_ID)
    const fetchMock = fakeFetch(() => jsonResponse({ ok: true }))

    await expect(apiPost('/api/v1/corrections/c1/approve', { asUser: true })).resolves.toEqual({
      ok: true,
    })

    const init = fetchMock.mock.calls[0][1]
    const headers = new Headers(init?.headers)
    expect(init?.method).toBe('POST')
    expect(headers.get('X-Company-Id')).toBe(COMPANY_ID)
    expect(headers.get('X-User-Id')).toBe(USER_ID)
  })

  it('omits the user header unless asked', async () => {
    vi.stubEnv('VITE_USER_ID', USER_ID)
    const fetchMock = fakeFetch(() => jsonResponse({}))

    await apiPost('/api/v1/findings/f1/corrections')

    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has('X-User-Id')).toBe(false)
  })

  it('throws when user id is required but missing', async () => {
    vi.stubEnv('VITE_USER_ID', '')
    const fetchMock = fakeFetch(() => jsonResponse({}))

    await expect(apiPost('/api/v1/corrections/c1/approve', { asUser: true })).rejects.toThrow(
      'VITE_USER_ID is missing or not a UUID. Set it in frontend/.env.local.',
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('maps error detail', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Correction is not pending.' }, 409))

    const error = await rejectionOf(apiPost('/api/v1/corrections/c1/approve', { asUser: true }))

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, message: 'Correction is not pending.' })
  })
})

describe('apiPost transport failures', () => {
  it('says the outcome is unknown when the connection is lost', async () => {
    const networkError = new TypeError('Failed to fetch')
    fakeFetch(() => Promise.reject(networkError))

    const error = await rejectionOf(apiPost('/api/v1/findings/f1/corrections'))

    expect(error).toBeInstanceOf(Error)
    expect(error).toMatchObject({
      message: 'Lost the connection. Reload to see whether it was saved.',
      cause: networkError,
    })
  })
})
