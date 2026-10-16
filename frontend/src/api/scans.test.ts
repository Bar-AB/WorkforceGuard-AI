import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeFetch, jsonResponse } from '../test/fakeFetch.ts'
import { runScan, type ScanResult } from './scans.ts'

const RESULT: ScanResult = {
  scan_id: '3f2e1d0c-9b8a-4c7d-8e6f-5a4b3c2d1e0f',
  findings_detected: 5,
  findings_created: 2,
  rules_skipped: [],
  findings_explained: 2,
  explanations_fallback: 0,
  explanations_fallback_by_reason: { llm_unavailable: 0, llm_misconfigured: 0, invalid_output: 0 },
  explanations_deferred: 0,
}

const SCAN_LOST =
  'Lost the connection. The scan may still be running on the server. Check the list again in a few minutes.'
const SCAN_TIMEOUT_MS = 45 * 60 * 1000

function abortableFetch() {
  return fakeFetch(
    (_url, init) =>
      new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(init.signal?.reason))
      }),
  )
}

async function rejectionOf(promise: Promise<unknown>): Promise<Error> {
  try {
    await promise
  } catch (error) {
    return error as Error
  }
  throw new Error('Expected a rejection')
}

describe('scans api', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('runScan posts to the scans route with the company and without a user id', async () => {
    const fetchMock = fakeFetch(() => jsonResponse(RESULT))

    await expect(runScan()).resolves.toEqual(RESULT)

    const [path, init] = fetchMock.mock.calls[0]
    expect(String(path)).toBe('/api/v1/scans')
    expect(init?.method).toBe('POST')
    const headers = new Headers(init?.headers)
    expect(headers.get('X-Company-Id')).toBe('00000000-0000-4000-8000-000000000001')
    expect(headers.has('X-User-Id')).toBe(false)
  })

  it('says the scan may still run when the connection drops', async () => {
    fakeFetch(() => Promise.reject(new TypeError('Failed to fetch')))

    const error = await rejectionOf(runScan())

    expect(error.message).toBe(SCAN_LOST)
  })

  it.each([
    ['a 504 without a body', () => new Response(null, { status: 504 })],
    ['a 502 without a body', () => new Response(null, { status: 502 })],
    ['a 504 with a proxy detail', () => jsonResponse({ detail: 'Gateway timeout.' }, 504)],
    ['a proxy 500 without a body', () => new Response('', { status: 500 })],
  ])('says the scan may still run after %s', async (_name, response) => {
    fakeFetch(() => response())

    const error = await rejectionOf(runScan())

    expect(error.message).toBe(SCAN_LOST)
  })

  it('keeps a real API detail', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Unknown company.' }, 404))

    const error = await rejectionOf(runScan())

    expect(error.message).toBe('Unknown company.')
  })

  it('keeps a server error detail', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Database is unavailable.' }, 500))

    const error = await rejectionOf(runScan())

    expect(error.message).toBe('Database is unavailable.')
  })

  it('gives up waiting after 45 minutes with the scan message', async () => {
    const timeout = new AbortController()
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeout.signal)
    abortableFetch()
    const scan = rejectionOf(runScan())

    timeout.abort(new DOMException('The operation timed out.', 'TimeoutError'))

    expect((await scan).message).toBe(SCAN_LOST)
    expect(timeoutSpy).toHaveBeenCalledWith(SCAN_TIMEOUT_MS)
  })
})
