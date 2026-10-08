import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EvidenceList } from './EvidenceList.tsx'

describe('EvidenceList', () => {
  it('renders keys and string/number/boolean values', () => {
    render(<EvidenceList evidence={{ worked_hours: 14, shift_date: '2026-03-02', paid: false }} />)

    expect(screen.getByText('worked_hours').tagName).toBe('DT')
    expect(screen.getByText('14').tagName).toBe('DD')
    expect(screen.getByText('shift_date')).toBeInTheDocument()
    expect(screen.getByText('2026-03-02')).toBeInTheDocument()
    expect(screen.getByText('false')).toBeInTheDocument()
  })

  it('renders a nested object as JSON text', () => {
    render(<EvidenceList evidence={{ limits: { daily: 12, weekly: [40, 45] } }} />)

    expect(screen.getByText('{"daily":12,"weekly":[40,45]}')).toBeInTheDocument()
  })

  it('shows a message when there is no evidence', () => {
    render(<EvidenceList evidence={{}} />)

    expect(screen.getByText('No evidence recorded.')).toBeInTheDocument()
  })
})
