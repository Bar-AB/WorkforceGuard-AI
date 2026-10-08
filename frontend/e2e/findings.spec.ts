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

const unexpectedApiPaths: string[] = []

function fakeApi(route: Route) {
  const { pathname } = new URL(route.request().url())
  if (pathname === '/api/v1/findings') {
    return route.fulfill({ json: { items: [SUMMARY], next_cursor: null } })
  }
  if (pathname === `/api/v1/findings/${FINDING_ID}`) {
    return route.fulfill({ json: DETAIL })
  }
  unexpectedApiPaths.push(pathname)
  return route.fulfill({ status: 500, json: { detail: `Unexpected API call: ${pathname}` } })
}

test.beforeEach(async ({ page }) => {
  unexpectedApiPaths.length = 0
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
