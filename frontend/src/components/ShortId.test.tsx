import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ShortId } from './ShortId.tsx'

const ID = 'fa02eaec-1b2c-4d3e-8f4a-5b6c7d88adfe'

describe('ShortId', () => {
  it('shows the short id and keeps the full id in the title and for screen readers', () => {
    render(<ShortId value={ID} />)

    expect(screen.getByTitle(ID)).toHaveTextContent(ID)
    expect(screen.getByText('fa02eaec…8adfe')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByText(ID)).toHaveClass('sr-only')
  })
})
