import { afterEach, describe, expect, it, vi } from 'vitest'
import { correction } from '../test/correctionFixtures.ts'
import { fakeFetch, jsonResponse } from '../test/fakeFetch.ts'
import { decideCorrection, fetchCorrections, suggestCorrection } from './corrections.ts'

const FINDING_ID = '7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f'
const CORRECTION_ID = '5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d'
const SUGGEST_LOST =
  'Lost the connection. The fix may still be in progress. Open the finding again in a few minutes.'
const SUGGEST_TIMEOUT_MS = 12 * 60 * 1000

async function rejectionOf(promise: Promise<unknown>): Promise<Error> {
  try {
    await promise
  } catch (error) {
    return error as Error
  }
  throw new Error('Expected a rejection')
}

describe('corrections api', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetchCorrections gets the finding corrections', async () => {
    const fetchMock = fakeFetch(() => jsonResponse([correction()]))

    await expect(fetchCorrections(FINDING_ID)).resolves.toEqual([correction()])

    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/v1/findings/${FINDING_ID}/corrections`)
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined()
  })

  it('suggestCorrection posts without a user id', async () => {
    const fetchMock = fakeFetch(() => jsonResponse({ proposal: null, reason: 'Real overwork.' }))

    await expect(suggestCorrection(FINDING_ID)).resolves.toEqual({
      proposal: null,
      reason: 'Real overwork.',
    })

    const [path, init] = fetchMock.mock.calls[0]
    expect(String(path)).toBe(`/api/v1/findings/${FINDING_ID}/corrections`)
    expect(init?.method).toBe('POST')
    expect(new Headers(init?.headers).has('X-User-Id')).toBe(false)
  })

  it.each(['approve', 'reject'] as const)(
    'decideCorrection posts %s with the user id',
    async (verdict) => {
      const fetchMock = fakeFetch(() => jsonResponse(correction({ status: 'approved' })))

      await decideCorrection(CORRECTION_ID, verdict)

      const [path, init] = fetchMock.mock.calls[0]
      expect(String(path)).toBe(`/api/v1/corrections/${CORRECTION_ID}/${verdict}`)
      expect(init?.method).toBe('POST')
      expect(new Headers(init?.headers).get('X-User-Id')).toBe(
        '00000000-0000-4000-8000-0000000000aa',
      )
    },
  )

  it.each([
    ['a dropped connection', () => Promise.reject(new TypeError('Failed to fetch'))],
    ['a 504 without a body', () => Promise.resolve(new Response(null, { status: 504 }))],
    [
      'a 502 with a proxy detail',
      () => Promise.resolve(jsonResponse({ detail: 'Bad gateway.' }, 502)),
    ],
    ['a 500 without a detail', () => Promise.resolve(new Response('', { status: 500 }))],
  ])('says the fix may still be in progress after %s', async (_name, response) => {
    fakeFetch(() => response())

    const error = await rejectionOf(suggestCorrection(FINDING_ID))

    expect(error.message).toBe(SUGGEST_LOST)
  })

  it('keeps a real API detail', async () => {
    fakeFetch(() => jsonResponse({ detail: 'The AI model is not configured correctly.' }, 503))

    const error = await rejectionOf(suggestCorrection(FINDING_ID))

    expect(error.message).toBe('The AI model is not configured correctly.')
  })

  it('gives up waiting after 12 minutes with the fix message', async () => {
    const timeout = new AbortController()
    const timeoutSpy = vi.spyOn(AbortSignal, 'timeout').mockReturnValue(timeout.signal)
    fakeFetch(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(init.signal?.reason))
        }),
    )
    const suggest = rejectionOf(suggestCorrection(FINDING_ID))

    timeout.abort(new DOMException('The operation timed out.', 'TimeoutError'))

    expect((await suggest).message).toBe(SUGGEST_LOST)
    expect(timeoutSpy).toHaveBeenCalledWith(SUGGEST_TIMEOUT_MS)
  })
})
