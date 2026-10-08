import type { ReactNode } from 'react'

export function Button({
  children,
  onClick,
  disabled = false,
}: {
  children: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center justify-center gap-2 rounded-lg border border-field-line bg-surface px-3.5 py-2 text-sm font-medium text-ink shadow-sm shadow-black/5 transition hover:border-accent/40 hover:bg-accent-soft hover:text-accent-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent active:translate-y-px disabled:cursor-not-allowed disabled:opacity-60"
    >
      {children}
    </button>
  )
}
