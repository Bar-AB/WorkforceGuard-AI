import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { onlineManager } from '@tanstack/react-query'
import { afterEach, describe, expect, it } from 'vitest'
import { fakeFetch, jsonResponse } from '../../test/fakeFetch.ts'
import { findingSummary } from '../../test/findingFixtures.ts'
import { renderRoute } from '../../test/renderRoute.tsx'

const FIRST = findingSummary({ id: '11111111-1111-4111-8111-111111111111', summary: 'First row' })
const SECOND = findingSummary({ id: '22222222-2222-4222-8222-222222222222', summary: 'Second row' })

const RANGE_MESSAGE = 'Occurred from must be on or before Occurred to.'

function page(items = [FIRST], nextCursor: string | null = null): Response {
  return jsonResponse({ items, next_cursor: nextCursor })
}

function requestedParams(fetchMock: ReturnType<typeof fakeFetch>, call: number): URLSearchParams {
  return new URL(String(fetchMock.mock.calls[call][0]), window.location.origin).searchParams
}

describe('FindingsListPage', () => {
  afterEach(() => {
    onlineManager.setOnline(true)
  })

  it('shows loading then rows', async () => {
    fakeFetch(() => page())

    renderRoute('/findings')

    expect(screen.getByRole('status')).toHaveTextContent('Loading findings')
    const table = await screen.findByRole('table', { name: 'Findings' })
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent)
    expect(headers).toEqual(['Occurred on', 'Type', 'Severity', 'Status', 'Summary'])
    const row = within(table).getByRole('row', { name: /First row/ })
    expect(within(row).getByText('Mar 2, 2026')).toHaveAttribute('dateTime', '2026-03-02')
    expect(row).toHaveTextContent('Overtime breach')
    expect(row).toHaveTextContent('high')
    expect(row).toHaveTextContent('open')
  })

  it('applies filters from the URL on first load', async () => {
    const fetchMock = fakeFetch(() => page())

    renderRoute(
      '/findings?rule_id=overtime_breach&severity=high&occurred_from=2026-03-01&occurred_to=2026-03-31',
    )

    await screen.findByRole('table', { name: 'Findings' })
    expect(requestedParams(fetchMock, 0).toString()).toBe(
      'limit=50&rule_id=overtime_breach&severity=high&occurred_from=2026-03-01&occurred_to=2026-03-31',
    )
    expect(screen.getByLabelText('Type')).toHaveValue('overtime_breach')
    expect(screen.getByLabelText('Severity')).toHaveValue('high')
    expect(screen.getByLabelText('Occurred from')).toHaveValue('2026-03-01')
    expect(screen.getByLabelText('Occurred to')).toHaveValue('2026-03-31')
  })

  it('drops invalid filters from the URL before calling the API', async () => {
    const fetchMock = fakeFetch(() => page())

    renderRoute('/findings?severity=critical&occurred_from=03/01/2026')

    await screen.findByRole('table', { name: 'Findings' })
    expect(requestedParams(fetchMock, 0).toString()).toBe('limit=50')
  })

  it('changing severity updates the URL and the request', async () => {
    const fetchMock = fakeFetch(() => page())
    const { router } = renderRoute('/findings')
    await screen.findByRole('table', { name: 'Findings' })

    fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'medium' } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(router.state.location.search).toBe('?severity=medium')
    expect(requestedParams(fetchMock, 1).get('severity')).toBe('medium')
  })

  it('changing the date range updates the URL', async () => {
    const fetchMock = fakeFetch(() => page())
    const { router } = renderRoute('/findings')
    await screen.findByRole('table', { name: 'Findings' })

    fireEvent.change(screen.getByLabelText('Occurred from'), { target: { value: '2026-03-01' } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(router.state.location.search).toBe('?occurred_from=2026-03-01')
  })

  it('clear removes filters', async () => {
    const fetchMock = fakeFetch(() => page())
    const { router } = renderRoute('/findings?severity=high&rule_id=overtime_breach')
    await screen.findByRole('table', { name: 'Findings' })

    fireEvent.click(
      within(screen.getByRole('form', { name: 'Filter findings' })).getByRole('button', {
        name: 'Clear filters',
      }),
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(router.state.location.search).toBe('')
    expect(requestedParams(fetchMock, 1).toString()).toBe('limit=50')
    expect(screen.getByLabelText('Severity')).toHaveValue('')
  })

  it('shows empty state with clear action', async () => {
    fakeFetch(() => page([]))
    const { router } = renderRoute('/findings?severity=low')

    expect(await screen.findByText('No findings match these filters.')).toBeInTheDocument()
    const [, emptyStateClear] = screen.getAllByRole('button', { name: 'Clear filters' })
    fireEvent.click(emptyStateClear)

    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(await screen.findByText('No findings yet.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Clear filters' })).toHaveLength(1)
  })

  it('shows error and retries', async () => {
    let calls = 0
    fakeFetch(() => {
      calls += 1
      return calls === 1 ? jsonResponse({ detail: 'Database is down.' }, 503) : page()
    })
    renderRoute('/findings')

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is down.')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('table', { name: 'Findings' })).toBeInTheDocument()
  })

  it('points to VITE_COMPANY_ID for an unknown company instead of offering a retry', async () => {
    fakeFetch(() => jsonResponse({ detail: 'Unknown company.' }, 404))
    renderRoute('/findings')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unknown company. Check VITE_COMPANY_ID in frontend/.env.local.',
    )
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })

  it('load more appends next page with cursor and filters', async () => {
    const fetchMock = fakeFetch((url) =>
      url.searchParams.get('cursor') === 'next-1' ? page([SECOND]) : page([FIRST], 'next-1'),
    )
    renderRoute('/findings?severity=high')
    await screen.findByRole('row', { name: /First row/ })

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByRole('row', { name: /Second row/ })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
    expect(screen.getByText('Showing 2 findings')).toBeInTheDocument()
    expect(requestedParams(fetchMock, 1).toString()).toBe('limit=50&severity=high&cursor=next-1')
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('disables load more while the next page is loading', async () => {
    let releaseNextPage: (response: Response) => void = () => undefined
    fakeFetch((url) =>
      url.searchParams.has('cursor')
        ? new Promise<Response>((resolve) => {
            releaseNextPage = resolve
          })
        : page([FIRST], 'next-1'),
    )
    renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    await waitFor(() => expect(screen.getByRole('button', { name: /Loading/ })).toBeDisabled())
    releaseNextPage(page([SECOND]))
    expect(await screen.findByRole('row', { name: /Second row/ })).toBeInTheDocument()
  })

  it('load more failure shows retry', async () => {
    let nextPageCalls = 0
    fakeFetch((url) => {
      if (!url.searchParams.has('cursor')) {
        return page([FIRST], 'next-1')
      }
      nextPageCalls += 1
      return nextPageCalls === 1
        ? jsonResponse({ detail: 'Database is down.' }, 503)
        : page([SECOND])
    })
    renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is down.')
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('row', { name: /Second row/ })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('counts the findings on screen', async () => {
    fakeFetch(() => page())

    renderRoute('/findings')

    expect(await screen.findByText('Showing 1 finding')).toBeInTheDocument()
  })

  it('gives each row exactly one link, to its finding', async () => {
    fakeFetch(() => page([FIRST, SECOND]))

    renderRoute('/findings')

    const row = await screen.findByRole('row', { name: /Second row/ })
    const links = within(row).getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', `/findings/${SECOND.id}`)
    expect(links[0]).toHaveAccessibleName('Second row')
  })

  it('row link opens detail', async () => {
    fakeFetch((url) =>
      url.pathname === '/api/v1/findings' ? page() : jsonResponse({ detail: 'x' }, 503),
    )
    const { router } = renderRoute('/findings')

    fireEvent.click(await screen.findByRole('link', { name: 'First row' }))

    expect(router.state.location.pathname).toBe(`/findings/${FIRST.id}`)
  })

  it('keeps the rows and shows an alert when a background refresh fails', async () => {
    let calls = 0
    fakeFetch(() => {
      calls += 1
      return calls === 2 ? jsonResponse({ detail: 'Database is down.' }, 503) : page()
    })
    const { queryClient } = renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })

    await act(() => queryClient.refetchQueries())

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not refresh findings: Database is down.',
    )
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
  })

  it('bounds each date input by the other one', async () => {
    fakeFetch(() => page())

    renderRoute('/findings?occurred_from=2026-03-01&occurred_to=2026-03-31')

    await screen.findByRole('table', { name: 'Findings' })
    expect(screen.getByLabelText('Occurred from')).toHaveAttribute('max', '2026-03-31')
    expect(screen.getByLabelText('Occurred to')).toHaveAttribute('min', '2026-03-01')
    expect(screen.getByLabelText('Occurred to')).toHaveAttribute('max', '9999-12-31')
  })

  it('caps both date inputs at a four-digit year when no range is set', async () => {
    fakeFetch(() => page())

    renderRoute('/findings')

    await screen.findByRole('table', { name: 'Findings' })
    expect(screen.getByLabelText('Occurred from')).toHaveAttribute('max', '9999-12-31')
    expect(screen.getByLabelText('Occurred to')).toHaveAttribute('max', '9999-12-31')
  })

  it('shows a range message and sends nothing for an inverted range in the URL', async () => {
    const fetchMock = fakeFetch(() => page())
    const { router } = renderRoute('/findings?occurred_from=2026-03-31&occurred_to=2026-03-01')

    expect(await screen.findByRole('alert')).toHaveTextContent(RANGE_MESSAGE)
    expect(screen.getByLabelText('Occurred from')).toHaveValue('2026-03-31')
    expect(screen.getByLabelText('Occurred to')).toHaveValue('2026-03-01')
    expect(fetchMock).not.toHaveBeenCalled()

    fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Clear dates' }))

    await screen.findByRole('table', { name: 'Findings' })
    expect(router.state.location.search).toBe('')
    expect(requestedParams(fetchMock, 0).toString()).toBe('limit=50')
  })

  it('clear dates in the range message keeps type and severity', async () => {
    const fetchMock = fakeFetch(() => page())
    const { router } = renderRoute(
      '/findings?rule_id=overtime_breach&severity=high&occurred_from=2026-03-31&occurred_to=2026-03-01',
    )

    fireEvent.click(
      within(await screen.findByRole('alert')).getByRole('button', { name: 'Clear dates' }),
    )

    await screen.findByRole('table', { name: 'Findings' })
    expect(router.state.location.search).toBe('?rule_id=overtime_breach&severity=high')
    expect(requestedParams(fetchMock, 0).toString()).toBe(
      'limit=50&rule_id=overtime_breach&severity=high',
    )
    expect(screen.getByLabelText('Occurred from')).toHaveValue('')
    expect(screen.getByLabelText('Occurred to')).toHaveValue('')
  })

  it('keeps typed dates and holds the request while Occurred to is before Occurred from', async () => {
    const fetchMock = fakeFetch(() => page())
    renderRoute('/findings?occurred_from=2026-01-01')
    await screen.findByRole('table', { name: 'Findings' })
    const occurredFrom = screen.getByLabelText('Occurred from')
    const occurredTo = screen.getByLabelText('Occurred to')

    fireEvent.change(occurredTo, { target: { value: '0002-10-08' } })

    expect(await screen.findByRole('alert')).toHaveTextContent(RANGE_MESSAGE)
    expect(occurredFrom).toHaveValue('2026-01-01')
    expect(occurredTo).toHaveValue('0002-10-08')
    expect(screen.queryByRole('table', { name: 'Findings' })).not.toBeInTheDocument()

    fireEvent.change(occurredTo, { target: { value: '2026-10-08' } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(requestedParams(fetchMock, 1).toString()).toBe(
      'limit=50&occurred_from=2026-01-01&occurred_to=2026-10-08',
    )
    expect(fetchMock.mock.calls.map(([url]) => String(url))).not.toContainEqual(
      expect.stringContaining('0002-10-08'),
    )
    expect(await screen.findByRole('table', { name: 'Findings' })).toBeInTheDocument()
    expect(screen.queryByText(RANGE_MESSAGE)).not.toBeInTheDocument()
  })

  it('disables Try again and says Retrying… while a failed refresh is retried', async () => {
    let calls = 0
    let releaseRetry: (response: Response) => void = () => undefined
    fakeFetch(() => {
      calls += 1
      if (calls === 2) {
        return jsonResponse({ detail: 'Database is down.' }, 503)
      }
      return calls === 3
        ? new Promise<Response>((resolve) => {
            releaseRetry = resolve
          })
        : page()
    })
    const { queryClient } = renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })
    await act(() => queryClient.refetchQueries())
    await screen.findByRole('alert')

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Retrying…' })).toBeDisabled())
    releaseRetry(page())
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('says it is offline instead of loading forever, then loads when back online', async () => {
    const fetchMock = fakeFetch(() => page())
    onlineManager.setOnline(false)

    renderRoute('/findings')

    expect(await screen.findByRole('status')).toHaveTextContent(
      'You are offline. Findings will load when the connection is back.',
    )
    expect(fetchMock).not.toHaveBeenCalled()
    act(() => onlineManager.setOnline(true))
    expect(await screen.findByRole('table', { name: 'Findings' })).toBeInTheDocument()
  })

  it('replaces the history entry when a filter changes', async () => {
    const fetchMock = fakeFetch(() => page())
    const { router } = renderRoute('/findings')
    await screen.findByRole('table', { name: 'Findings' })

    fireEvent.change(screen.getByLabelText('Occurred from'), { target: { value: '2026-03-01' } })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(router.state.location.search).toBe('?occurred_from=2026-03-01')
    expect(router.state.historyAction).toBe('REPLACE')
  })

  it('keeps the rows while a new filter loads instead of showing the loading message', async () => {
    fakeFetch((url) =>
      url.searchParams.has('occurred_from') ? new Promise<Response>(() => undefined) : page(),
    )
    renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })

    fireEvent.change(screen.getByLabelText('Occurred from'), { target: { value: '0002-10-08' } })

    expect(await screen.findByRole('status')).toHaveTextContent('Updating findings…')
    expect(screen.queryByText('Loading findings…')).not.toBeInTheDocument()
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
  })

  it('replaces the kept rows when the new filter returns no findings', async () => {
    fakeFetch((url) => (url.searchParams.has('severity') ? page([]) : page([FIRST], 'next-1')))
    renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })

    fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'low' } })

    expect(await screen.findByText('No findings match these filters.')).toBeInTheDocument()
    expect(screen.queryByRole('row', { name: /First row/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('replaces the kept rows with the error when the new filter fails', async () => {
    fakeFetch((url) =>
      url.searchParams.has('severity')
        ? jsonResponse({ detail: 'Database is down.' }, 503)
        : page(),
    )
    renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })

    fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'low' } })

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is down.')
    expect(screen.queryByRole('row', { name: /First row/ })).not.toBeInTheDocument()
  })

  it('hides load more while the kept rows belong to the previous filters', async () => {
    fakeFetch((url) =>
      url.searchParams.has('severity')
        ? new Promise<Response>(() => undefined)
        : page([FIRST], 'next-1'),
    )
    renderRoute('/findings')
    await screen.findByRole('button', { name: 'Load more' })

    fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'low' } })

    await screen.findByText('Updating findings…')
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('hides load more while a refresh error is shown', async () => {
    let calls = 0
    fakeFetch(() => {
      calls += 1
      return calls === 2
        ? jsonResponse({ detail: 'Database is down.' }, 503)
        : page([FIRST], 'next-1')
    })
    const { queryClient } = renderRoute('/findings')
    await screen.findByRole('button', { name: 'Load more' })

    await act(() => queryClient.refetchQueries())

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh findings')
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('says it is offline when load more pauses with rows on screen', async () => {
    const fetchMock = fakeFetch(() => page([FIRST], 'next-1'))
    renderRoute('/findings')
    await screen.findByRole('row', { name: /First row/ })
    act(() => onlineManager.setOnline(false))

    fireEvent.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByRole('status')).toHaveTextContent('You are offline.')
    expect(screen.getByRole('row', { name: /First row/ })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
