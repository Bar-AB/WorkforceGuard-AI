import type { ReactNode } from 'react'
import { InboxIcon } from './icons.tsx'

export function EmptyState({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <div className="flex animate-rise flex-col items-center rounded-2xl border border-dashed border-line bg-surface/60 px-6 py-12 text-center">
      <span className="grid size-12 place-items-center rounded-full bg-accent-soft text-accent-ink">
        <InboxIcon className="size-6" />
      </span>
      <p className="mt-4 font-medium text-ink">{title}</p>
      {action !== undefined && <div className="mt-5">{action}</div>}
    </div>
  )
}
