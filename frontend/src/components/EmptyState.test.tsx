import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmptyState } from './EmptyState.tsx'

describe('EmptyState', () => {
  it('shows the title', () => {
    render(<EmptyState title="No findings match these filters." />)

    expect(screen.getByText('No findings match these filters.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('renders the action when given', () => {
    render(
      <EmptyState title="No findings." action={<button type="button">Clear filters</button>} />,
    )

    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeInTheDocument()
  })
})
