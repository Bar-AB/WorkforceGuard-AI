import type { ComponentProps } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, expectTypeOf, it, vi } from 'vitest'
import { Button } from './Button.tsx'

describe('Button', () => {
  it('is a non-submitting button that calls onClick', () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Load more</Button>)

    const button = screen.getByRole('button', { name: 'Load more' })
    fireEvent.click(button)

    expect(button).toHaveAttribute('type', 'button')
    expect(onClick).toHaveBeenCalledOnce()
  })

  it('does not call onClick when disabled', () => {
    const onClick = vi.fn()
    render(
      <Button onClick={onClick} disabled>
        Loading…
      </Button>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Loading…' }))

    expect(screen.getByRole('button', { name: 'Loading…' })).toBeDisabled()
    expect(onClick).not.toHaveBeenCalled()
  })

  it('takes no type prop', () => {
    expectTypeOf<ComponentProps<typeof Button>>().not.toHaveProperty('type')
  })
})
