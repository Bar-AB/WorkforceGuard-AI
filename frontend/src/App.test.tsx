import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App.tsx'
import { fakeFetch, jsonResponse } from './test/fakeFetch.ts'

describe('App', () => {
  it('shows config error when VITE_COMPANY_ID is missing', () => {
    vi.stubEnv('VITE_COMPANY_ID', '')

    render(<App />)

    expect(screen.getByRole('heading', { name: 'Configuration error' })).toBeInTheDocument()
    expect(screen.getByText(/VITE_COMPANY_ID/)).toBeInTheDocument()
  })

  it('keeps the config error as plain page content without a live region', () => {
    vi.stubEnv('VITE_COMPANY_ID', '')

    render(<App />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders the product heading and the findings page when configured', async () => {
    const fetchMock = fakeFetch(() => jsonResponse({ items: [], next_cursor: null }))

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'WorkforceGuard AI' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Findings' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Configuration error' })).not.toBeInTheDocument()
  })
})
