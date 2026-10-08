import type { ReactNode } from 'react'
import { Outlet } from 'react-router'
import { Badge } from './Badge.tsx'
import { BrandIcon } from './icons.tsx'

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="relative isolate min-h-screen">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-96 bg-glow"
      />
      <header className="sticky top-0 z-20 border-b border-line/70 bg-surface/75 backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-4 py-3 sm:px-6">
          <span className="grid size-9 place-items-center rounded-xl bg-linear-to-br from-indigo-500 to-violet-600 text-white shadow-md shadow-indigo-500/30">
            <BrandIcon className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h1 className="text-base leading-tight font-semibold tracking-tight">
              WorkforceGuard AI
            </h1>
            <p className="text-xs text-ink-muted">Anomaly findings</p>
          </div>
          <Badge tone="neutral">Synthetic data</Badge>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">{children}</main>
    </div>
  )
}

export function Layout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  )
}
