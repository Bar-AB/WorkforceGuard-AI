import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'
import { afterEach, describe, expect, it } from 'vitest'
import type { FindingDetail } from '../../api/findings.ts'
import { fakeFetch, jsonResponse } from '../../test/fakeFetch.ts'
import { findingDetail, findingSummary } from '../../test/findingFixtures.ts'
import { renderRoute } from '../../test/renderRoute.tsx'

const FINDING = findingDetail()
const DETAIL_PATH = `/findings/${FINDING.id}`

function fact(term: string): HTMLElement {
  const value = screen.getByText(term, { selector: 'dt' }).nextElementSibling
  if (!(value instanceof HTMLElement)) {
    throw new Error(`No value for ${term}`)
  }
  return value
}

function serveDetail(detail: FindingDetail = FINDING) {
  return fakeFetch((url) =>
    url.pathname === `/api/v1/findings/${detail.id}`
      ? jsonResponse(detail)
      : jsonResponse({ items: [], next_cursor: null }),
  )
}

describe('FindingDetailPage', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
  })

  it('shows loading then summary fields', async () => {
    const fetchMock = serveDetail()

    renderRoute(DETAIL_PATH)

    expect(screen.getByRole('status')).toHaveTextContent('Loading finding')
    expect(await screen.findByRole('heading', { name: 'Overtime breach' })).toBeInTheDocument()
    expect(screen.getByText('Worked 14h on 2026-03-02')).toBeInTheDocument()
    expect(fact('Severity')).toHaveTextContent('high')
    expect(fact('Status')).toHaveTextContent('open')
    expect(within(fact('Occurred')).getByText('Mar 2, 2026')).toHaveAttribute(
      'dateTime',
      '2026-03-02',
    )
    expect(within(fact('Detected at')).getByText('Mar 3, 2026, 08:00 UTC')).toHaveAttribute(
      'dateTime',
      '2026-03-03T08:00:00Z',
    )
    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/v1/findings/${FINDING.id}`)
  })

  it('shortens the employee id but keeps the full id for hover and screen readers', async () => {
    serveDetail()

    renderRoute(DETAIL_PATH)

    await screen.findByRole('heading', { name: 'Overtime breach' })
    const employee = fact('Employee')
    expect(within(employee).getByTitle(FINDING.employee_id)).toBeInTheDocument()
    expect(within(employee).getByText('b2c3d4e5…c5d6e')).toHaveAttribute('aria-hidden', 'true')
    expect(within(employee).getByText(FINDING.employee_id)).toHaveClass('sr-only')
  })

  it('shows evidence', async () => {
    serveDetail()

    renderRoute(DETAIL_PATH)

    expect(await screen.findByRole('heading', { name: 'Evidence' })).toBeInTheDocument()
    expect(screen.getByText('worked_hours')).toBeInTheDocument()
    expect(screen.getByText('14')).toBeInTheDocument()
  })

  it('shows pending explanation', async () => {
    serveDetail()

    renderRoute(DETAIL_PATH)

    expect(await screen.findByText('Explanation not ready yet.')).toBeInTheDocument()
    expect(screen.queryByText(/Prompt version/)).not.toBeInTheDocument()
  })

  it('shows explanation with source and prompt version', async () => {
    serveDetail(
      findingDetail({
        explanation: 'The employee worked 14 hours.\nThe limit is 12.',
        explanation_source: 'llm',
        explanation_prompt_version: 'explain_finding@v1',
      }),
    )

    renderRoute(DETAIL_PATH)

    const explanation = await screen.findByText(/The employee worked 14 hours\./)
    expect(explanation.tagName).toBe('P')
    expect(explanation).toHaveClass('whitespace-pre-wrap')
    expect(explanation.textContent).toBe('The employee worked 14 hours.\nThe limit is 12.')
    expect(screen.getByText('Source: llm')).toBeInTheDocument()
    expect(screen.getByText('Prompt version: explain_finding@v1')).toBeInTheDocument()
    expect(screen.queryByText('Explanation not ready yet.')).not.toBeInTheDocument()
  })

  it('renders explanation markup as plain text', async () => {
    const markup = '<img src=x onerror=alert(1)>'
    serveDetail(
      findingDetail({
        explanation: markup,
        explanation_source: 'llm',
        explanation_prompt_version: 'explain_finding@v1',
      }),
    )

    const { container } = renderRoute(DETAIL_PATH)

    expect(await screen.findByText(markup)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
  })

  it.each([
    [404, { detail: 'Finding not found.' }],
    [422, { detail: [{ loc: ['path', 'finding_id'], msg: 'Input should be a valid UUID' }] }],
  ])('shows not found for %i', async (status, body) => {
    fakeFetch(() => jsonResponse(body, status))

    renderRoute('/findings/not-a-uuid')

    expect(await screen.findByRole('heading', { name: 'Finding not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to findings' })).toHaveAttribute(
      'href',
      '/findings',
    )
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('points to VITE_COMPANY_ID for an unknown company, not to a missing finding', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Unknown company.' }, 404))

    renderRoute(DETAIL_PATH)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unknown company. Check VITE_COMPANY_ID in frontend/.env.local.',
    )
    expect(screen.queryByRole('heading', { name: 'Finding not found' })).not.toBeInTheDocument()
  })

  it('shows error and retries', async () => {
    let calls = 0
    fakeFetch(() => {
      calls += 1
      return calls === 1
        ? jsonResponse({ detail: 'Database is down.' }, 503)
        : jsonResponse(FINDING)
    })
    renderRoute(DETAIL_PATH)

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is down.')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('heading', { name: 'Overtime breach' })).toBeInTheDocument()
  })

  it('back link returns to list', async () => {
    serveDetail()
    const { router } = renderRoute(DETAIL_PATH)

    fireEvent.click(await screen.findByRole('link', { name: 'Back to findings' }))

    expect(router.state.location.pathname).toBe('/findings')
    expect(await screen.findByRole('heading', { name: 'Findings' })).toBeInTheDocument()
  })

  it('keeps the finding and shows an alert when a background refresh fails', async () => {
    let calls = 0
    fakeFetch(() => {
      calls += 1
      return calls === 2
        ? jsonResponse({ detail: 'Database is down.' }, 503)
        : jsonResponse(FINDING)
    })
    const { queryClient } = renderRoute(DETAIL_PATH)
    await screen.findByRole('heading', { name: 'Overtime breach' })

    await act(() => queryClient.refetchQueries())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not refresh finding: Database is down.',
    )
    expect(screen.getByRole('heading', { name: 'Overtime breach' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Overtime breach' })).toBeInTheDocument()
  })

  it('back link keeps the list filters it came from', async () => {
    fakeFetch((url) =>
      url.pathname === '/api/v1/findings'
        ? jsonResponse({ items: [findingSummary({ id: FINDING.id })], next_cursor: null })
        : jsonResponse(FINDING),
    )
    const { router } = renderRoute('/findings?severity=high&occurred_from=2026-03-01')

    fireEvent.click(await screen.findByRole('link', { name: FINDING.summary }))
    fireEvent.click(await screen.findByRole('link', { name: 'Back to findings' }))

    expect(router.state.location.pathname).toBe('/findings')
    expect(router.state.location.search).toBe('?severity=high&occurred_from=2026-03-01')
    expect(screen.getByLabelText('Severity')).toHaveValue('high')
  })

  it('back link falls back to the plain list when opened directly', async () => {
    serveDetail()

    renderRoute(DETAIL_PATH)

    expect(await screen.findByRole('link', { name: 'Back to findings' })).toHaveAttribute(
      'href',
      '/findings',
    )
  })

  it('says it is offline instead of loading forever, then loads when back online', async () => {
    const fetchMock = serveDetail()
    onlineManager.setOnline(false)

    renderRoute(DETAIL_PATH)

    expect(await screen.findByRole('status')).toHaveTextContent(
      'You are offline. The finding will load when the connection is back.',
    )
    expect(fetchMock).not.toHaveBeenCalled()
    act(() => onlineManager.setOnline(true))
    expect(await screen.findByRole('heading', { name: 'Overtime breach' })).toBeInTheDocument()
  })

  it('says it is offline when a refresh pauses with the finding on screen', async () => {
    serveDetail()
    const { queryClient } = renderRoute(DETAIL_PATH)
    await screen.findByRole('heading', { name: 'Overtime breach' })
    act(() => onlineManager.setOnline(false))

    act(() => void queryClient.refetchQueries())

    expect(await screen.findByRole('status')).toHaveTextContent('You are offline.')
    expect(screen.getByRole('heading', { name: 'Overtime breach' })).toBeInTheDocument()
  })
})
