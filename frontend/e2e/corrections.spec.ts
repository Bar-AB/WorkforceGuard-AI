import { expect, test, type Page, type Route } from '@playwright/test'

const FINDING_ID = '7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f'
const CORRECTION_ID = '5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d'
const USER_ID = '00000000-0000-4000-8000-0000000000aa'

const DETAIL = {
  id: FINDING_ID,
  employee_id: 'b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e',
  rule_id: 'overtime_breach',
  rule_version: '1',
  severity: 'high',
  status: 'open',
  summary: 'Worked 14h on 2026-03-02',
  occurred_on: '2026-03-02',
  detected_at: '2026-03-03T08:00:00Z',
  evidence: { worked_hours: 14, shift_date: '2026-03-02' },
  explanation: null,
  explanation_source: null,
  explanation_prompt_version: null,
}

const JUDGE_REASON = 'The patch keeps the 14h shift.'

const PROPOSAL = {
  id: CORRECTION_ID,
  finding_id: FINDING_ID,
  target_table: 'shifts',
  target_id: 'e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b',
  patch: { ends_at: '2026-03-02T17:00:00Z' },
  current: { ends_at: '2026-03-02T23:00:00Z' },
  before: { ends_at: '2026-03-02T23:00:00Z' },
  reason: 'The clock-out time looks like a typo.',
  status: 'pending',
  proposed_by: 'llm:qwen3:4b:propose_correction.v1',
  created_at: '2026-03-04T09:30:00Z',
  decided_by_user_id: null,
  decided_at: null,
  judge_supported: false,
  judge_reason: JUDGE_REASON,
  judged_by: 'judge_proposal.v1',
}

interface FakeState {
  findingStatus: string
  corrections: (typeof PROPOSAL | ReturnType<typeof decided>)[]
  decisionUserIds: (string | null)[]
}

function decided(status: 'approved' | 'rejected') {
  return {
    ...PROPOSAL,
    status,
    current: status === 'approved' ? PROPOSAL.patch : PROPOSAL.current,
    decided_by_user_id: USER_ID,
    decided_at: '2026-03-04T10:00:00Z',
  }
}

const unexpectedApiPaths: string[] = []

function fakeApi(state: FakeState) {
  return (route: Route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    if (pathname === `/api/v1/findings/${FINDING_ID}`) {
      return route.fulfill({ json: { ...DETAIL, status: state.findingStatus } })
    }
    if (pathname === `/api/v1/findings/${FINDING_ID}/corrections`) {
      if (request.method() === 'POST') {
        state.corrections = [PROPOSAL]
        return route.fulfill({ status: 201, json: { proposal: PROPOSAL, reason: null } })
      }
      return route.fulfill({ json: state.corrections })
    }
    const decision = /^\/api\/v1\/corrections\/([^/]+)\/(approve|reject)$/.exec(pathname)
    if (decision !== null && decision[1] === CORRECTION_ID && request.method() === 'POST') {
      state.decisionUserIds.push(request.headers()['x-user-id'] ?? null)
      const verdict = decision[2] === 'approve' ? 'approved' : 'rejected'
      state.corrections = [decided(verdict)]
      if (verdict === 'approved') {
        state.findingStatus = 'confirmed'
      }
      return route.fulfill({ json: state.corrections[0] })
    }
    unexpectedApiPaths.push(pathname)
    return route.fulfill({ status: 500, json: { detail: `Unexpected API call: ${pathname}` } })
  }
}

async function openFinding(page: Page): Promise<FakeState> {
  const state: FakeState = { findingStatus: 'open', corrections: [], decisionUserIds: [] }
  await page.route((url) => url.pathname.startsWith('/api/'), fakeApi(state))
  await page.goto(`/findings/${FINDING_ID}`)
  return state
}

test.beforeEach(() => {
  unexpectedApiPaths.length = 0
})

test.afterEach(() => {
  expect(unexpectedApiPaths).toEqual([])
})

async function suggestFix(page: Page) {
  const panel = page.getByRole('region', { name: 'Suggested fixes' })
  await expect(panel.getByText('No fixes suggested yet.')).toBeVisible()
  await panel.getByRole('button', { name: 'Suggest fix' }).click()
  const proposal = panel.getByRole('article', { name: /Shift/ })
  await expect(proposal.getByText('Pending')).toBeVisible()
  await expect(proposal.getByText('Mar 2, 2026, 23:00 UTC')).toBeVisible()
  await expect(proposal.getByText('Mar 2, 2026, 17:00 UTC')).toBeVisible()
  await expect(proposal.getByText('Judge: looks wrong')).toBeVisible()
  await expect(proposal.getByText(JUDGE_REASON)).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Suggest fix' })).toHaveCount(0)
  return { panel, proposal }
}

test('suggest -> approve shows approved proposal', async ({ page }) => {
  const state = await openFinding(page)
  const { proposal } = await suggestFix(page)

  await proposal.getByRole('button', { name: 'Approve' }).click()

  await expect(proposal.getByText('Approved')).toBeVisible()
  await expect(proposal.getByText('Decided Mar 4, 2026, 10:00 UTC')).toBeVisible()
  await expect(proposal.getByText('Mar 2, 2026, 23:00 UTC')).toBeVisible()
  await expect(proposal.getByText('Mar 2, 2026, 17:00 UTC')).toBeVisible()
  await expect(proposal.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  await expect(page.getByText('confirmed', { exact: true })).toBeVisible()
  expect(state.decisionUserIds).toEqual([USER_ID])
})

test('suggest -> reject shows rejected proposal', async ({ page }) => {
  const state = await openFinding(page)
  const { panel, proposal } = await suggestFix(page)

  await proposal.getByRole('button', { name: 'Reject' }).click()

  await expect(proposal.getByText('Rejected')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Suggest fix' })).toBeVisible()
  await expect(page.getByText('open', { exact: true })).toBeVisible()
  expect(state.decisionUserIds).toEqual([USER_ID])
})
