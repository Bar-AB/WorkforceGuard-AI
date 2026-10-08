import { screen } from '@testing-library/react'
import type { RouteObject } from 'react-router'
import { describe, expect, it, onTestFinished, vi } from 'vitest'
import { routes } from './routes.tsx'
import { fakeFetch, jsonResponse } from './test/fakeFetch.ts'
import { renderRoute } from './test/renderRoute.tsx'

describe('routes', () => {
  it('unknown path shows not found', async () => {
    renderRoute('/no-such-page')

    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'WorkforceGuard AI' })).toBeInTheDocument()
  })

  it('a page that throws shows a friendly error inside the app layout', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    onTestFinished(() => consoleError.mockRestore())
    const [root] = routes
    const withBrokenPage: RouteObject[] = [
      {
        element: root.element,
        errorElement: root.errorElement,
        children: [{ path: 'broken', element: <BrokenPage /> }],
      },
    ]

    renderRoute('/broken', withBrokenPage)

    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByRole('heading', { name: 'WorkforceGuard AI' })).toBeInTheDocument()
    expect(screen.queryByText('page exploded')).not.toBeInTheDocument()
    expect(consoleError.mock.calls.flat()).toContainEqual(
      expect.objectContaining({ message: 'page exploded' }),
    )
  })

  it('redirects / to /findings', async () => {
    fakeFetch(() => jsonResponse({ items: [], next_cursor: null }))

    const { router } = renderRoute('/')

    expect(await screen.findByRole('heading', { name: 'Findings' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/findings')
  })
})

function BrokenPage(): never {
  throw new Error('page exploded')
}
