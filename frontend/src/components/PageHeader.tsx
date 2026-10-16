import type { ReactNode } from 'react'

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2 className="text-2xl font-semibold tracking-tight text-ink">{title}</h2>
        <p className="mt-1 max-w-2xl text-sm text-ink-muted">{description}</p>
      </div>
      {action}
    </header>
  )
}
