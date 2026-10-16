import { act, fireEvent, screen } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ScanResult } from '../../api/scans.ts'
import { fakeFetch, jsonResponse } from '../../test/fakeFetch.ts'
import { findingRow } from '../../test/findingFixtures.ts'
import { findStatus } from '../../test/findStatus.ts'
import { renderRoute } from '../../test/renderRoute.tsx'

const FINDING = findingRow()

const NO_FALLBACKS = { llm_unavailable: 0, llm_misconfigured: 0, invalid_output: 0 }

function fallbacks(counts: Partial<typeof NO_FALLBACKS>): Record<string, number> {
  return { ...NO_FALLBACKS, ...counts }
}

const CLEAN: ScanResult = {
  scan_id: '3f2e1d0c-9b8a-4c7d-8e6f-5a4b3c2d1e0f',
  findings_detected: 5,
  findings_created: 2,
  rules_skipped: [],
  findings_explained: 2,
  explanations_fallback: 0,
  explanations_fallback_by_reason: NO_FALLBACKS,
  explanations_deferred: 0,
}

const RESULT: ScanResult = {
  ...CLEAN,
  rules_skipped: [{ rule_id: 'overtime_breach', reason: 'Policy is missing.' }],
}

function scanResult(overrides: Partial<ScanResult>): Response {
  return jsonResponse({ ...CLEAN, ...overrides })
}

function outcome(text: string | RegExp): HTMLElement {
  const callout = screen.getByText(text).closest<HTMLElement>('[role="status"]')
  if (callout === null) {
    throw new Error('The scan outcome is not in a status callout')
  }
  return callout
}

type Handler = () => Response | Promise<Response>

function serveApi(scan: Handler) {
  return fakeFetch((url, init) => {
    if (url.pathname === '/api/v1/scans' && init.method === 'POST') {
      return scan()
    }
    if (url.pathname === '/api/v1/findings') {
      return jsonResponse({ items: [FINDING], next_cursor: null })
    }
    if (url.pathname === `/api/v1/findings/${FINDING.id}/corrections`) {
      return jsonResponse([])
    }
    if (url.pathname === `/api/v1/findings/${FINDING.id}`) {
      return jsonResponse({ ...FINDING, evidence: {}, explanation: null })
    }
    return jsonResponse({ detail: `Unexpected path ${url.pathname}` }, 500)
  })
}

function deferred(): { promise: Promise<Response>; resolve: (response: Response) => void } {
  let resolve: (response: Response) => void = () => undefined
  const promise = new Promise<Response>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

function scanPosts(fetchMock: ReturnType<typeof fakeFetch>) {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
}

function findingsGets(fetchMock: ReturnType<typeof fakeFetch>) {
  return fetchMock.mock.calls.filter(
    ([path]) => new URL(String(path), window.location.origin).pathname === '/api/v1/findings',
  )
}

async function runScanButton(): Promise<HTMLElement> {
  await screen.findByRole('table', { name: 'Findings' })
  return screen.getByRole('button', { name: 'Run scan' })
}

describe('RunScanPanel', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
  })

  it('shows Scanning while the scan runs, then the counts and skipped rules', async () => {
    const scan = deferred()
    serveApi(() => scan.promise)
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    expect(await screen.findByRole('button', { name: 'Scanning…' })).toBeDisabled()
    expect(await findStatus('Scanning. This can take a few minutes.')).toBeInTheDocument()
    scan.resolve(jsonResponse(RESULT))
    const done = await screen.findByText('Scan finished: 2 new findings (5 detected).')
    expect(done.closest('[role="status"]')).not.toBeNull()
    expect(screen.getByText('Skipped Overtime breach: Policy is missing.')).toBeInTheDocument()
    expect(outcome(/Scan finished/).className).toContain('bg-caution-soft')
    expect(screen.getByRole('button', { name: 'Run scan' })).toBeEnabled()
  })

  it('shows the live clean scan payload with the info tone and no AI lines', async () => {
    serveApi(() =>
      jsonResponse({
        ...CLEAN,
        explanations_fallback: 0,
        explanations_deferred: 0,
        rules_skipped: [],
        explanations_fallback_by_reason: {
          llm_unavailable: 0,
          llm_misconfigured: 0,
          invalid_output: 0,
        },
      }),
    )
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    await screen.findByText('Scan finished: 2 new findings (5 detected).')
    expect(outcome(/Scan finished/).className).toContain('bg-accent-soft')
    expect(screen.queryByText(/Explanations/)).not.toBeInTheDocument()
  })

  it('uses the info tone for a clean scan', async () => {
    serveApi(() => jsonResponse(CLEAN))
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    await screen.findByText('Scan finished: 2 new findings (5 detected).')
    expect(outcome(/Scan finished/).className).toContain('bg-accent-soft')
  })

  it('says 1 new finding in the singular', async () => {
    serveApi(() => scanResult({ findings_created: 1, findings_detected: 1 }))
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    expect(await screen.findByText('Scan finished: 1 new finding (1 detected).')).toBeVisible()
  })

  it('says 0 new findings when nothing was found', async () => {
    serveApi(() => scanResult({ findings_created: 0, findings_detected: 0 }))
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    expect(await screen.findByText('Scan finished: 0 new findings (0 detected).')).toBeVisible()
  })

  it('warns when the AI could not write explanations', async () => {
    serveApi(() =>
      scanResult({
        explanations_fallback: 1,
        explanations_fallback_by_reason: fallbacks({ llm_unavailable: 1 }),
        explanations_deferred: 9,
      }),
    )
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    await screen.findByText(/Scan finished/)
    expect(
      screen.getByText('Explanations the AI could not write: 1 (AI model unavailable: 1).'),
    ).toBeInTheDocument()
    expect(screen.getByText('Explanations waiting for the next scan: 9.')).toBeInTheDocument()
    expect(outcome(/Scan finished/).className).toContain('bg-caution-soft')
  })

  it('uses the caution tone when only explanations were deferred', async () => {
    serveApi(() => scanResult({ explanations_deferred: 3 }))
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    await screen.findByText('Explanations waiting for the next scan: 3.')
    expect(outcome(/Scan finished/).className).toContain('bg-caution-soft')
  })

  it('uses the caution tone when only the AI model was misconfigured', async () => {
    serveApi(() =>
      scanResult({
        explanations_fallback: 1,
        explanations_fallback_by_reason: fallbacks({ llm_misconfigured: 1 }),
      }),
    )
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    await screen.findByText('Explanations the AI could not write: 1 (AI model misconfigured: 1).')
    expect(outcome(/Scan finished/).className).toContain('bg-caution-soft')
  })

  it('keeps the info tone when only the AI output was invalid', async () => {
    serveApi(() =>
      scanResult({
        explanations_fallback: 2,
        explanations_fallback_by_reason: fallbacks({ invalid_output: 2 }),
      }),
    )
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    expect(
      await screen.findByText('Explanations the AI could not write: 2 (invalid AI output: 2).'),
    ).toBeInTheDocument()
    expect(outcome(/Scan finished/).className).toContain('bg-accent-soft')
  })

  it('refetches the findings list after the scan', async () => {
    const fetchMock = serveApi(() => jsonResponse(RESULT))
    renderRoute('/findings')
    const button = await runScanButton()
    expect(findingsGets(fetchMock)).toHaveLength(1)

    fireEvent.click(button)

    await screen.findByText(/Scan finished/)
    await vi.waitFor(() => expect(findingsGets(fetchMock)).toHaveLength(2))
  })

  it('shows the API detail when the scan fails', async () => {
    serveApi(() => jsonResponse({ detail: 'Database is unavailable.' }, 500))
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is unavailable.')
    expect(screen.getByRole('button', { name: 'Run scan' })).toBeEnabled()
  })

  it('says the scan may still run when the connection drops', async () => {
    serveApi(() => Promise.reject(new TypeError('Failed to fetch')))
    renderRoute('/findings')

    fireEvent.click(await runScanButton())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Lost the connection. The scan may still be running on the server. Check the list again in a few minutes.',
    )
  })

  it('refetches the findings list after a failed scan', async () => {
    const fetchMock = serveApi(() => new Response(null, { status: 504 }))
    renderRoute('/findings')
    const button = await runScanButton()

    fireEvent.click(button)

    await screen.findByRole('alert')
    await vi.waitFor(() => expect(findingsGets(fetchMock)).toHaveLength(2))
  })

  it('shows only the failure when a later scan fails', async () => {
    let scans = 0
    serveApi(() => {
      scans += 1
      return scans === 1
        ? jsonResponse(CLEAN)
        : jsonResponse({ detail: 'Database is unavailable.' }, 500)
    })
    renderRoute('/findings')
    fireEvent.click(await runScanButton())
    await screen.findByText(/Scan finished/)

    fireEvent.click(screen.getByRole('button', { name: 'Run scan' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is unavailable.')
    expect(screen.queryByText(/Scan finished/)).not.toBeInTheDocument()
  })

  it('keeps Scanning after leaving the page and coming back', async () => {
    const scan = deferred()
    const fetchMock = serveApi(() => scan.promise)
    const { router } = renderRoute('/findings')
    fireEvent.click(await runScanButton())
    await screen.findByRole('button', { name: 'Scanning…' })

    await act(() => router.navigate(`/findings/${FINDING.id}`))
    await screen.findByRole('link', { name: 'Back to findings' })
    await act(() => router.navigate('/findings'))

    expect(await screen.findByRole('button', { name: 'Scanning…' })).toBeDisabled()
    scan.resolve(jsonResponse(RESULT))
    expect(await screen.findByText('Scan finished: 2 new findings (5 detected).')).toBeVisible()
    expect(scanPosts(fetchMock)).toHaveLength(1)
  })

  it('sends one scan when Run scan is clicked twice at once', async () => {
    const fetchMock = serveApi(() => jsonResponse(RESULT))
    renderRoute('/findings')
    const button = await runScanButton()

    act(() => {
      button.click()
      button.click()
    })

    await screen.findByText(/Scan finished/)
    expect(scanPosts(fetchMock)).toHaveLength(1)
  })

  it('says the scan waits for the connection when offline', async () => {
    const fetchMock = serveApi(() => jsonResponse(RESULT))
    renderRoute('/findings')
    const button = await runScanButton()
    act(() => onlineManager.setOnline(false))

    fireEvent.click(button)

    expect(await screen.findByText(/You are offline. The scan will start/)).toBeInTheDocument()
    expect(screen.queryByText(/Scanning\./)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Waiting for connection…' })).toBeDisabled()
    expect(scanPosts(fetchMock)).toHaveLength(0)
  })
})
