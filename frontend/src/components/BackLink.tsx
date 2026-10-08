import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { ArrowLeftIcon } from './icons.tsx'

export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="group inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink-muted shadow-sm shadow-black/5 transition hover:border-accent/40 hover:text-accent-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <ArrowLeftIcon className="size-4 transition-transform group-hover:-translate-x-0.5" />
      {children}
    </Link>
  )
}
