import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client.ts'
import { RefreshError } from './RefreshError.tsx'

describe('RefreshError', () => {
  it('shows the message and retries', () => {
    const onRetry = vi.fn()
    render(
      <RefreshError
        subject="findings"
        error={new Error('Database is down.')}
        isRetrying={false}
        onRetry={onRetry}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not refresh findings: Database is down.',
    )
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('disables the button and says Retrying… while the retry runs', () => {
    const onRetry = vi.fn()
    render(
      <RefreshError
        subject="findings"
        error={new Error('Database is down.')}
        isRetrying
        onRetry={onRetry}
      />,
    )

    const button = screen.getByRole('button', { name: 'Retrying…' })
    fireEvent.click(button)

    expect(button).toBeDisabled()
    expect(onRetry).not.toHaveBeenCalled()
  })

  it('points to VITE_COMPANY_ID for an unknown company without a retry button', () => {
    render(
      <RefreshError
        subject="findings"
        error={new ApiError(404, 'Unknown company.')}
        isRetrying={false}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Unknown company. Check VITE_COMPANY_ID in frontend/.env.local.',
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
