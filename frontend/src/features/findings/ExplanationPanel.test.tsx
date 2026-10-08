import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ExplanationPanel } from './ExplanationPanel.tsx'

function renderWithSource(source: string) {
  render(
    <ExplanationPanel explanation="Worked 14 hours." source={source} promptVersion="explain@v1" />,
  )
}

describe('ExplanationPanel', () => {
  it.each([
    ['llm', 'accent'],
    ['fallback', 'caution'],
    ['manual', 'neutral'],
  ])('shows source %s with the %s tone', (source, tone) => {
    renderWithSource(source)

    expect(screen.getByText(`Source: ${source}`)).toHaveAttribute('data-tone', tone)
  })
})
