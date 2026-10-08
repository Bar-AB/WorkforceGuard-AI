import type { ReactNode } from 'react'

export function Card({ className = '', children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={`rounded-2xl border border-line bg-surface shadow-sm shadow-black/5 ${className}`}
    >
      {children}
    </div>
  )
}
