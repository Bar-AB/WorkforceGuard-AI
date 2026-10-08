import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SeverityBadge, StatusBadge } from './FindingBadges.tsx'

describe('SeverityBadge', () => {
  it.each([
    ['low', 'low'],
    ['medium', 'medium'],
    ['high', 'high'],
  ])('shows %s in its own tone', (severity, tone) => {
    render(<SeverityBadge severity={severity} />)

    expect(screen.getByText(severity)).toHaveAttribute('data-tone', tone)
  })

  it('shows an unknown severity as is, in the neutral tone', () => {
    render(<SeverityBadge severity="critical" />)

    expect(screen.getByText('critical')).toHaveAttribute('data-tone', 'neutral')
  })
})

describe('StatusBadge', () => {
  it('highlights open findings', () => {
    render(<StatusBadge status="open" />)

    expect(screen.getByText('open')).toHaveAttribute('data-tone', 'accent')
  })

  it('shows other statuses in the neutral tone', () => {
    render(<StatusBadge status="dismissed" />)

    expect(screen.getByText('dismissed')).toHaveAttribute('data-tone', 'neutral')
  })
})
