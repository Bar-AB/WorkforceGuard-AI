import { vi, type Mock } from 'vitest'

export function fakeFetch(handler: (url: URL) => Response | Promise<Response>): Mock<typeof fetch> {
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const href = input instanceof Request ? input.url : String(input)
    return Promise.resolve(handler(new URL(href, window.location.origin)))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
