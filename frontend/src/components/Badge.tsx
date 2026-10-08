import type { ReactNode } from 'react'

export type BadgeTone = 'low' | 'medium' | 'high' | 'accent' | 'caution' | 'neutral'

const TONE_CLASSES: Record<BadgeTone, string> = {
  low: 'bg-low-soft text-low-ink ring-low-line',
  medium: 'bg-medium-soft text-medium-ink ring-medium-line',
  high: 'bg-high-soft text-high-ink ring-high-line',
  accent: 'bg-accent-soft text-accent-ink ring-accent/30',
  caution: 'bg-caution-soft text-caution-ink ring-caution-line',
  neutral: 'bg-neutral-soft text-neutral-ink ring-neutral-line',
}

export function Badge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return (
    <span
      data-tone={tone}
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset first-letter:uppercase ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  )
}
