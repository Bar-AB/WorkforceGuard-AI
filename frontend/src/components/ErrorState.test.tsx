import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client.ts'
import { ErrorState } from './ErrorState.tsx'

describe('ErrorState', () => {
  it('shows the error message in an alert', () => {
    render(
      <ErrorState
        error={new Error('The API is unavailable (500). Check that the backend is running.')}
        onRetry={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(
      'The API is unavailable (500). Check that the backend is running.',
    )
  })

  it('calls onRetry when Try again is clicked', () => {
    const onRetry = vi.fn()
    render(
      <ErrorState
        error={new Error('The API is unavailable (500). Check that the backend is running.')}
        onRetry={onRetry}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('points to VITE_COMPANY_ID for an unknown company without a retry button', () => {
    render(<ErrorState error={new ApiError(404, 'Unknown company.')} onRetry={vi.fn()} />)

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Unknown company. Check VITE_COMPANY_ID in frontend/.env.local.',
    )
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
  })
})
