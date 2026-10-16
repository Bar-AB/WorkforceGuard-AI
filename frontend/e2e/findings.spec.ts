import { expect, test, type Locator, type Page, type Route } from '@playwright/test'

const FINDING_ID = '7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f'

const SUMMARY = {
  id: FINDING_ID,
  employee_id: 'b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e',
  rule_id: 'overtime_breach',
  rule_version: '1',
  severity: 'high',
  status: 'open',
  summary: 'Worked 14h on 2026-03-02',
  occurred_on: '2026-03-02',
  detected_at: '2026-03-03T08:00:00Z',
}

const DETAIL = {
  ...SUMMARY,
  evidence: { worked_hours: 14, shift_date: '2026-03-02' },
  explanation: 'The employee worked 14 hours. The limit is 12.',
  explanation_source: 'llm',
  explanation_prompt_version: 'explain_finding@v1',
}

const SCAN_RESULT = {
  scan_id: '3f2e1d0c-9b8a-4c7d-8e6f-5a4b3c2d1e0f',
  findings_detected: 5,
  findings_created: 2,
  rules_skipped: [],
  findings_explained: 2,
  explanations_fallback: 0,
  explanations_fallback_by_reason: { llm_unavailable: 0, llm_misconfigured: 0, invalid_output: 0 },
  explanations_deferred: 0,
}

const PROPOSAL = {
  id: '5e6f7a8b-9c0d-4e1f-8a2b-3c4d5e6f7a8b',
  finding_id: FINDING_ID,
  target_table: 'shifts',
  target_id: 'c3d4e5f6-a7b8-4c9d-8e0f-1a2b3c4d5e6f',
  patch: { ends_at: '2026-03-02T17:00:00Z' },
  current: { ends_at: '2026-03-02T23:00:00Z' },
  before: { ends_at: '2026-03-02T23:00:00Z' },
  reason: 'The shift end looks mistyped.',
  status: 'pending',
  proposed_by: 'llm:qwen3:4b:propose_correction.v1',
  created_at: '2026-03-03T09:00:00Z',
  decided_by_user_id: null,
  decided_at: null,
  judge_supported: null,
  judge_reason: null,
  judged_by: null,
}

const unexpectedApiPaths: string[] = []
const storedCorrections: (typeof PROPOSAL)[] = []

function fakeApi(route: Route) {
  const { pathname } = new URL(route.request().url())
  const method = route.request().method()
  if (pathname === '/api/v1/scans' && method === 'POST') {
    return route.fulfill({ json: SCAN_RESULT })
  }
  if (pathname === `/api/v1/findings/${FINDING_ID}/corrections` && method === 'POST') {
    storedCorrections.push(PROPOSAL)
    return route.fulfill({ status: 201, json: { proposal: PROPOSAL, reason: null } })
  }
  if (pathname === '/api/v1/findings') {
    const row = { ...SUMMARY, has_pending_correction: storedCorrections.length > 0 }
    return route.fulfill({ json: { items: [row], next_cursor: null } })
  }
  if (pathname === `/api/v1/findings/${FINDING_ID}`) {
    return route.fulfill({ json: DETAIL })
  }
  if (pathname === `/api/v1/findings/${FINDING_ID}/corrections`) {
    return route.fulfill({ json: storedCorrections })
  }
  unexpectedApiPaths.push(pathname)
  return route.fulfill({ status: 500, json: { detail: `Unexpected API call: ${pathname}` } })
}

test.beforeEach(async ({ page }) => {
  unexpectedApiPaths.length = 0
  storedCorrections.length = 0
  await page.route((url) => url.pathname.startsWith('/api/'), fakeApi)
})

test.afterEach(() => {
  expect(unexpectedApiPaths).toEqual([])
})

test('list -> detail smoke', async ({ page }) => {
  await page.goto('/findings')

  const table = page.getByRole('table', { name: 'Findings' })
  await table.getByRole('link', { name: 'Worked 14h on 2026-03-02' }).click()

  await expect(page).toHaveURL(`/findings/${FINDING_ID}`)
  await expect(page.getByRole('heading', { name: 'Overtime breach' })).toBeVisible()
  await expect(page.getByText('worked_hours')).toBeVisible()
  await expect(page.getByText('The employee worked 14 hours. The limit is 12.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Back to findings' })).toBeVisible()
})

async function clickCenterOf(page: Page, locator: Locator) {
  const box = await locator.boundingBox()
  if (box === null) {
    throw new Error('Element has no box to click')
  }
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
}

test('clicking anywhere on a row opens the finding', async ({ page }) => {
  await page.goto('/findings')

  const table = page.getByRole('table', { name: 'Findings' })
  await clickCenterOf(page, table.getByRole('cell', { name: 'Overtime breach' }))

  await expect(page).toHaveURL(`/findings/${FINDING_ID}`)
})

test('finished entrance animations leave no transform behind', async ({ page }) => {
  await page.goto('/findings')
  await expect(page.getByRole('table', { name: 'Findings' })).toBeVisible()

  await expect(page.locator('main section').first()).toHaveCSS('transform', 'none')
})

test('run scan shows the counts and reloads the list', async ({ page }) => {
  await page.goto('/findings')
  await expect(page.getByRole('table', { name: 'Findings' })).toBeVisible()
  const listReloaded = page.waitForRequest(
    (request) => new URL(request.url()).pathname === '/api/v1/findings',
  )

  await page.getByRole('button', { name: 'Run scan' }).click()

  await expect(page.getByText('Scan finished: 2 new findings (5 detected).')).toBeVisible()
  await listReloaded
  await expect(page.getByRole('button', { name: 'Run scan' })).toBeEnabled()
})

test('suggest fix on a row stays on the list and the detail page lists the proposal', async ({
  page,
}) => {
  await page.goto('/findings')
  const table = page.getByRole('table', { name: 'Findings' })

  await table.getByRole('button', { name: 'Suggest fix' }).click()

  await expect(table.getByText('Fix suggested')).toBeVisible()
  await expect(page).toHaveURL('/findings')
  await clickCenterOf(page, table.getByRole('cell', { name: 'Overtime breach' }))
  await expect(page).toHaveURL(`/findings/${FINDING_ID}`)
  const fixes = page.getByRole('region', { name: 'Suggested fixes' })
  await expect(fixes.getByText('The shift end looks mistyped.')).toBeVisible()
})
