import { act, fireEvent, screen, within } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Correction } from '../../api/corrections.ts'
import type { FindingDetail } from '../../api/findings.ts'
import { correction } from '../../test/correctionFixtures.ts'
import { fakeFetch, jsonResponse } from '../../test/fakeFetch.ts'
import { findingDetail, findingRow } from '../../test/findingFixtures.ts'
import { renderRoute } from '../../test/renderRoute.tsx'

const FINDING = findingDetail()
const DETAIL_PATH = `/findings/${FINDING.id}`
const CORRECTIONS_PATH = `/api/v1/findings/${FINDING.id}/corrections`
const PROPOSAL = correction()
const USER_ID = '00000000-0000-4000-8000-0000000000aa'
const SUGGEST_LOST =
  'Lost the connection. The fix may still be in progress. Open the finding again in a few minutes.'

type Handler = (init: RequestInit) => Response | Promise<Response>

interface FakeApi {
  finding?: () => FindingDetail
  corrections?: Handler
  suggest?: Handler
  decide?: (verdict: string, init: RequestInit) => Response | Promise<Response>
}

function serveApi({
  finding = () => FINDING,
  corrections = () => jsonResponse([]),
  suggest = () => jsonResponse({ detail: 'Unexpected suggest.' }, 500),
  decide = () => jsonResponse({ detail: 'Unexpected decision.' }, 500),
}: FakeApi) {
  return fakeFetch((url, init) => {
    if (url.pathname === '/api/v1/findings') {
      return jsonResponse({ items: [findingRow({ id: FINDING.id })], next_cursor: null })
    }
    if (url.pathname === `/api/v1/findings/${FINDING.id}`) {
      return jsonResponse(finding())
    }
    if (url.pathname === CORRECTIONS_PATH) {
      return init.method === 'POST' ? suggest(init) : corrections(init)
    }
    const decision = /^\/api\/v1\/corrections\/[^/]+\/(approve|reject)$/.exec(url.pathname)
    if (decision !== null) {
      return decide(decision[1], init)
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

async function panel(): Promise<HTMLElement> {
  return screen.findByRole('region', { name: 'Suggested fixes' })
}

function card(): HTMLElement {
  return screen.getByRole('article', { name: /Shift/ })
}

function decidedCorrection(status: 'approved' | 'rejected'): Correction {
  return correction({
    status,
    decided_by_user_id: USER_ID,
    decided_at: '2026-03-04T10:00:00Z',
  })
}

describe('CorrectionsPanel', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
  })

  it('shows empty state', async () => {
    serveApi({})

    renderRoute(DETAIL_PATH)

    const section = await panel()
    expect(await within(section).findByText('No fixes suggested yet.')).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: 'Suggest fix' })).toBeEnabled()
  })

  it('shows loading then pending proposal with before and after', async () => {
    const list = deferred()
    serveApi({ corrections: () => list.promise })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    expect(within(section).getByRole('status')).toHaveTextContent('Loading suggested fixes')
    list.resolve(jsonResponse([PROPOSAL]))
    const proposal = await within(section).findByRole('article', { name: /Shift/ })
    expect(within(proposal).getByTitle(PROPOSAL.target_id)).toBeInTheDocument()
    const change = within(proposal).getByRole('listitem')
    expect(within(change).getByText('ends_at')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 23:00 UTC')).toBeInTheDocument()
    expect(within(change).getByText('Mar 2, 2026, 17:00 UTC')).toBeInTheDocument()
    expect(within(proposal).getByText(PROPOSAL.reason)).toBeInTheDocument()
    expect(within(proposal).getByText(/llm:qwen3:4b:propose_correction.v1/)).toBeInTheDocument()
    expect(within(proposal).getByText('Pending')).toBeInTheDocument()
    expect(within(proposal).getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(within(proposal).getByRole('button', { name: 'Reject' })).toBeEnabled()
  })

  it('says it is offline instead of loading the fixes forever', async () => {
    let online = true
    serveApi({
      finding: () => {
        if (online) {
          online = false
          act(() => onlineManager.setOnline(false))
        }
        return FINDING
      },
      corrections: () => jsonResponse(online ? [] : [PROPOSAL]),
    })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    expect(within(section).getByRole('status')).toHaveTextContent(
      'You are offline. Suggested fixes will load when the connection is back.',
    )
    act(() => onlineManager.setOnline(true))
    expect(await within(section).findByRole('article', { name: /Shift/ })).toBeInTheDocument()
  })

  it('shows the error and retries loading the fixes', async () => {
    let calls = 0
    serveApi({
      corrections: () => {
        calls += 1
        return calls === 1
          ? jsonResponse({ detail: 'Database is down.' }, 503)
          : jsonResponse([PROPOSAL])
      },
    })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    expect(await within(section).findByRole('alert')).toHaveTextContent('Database is down.')
    fireEvent.click(within(section).getByRole('button', { name: 'Try again' }))
    expect(await within(section).findByRole('article', { name: /Shift/ })).toBeInTheDocument()
  })

  it('suggest shows waiting text then the new proposal', async () => {
    const suggestion = deferred()
    let stored: Correction[] = []
    serveApi({ corrections: () => jsonResponse(stored), suggest: () => suggestion.promise })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Suggest fix' }))

    expect(await within(section).findByRole('status')).toHaveTextContent(
      'Asking the AI for a fix. This can take a few minutes.',
    )
    expect(within(section).getByRole('button', { name: 'Suggest fix' })).toBeDisabled()
    stored = [PROPOSAL]
    suggestion.resolve(jsonResponse({ proposal: PROPOSAL, reason: null }, 201))
    expect(await within(section).findByRole('article', { name: /Shift/ })).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Suggest fix' })).not.toBeInTheDocument()
    expect(within(section).queryByText(/Asking the AI/)).not.toBeInTheDocument()
  })

  it('suggest with no fix shows the reason', async () => {
    serveApi({
      suggest: () =>
        jsonResponse({ proposal: null, reason: 'The hours look real. No data error to fix.' }),
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Suggest fix' }))

    const reason = await within(section).findByText(/The hours look real/)
    expect(reason.closest('[role="status"]')).toHaveTextContent(
      'No fix suggested: The hours look real. No data error to fix.',
    )
    expect(within(section).getByText('No fixes suggested yet.')).toBeInTheDocument()
  })

  it('suggest error shows the API detail', async () => {
    serveApi({
      suggest: () => jsonResponse({ detail: 'The AI model is not configured correctly.' }, 503),
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Suggest fix' }))

    expect(await within(section).findByRole('alert')).toHaveTextContent(
      'The AI model is not configured correctly.',
    )
    expect(within(section).getByRole('button', { name: 'Suggest fix' })).toBeEnabled()
  })

  it('hides suggest while a proposal is pending', async () => {
    serveApi({ corrections: () => jsonResponse([PROPOSAL]) })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    await within(section).findByRole('article', { name: /Shift/ })
    expect(within(section).queryByRole('button', { name: 'Suggest fix' })).not.toBeInTheDocument()
  })

  it('offers suggest again when every proposal is decided', async () => {
    serveApi({ corrections: () => jsonResponse([decidedCorrection('rejected')]) })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    expect(await within(section).findByRole('button', { name: 'Suggest fix' })).toBeEnabled()
  })

  it('hides suggest for dismissed findings', async () => {
    serveApi({ finding: () => findingDetail({ status: 'dismissed' }) })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    expect(await within(section).findByText('No fixes suggested yet.')).toBeInTheDocument()
    expect(within(section).queryByRole('button', { name: 'Suggest fix' })).not.toBeInTheDocument()
  })

  it('approve sends X-User-Id and shows Approved', async () => {
    let stored = [PROPOSAL]
    let status = 'open'
    const fetchMock = serveApi({
      finding: () => findingDetail({ status }),
      corrections: () => jsonResponse(stored),
      decide: () => {
        stored = [decidedCorrection('approved')]
        status = 'confirmed'
        return jsonResponse(stored[0])
      },
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Approve' }))

    expect(await within(section).findByText('Approved')).toBeInTheDocument()
    expect(within(card()).getByText('Decided Mar 4, 2026, 10:00 UTC')).toBeInTheDocument()
    expect(within(card()).queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    const call = fetchMock.mock.calls.find(([path]) => String(path).endsWith('/approve'))
    expect(String(call?.[0])).toBe(`/api/v1/corrections/${PROPOSAL.id}/approve`)
    expect(call?.[1]?.method).toBe('POST')
    expect(new Headers(call?.[1]?.headers).get('X-User-Id')).toBe(USER_ID)
    expect(await screen.findByText('confirmed')).toBeInTheDocument()
  })

  it('reject shows Rejected', async () => {
    let stored = [PROPOSAL]
    const verdicts: string[] = []
    serveApi({
      corrections: () => jsonResponse(stored),
      decide: (verdict) => {
        verdicts.push(verdict)
        stored = [decidedCorrection('rejected')]
        return jsonResponse(stored[0])
      },
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Reject' }))

    expect(await within(section).findByText('Rejected')).toBeInTheDocument()
    expect(verdicts).toEqual(['reject'])
  })

  it('disables decisions without VITE_USER_ID', async () => {
    vi.stubEnv('VITE_USER_ID', '')
    serveApi({ corrections: () => jsonResponse([PROPOSAL]) })

    renderRoute(DETAIL_PATH)

    const section = await panel()
    const proposal = await within(section).findByRole('article', { name: /Shift/ })
    expect(within(proposal).getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(within(proposal).getByRole('button', { name: 'Reject' })).toBeDisabled()
    expect(within(proposal).getByText('Set VITE_USER_ID to approve or reject.')).toBeInTheDocument()
  })

  it('decision conflict shows the API detail', async () => {
    serveApi({
      corrections: () => jsonResponse([PROPOSAL]),
      decide: () =>
        jsonResponse(
          { detail: 'The finding is locked by another update. Try again shortly.' },
          409,
        ),
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Approve' }))

    expect(await within(card()).findByRole('alert')).toHaveTextContent(
      'The finding is locked by another update. Try again shortly.',
    )
    expect(within(card()).getByText('Pending')).toBeInTheDocument()
  })

  it('stale approve shows the refusal and keeps the card pending', async () => {
    const refusal =
      'The row changed since this fix was proposed. Reject it and ask for a new suggestion.'
    serveApi({
      corrections: () => jsonResponse([PROPOSAL]),
      decide: () => jsonResponse({ detail: refusal }, 409),
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Approve' }))

    expect(await within(card()).findByRole('alert')).toHaveTextContent(refusal)
    expect(within(card()).getByText('Pending')).toBeInTheDocument()
    expect(within(card()).getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(within(card()).getByRole('button', { name: 'Reject' })).toBeEnabled()
  })
})

describe('CorrectionsPanel when requests go wrong', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
  })

  it('refreshes the fixes when a suggest loses the connection', async () => {
    let stored: Correction[] = []
    serveApi({
      corrections: () => jsonResponse(stored),
      suggest: () => {
        stored = [PROPOSAL]
        return Promise.reject(new TypeError('Failed to fetch'))
      },
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Suggest fix' }))

    expect(await within(section).findByRole('article', { name: /Shift/ })).toBeInTheDocument()
    expect(within(section).getByRole('alert')).toHaveTextContent(SUGGEST_LOST)
    expect(within(section).queryByText(/Failed to fetch/)).not.toBeInTheDocument()
  })

  it('keeps waiting for a suggest after leaving the page and coming back', async () => {
    const suggestion = deferred()
    const fetchMock = serveApi({ suggest: () => suggestion.promise })
    const { router } = renderRoute(DETAIL_PATH)
    fireEvent.click(await within(await panel()).findByRole('button', { name: 'Suggest fix' }))

    fireEvent.click(screen.getByRole('link', { name: 'Back to findings' }))
    await screen.findByRole('heading', { name: 'Findings' })
    await act(() => router.navigate(DETAIL_PATH))

    const section = await panel()
    expect(await within(section).findByRole('status')).toHaveTextContent(/Asking the AI/)
    expect(within(section).getByRole('button', { name: 'Suggest fix' })).toBeDisabled()
    suggestion.resolve(
      jsonResponse({ detail: 'The AI model is unavailable. Try again later.' }, 503),
    )
    expect(await within(section).findByRole('alert')).toHaveTextContent(
      'The AI model is unavailable. Try again later.',
    )
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')
    expect(posts).toHaveLength(1)
  })

  it('says a suggest waits for the connection when offline', async () => {
    const fetchMock = serveApi({})
    renderRoute(DETAIL_PATH)
    const section = await panel()
    const button = await within(section).findByRole('button', { name: 'Suggest fix' })
    act(() => onlineManager.setOnline(false))

    fireEvent.click(button)

    expect(await within(section).findByRole('status')).toHaveTextContent(
      'You are offline. The fix will be requested when the connection is back.',
    )
    expect(within(section).queryByText(/Asking the AI/)).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0)
  })

  it('shows the decided state after a decision conflict', async () => {
    let stored = [PROPOSAL]
    serveApi({
      corrections: () => jsonResponse(stored),
      decide: () => {
        stored = [decidedCorrection('approved')]
        return jsonResponse({ detail: 'This fix is already approved.' }, 409)
      },
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Reject' }))

    expect(await within(section).findByText('Approved')).toBeInTheDocument()
    expect(within(card()).queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('says a decision waits for the connection when offline', async () => {
    const decide = vi.fn(() => jsonResponse(decidedCorrection('approved')))
    serveApi({ corrections: () => jsonResponse([PROPOSAL]), decide })
    renderRoute(DETAIL_PATH)
    const approve = await within(await panel()).findByRole('button', { name: 'Approve' })
    act(() => onlineManager.setOnline(false))

    fireEvent.click(approve)

    expect(await within(card()).findByRole('status')).toHaveTextContent(
      'You are offline. The decision will be sent when the connection is back.',
    )
    expect(within(card()).getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(decide).not.toHaveBeenCalled()
  })

  it('sends only one decision when approve and reject are clicked at once', async () => {
    const verdicts: string[] = []
    let stored = [PROPOSAL]
    serveApi({
      corrections: () => jsonResponse(stored),
      decide: (verdict) => {
        verdicts.push(verdict)
        stored = [decidedCorrection('approved')]
        return jsonResponse(stored[0])
      },
    })
    renderRoute(DETAIL_PATH)
    const section = await panel()
    const proposal = await within(section).findByRole('article', { name: /Shift/ })
    const approve = within(proposal).getByRole('button', { name: 'Approve' })
    const reject = within(proposal).getByRole('button', { name: 'Reject' })

    act(() => {
      approve.click()
      reject.click()
    })

    expect(await within(section).findByText('Approved')).toBeInTheDocument()
    expect(verdicts).toEqual(['approve'])
  })

  it('marks the findings list stale after a decision', async () => {
    serveApi({
      corrections: () => jsonResponse([PROPOSAL]),
      decide: () => jsonResponse(decidedCorrection('approved')),
    })
    const { queryClient } = renderRoute('/findings')
    fireEvent.click(await screen.findByRole('link', { name: FINDING.summary }))
    const section = await panel()

    fireEvent.click(await within(section).findByRole('button', { name: 'Approve' }))

    await vi.waitFor(() => {
      const lists = queryClient.getQueryCache().findAll({ queryKey: ['findings'] })
      expect(lists.length).toBeGreaterThan(0)
      expect(lists.every((query) => query.state.isInvalidated)).toBe(true)
    })
  })

  it('links the missing user hint to the decision buttons', async () => {
    vi.stubEnv('VITE_USER_ID', '')
    serveApi({ corrections: () => jsonResponse([PROPOSAL]) })

    renderRoute(DETAIL_PATH)

    const proposal = await within(await panel()).findByRole('article', { name: /Shift/ })
    for (const name of ['Approve', 'Reject']) {
      expect(within(proposal).getByRole('button', { name })).toHaveAccessibleDescription(
        'Set VITE_USER_ID to approve or reject.',
      )
    }
  })
})
