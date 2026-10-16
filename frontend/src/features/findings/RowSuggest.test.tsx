import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'
import { afterEach, describe, expect, it } from 'vitest'
import type { FindingRow } from '../../api/findings.ts'
import { correction } from '../../test/correctionFixtures.ts'
import { fakeFetch, jsonResponse } from '../../test/fakeFetch.ts'
import { findingRow } from '../../test/findingFixtures.ts'
import { renderRoute } from '../../test/renderRoute.tsx'

const FINDING = findingRow()
const OTHER = findingRow({
  id: '22222222-2222-4222-8222-222222222222',
  summary: 'Worked 13h on 2026-03-05',
})
const PROPOSAL = correction()
const PENDING_MESSAGE = 'A fix for this finding is already waiting for a decision.'
const ANOTHER_BUSY = 'Another fix is being suggested.'
const USER_ID = '00000000-0000-4000-8000-0000000000aa'

type Suggest = (findingId: string) => Response | Promise<Response>

interface FakeServer {
  fetchMock: ReturnType<typeof fakeFetch>
  pending: Set<string>
  statuses: Map<string, string>
}

function created(): Response {
  return jsonResponse({ proposal: PROPOSAL, reason: null }, 201)
}

function serveApi({
  rows = [FINDING],
  suggest = created,
}: { rows?: FindingRow[]; suggest?: Suggest } = {}): FakeServer {
  const pending = new Set(rows.filter((row) => row.has_pending_correction).map((row) => row.id))
  const statuses = new Map(rows.map((row) => [row.id, row.status]))
  const current = (row: FindingRow): FindingRow => ({
    ...row,
    status: statuses.get(row.id) ?? row.status,
    has_pending_correction: pending.has(row.id),
  })
  const fetchMock = fakeFetch(async (url, init) => {
    if (url.pathname === '/api/v1/findings') {
      return jsonResponse({ items: rows.map(current), next_cursor: null })
    }
    const decision = /^\/api\/v1\/corrections\/[^/]+\/(approve|reject)$/.exec(url.pathname)
    if (decision !== null) {
      pending.clear()
      return jsonResponse(
        correction({
          status: 'rejected',
          decided_by_user_id: USER_ID,
          decided_at: '2026-03-04T10:00:00Z',
        }),
      )
    }
    const finding = /^\/api\/v1\/findings\/([^/]+)(\/corrections)?$/.exec(url.pathname)
    const row = rows.find((candidate) => candidate.id === finding?.[1])
    if (finding === null || row === undefined) {
      return jsonResponse({ detail: `Unexpected path ${url.pathname}` }, 500)
    }
    if (finding[2] === undefined) {
      return jsonResponse({ ...current(row), evidence: {}, explanation: null })
    }
    if (init.method !== 'POST') {
      return jsonResponse(pending.has(row.id) ? [correction({ finding_id: row.id })] : [])
    }
    const response = await suggest(row.id)
    if (response.status === 201) {
      pending.add(row.id)
    }
    return response
  })
  return { fetchMock, pending, statuses }
}

function deferred(): { promise: Promise<Response>; resolve: (response: Response) => void } {
  let resolve: (response: Response) => void = () => undefined
  const promise = new Promise<Response>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

function posts(fetchMock: ReturnType<typeof fakeFetch>) {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
}

function listGets(fetchMock: ReturnType<typeof fakeFetch>) {
  return fetchMock.mock.calls.filter(
    ([path]) => new URL(String(path), window.location.origin).pathname === '/api/v1/findings',
  )
}

async function row(finding: FindingRow = FINDING): Promise<HTMLElement> {
  return screen.findByRole('row', { name: new RegExp(finding.summary) })
}

async function suggestButton(finding: FindingRow = FINDING): Promise<HTMLElement> {
  return within(await row(finding)).findByRole('button', { name: 'Suggest fix' })
}

async function rowStatus(finding: FindingRow = FINDING): Promise<HTMLElement> {
  return within(await row(finding)).getByRole('status')
}

async function rejectOnDetailPage(router: ReturnType<typeof renderRoute>['router']) {
  await act(() => router.navigate(`/findings/${FINDING.id}`))
  const panel = await screen.findByRole('region', { name: 'Suggested fixes' })
  fireEvent.click(await within(panel).findByRole('button', { name: 'Reject' }))
  await within(panel).findByText('No fixes suggested yet.')
  await act(() => router.navigate('/findings'))
}

describe('RowSuggest', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
  })

  it('offers no suggest on a dismissed row', async () => {
    serveApi({ rows: [findingRow({ status: 'dismissed' })] })

    renderRoute('/findings')

    const dismissed = await row()
    expect(within(dismissed).queryByRole('button', { name: 'Suggest fix' })).toBeNull()
  })

  it('posts to the finding corrections path, keeps the list open and reloads it', async () => {
    const { fetchMock } = serveApi()
    const { router } = renderRoute('/findings')

    fireEvent.click(await suggestButton())

    const status = await rowStatus()
    expect(await within(status).findByText('Fix suggested')).toBeInTheDocument()
    expect(String(posts(fetchMock)[0][0])).toBe(`/api/v1/findings/${FINDING.id}/corrections`)
    expect(router.state.location.pathname).toBe('/findings')
    expect(within(await row()).queryByRole('button', { name: 'Suggest fix' })).toBeNull()
    expect(listGets(fetchMock)).toHaveLength(2)
  })

  it('shows the badge from the server without a button or a request', async () => {
    const { fetchMock } = serveApi({ rows: [findingRow({ has_pending_correction: true })] })

    renderRoute('/findings')

    expect(await within(await rowStatus()).findByText('Fix suggested')).toBeInTheDocument()
    expect(within(await row()).queryByRole('button', { name: 'Suggest fix' })).toBeNull()
    expect(posts(fetchMock)).toHaveLength(0)
  })

  it('offers suggest again after the fix is decided on the detail page', async () => {
    serveApi({ rows: [findingRow({ has_pending_correction: true })] })
    const { router } = renderRoute('/findings')
    await within(await rowStatus()).findByText('Fix suggested')

    await rejectOnDetailPage(router)

    expect(await suggestButton()).toBeEnabled()
    expect(within(await rowStatus()).queryByText('Fix suggested')).toBeNull()
  })

  it('clears the old suggest error after the fix is decided', async () => {
    const server = serveApi({
      suggest: (findingId) => {
        server.pending.add(findingId)
        return jsonResponse({ detail: PENDING_MESSAGE }, 409)
      },
    })
    const { router } = renderRoute('/findings')
    fireEvent.click(await suggestButton())
    expect(await within(await row()).findByRole('alert')).toHaveTextContent(PENDING_MESSAGE)

    await rejectOnDetailPage(router)

    expect(await suggestButton()).toBeEnabled()
    expect(within(await row()).queryByRole('alert')).toBeNull()
  })

  it('disables the button and says it is asking the AI while pending', async () => {
    const suggestion = deferred()
    serveApi({ suggest: () => suggestion.promise })
    renderRoute('/findings')

    fireEvent.click(await suggestButton())

    expect(await within(await rowStatus()).findByText('Asking the AI…')).toBeInTheDocument()
    expect(await suggestButton()).toBeDisabled()
    suggestion.resolve(created())
    expect(await within(await rowStatus()).findByText('Fix suggested')).toBeInTheDocument()
  })

  it('shows the reason when no fix is suggested', async () => {
    serveApi({
      suggest: () =>
        jsonResponse({ proposal: null, reason: 'The hours look real. No data error to fix.' }),
    })
    renderRoute('/findings')

    fireEvent.click(await suggestButton())

    expect(
      await within(await rowStatus()).findByText(
        'No fix: The hours look real. No data error to fix.',
      ),
    ).toBeInTheDocument()
    expect(await suggestButton()).toBeEnabled()
  })

  it('shows the pending-proposal message on 409', async () => {
    serveApi({ suggest: () => jsonResponse({ detail: PENDING_MESSAGE }, 409) })
    renderRoute('/findings')

    fireEvent.click(await suggestButton())

    expect(await within(await row()).findByRole('alert')).toHaveTextContent(PENDING_MESSAGE)
    expect(await suggestButton()).toBeEnabled()
  })

  it('shows the API detail when the suggest fails', async () => {
    serveApi({
      suggest: () => jsonResponse({ detail: 'The AI model is not configured correctly.' }, 503),
    })
    renderRoute('/findings')

    fireEvent.click(await suggestButton())

    expect(await within(await row()).findByRole('alert')).toHaveTextContent(
      'The AI model is not configured correctly.',
    )
  })

  it('says it will retry when offline', async () => {
    serveApi()
    renderRoute('/findings')
    const button = await suggestButton()
    act(() => onlineManager.setOnline(false))

    fireEvent.click(button)

    expect(await within(await rowStatus()).findByText('Offline. Will retry.')).toBeInTheDocument()
    expect(await suggestButton()).toBeDisabled()
  })

  it('shares the busy state with the finding detail page', async () => {
    const suggestion = deferred()
    const { fetchMock } = serveApi({ suggest: () => suggestion.promise })
    const { router } = renderRoute('/findings')
    fireEvent.click(await suggestButton())
    await within(await rowStatus()).findByText('Asking the AI…')

    await act(() => router.navigate(`/findings/${FINDING.id}`))

    const panel = await screen.findByRole('region', { name: 'Suggested fixes' })
    expect(
      await within(panel).findByText('Asking the AI for a fix. This can take a few minutes.'),
    ).toBeInTheDocument()
    expect(await within(panel).findByRole('button', { name: 'Suggest fix' })).toBeDisabled()
    expect(posts(fetchMock)).toHaveLength(1)
  })

  it('lets only one fix be suggested at a time across rows and pages', async () => {
    const suggestion = deferred()
    serveApi({ rows: [FINDING, OTHER], suggest: () => suggestion.promise })
    const { router } = renderRoute('/findings')

    fireEvent.click(await suggestButton(FINDING))

    expect(await suggestButton(OTHER)).toBeDisabled()
    expect(await rowStatus(OTHER)).toHaveTextContent(ANOTHER_BUSY)
    await act(() => router.navigate(`/findings/${OTHER.id}`))
    const panel = await screen.findByRole('region', { name: 'Suggested fixes' })
    expect(await within(panel).findByRole('button', { name: 'Suggest fix' })).toBeDisabled()
    expect(within(panel).getByText(ANOTHER_BUSY)).toBeInTheDocument()
    suggestion.resolve(created())
    await waitFor(() =>
      expect(within(panel).getByRole('button', { name: 'Suggest fix' })).toBeEnabled(),
    )
    expect(within(panel).queryByText(ANOTHER_BUSY)).toBeNull()
  })

  it('sends one suggest when the button is clicked twice at once', async () => {
    const { fetchMock } = serveApi()
    renderRoute('/findings')
    const button = await suggestButton()

    act(() => {
      button.click()
      button.click()
    })

    await within(await rowStatus()).findByText('Fix suggested')
    expect(posts(fetchMock)).toHaveLength(1)
  })

  it('moves focus into the row when the focused button goes away', async () => {
    serveApi()
    renderRoute('/findings')
    const button = await suggestButton()
    button.focus()

    fireEvent.click(button)

    await within(await rowStatus()).findByText('Fix suggested')
    expect(document.activeElement).not.toBe(document.body)
    expect((await row()).contains(document.activeElement)).toBe(true)
  })

  it('leaves focus alone when the button was not focused', async () => {
    serveApi()
    renderRoute('/findings')

    fireEvent.click(await suggestButton())

    await within(await rowStatus()).findByText('Fix suggested')
    expect(document.activeElement).toBe(document.body)
  })

  it('keeps asking the AI when the row turns dismissed meanwhile', async () => {
    const suggestion = deferred()
    const { statuses } = serveApi({ suggest: () => suggestion.promise })
    const { queryClient } = renderRoute('/findings')
    fireEvent.click(await suggestButton())
    await within(await rowStatus()).findByText('Asking the AI…')

    statuses.set(FINDING.id, 'dismissed')
    await act(() => queryClient.invalidateQueries({ queryKey: ['findings'] }))

    expect(await within(await row()).findByText('dismissed')).toBeInTheDocument()
    expect(await rowStatus()).toHaveTextContent('Asking the AI…')
    expect(within(await row()).queryByRole('button', { name: 'Suggest fix' })).toBeNull()
  })

  it('keeps the error when the suggest finds the row dismissed', async () => {
    const dismissedMessage = `Finding ${FINDING.id} is dismissed; there is nothing to correct.`
    const { statuses } = serveApi({
      suggest: (findingId) => {
        statuses.set(findingId, 'dismissed')
        return jsonResponse({ detail: dismissedMessage }, 409)
      },
    })
    renderRoute('/findings')

    fireEvent.click(await suggestButton())

    expect(await within(await row()).findByText('dismissed')).toBeInTheDocument()
    expect(within(await row()).getByRole('alert')).toHaveTextContent(dismissedMessage)
    expect(within(await row()).queryByRole('button', { name: 'Suggest fix' })).toBeNull()
  })
})
