import type { ReactNode } from 'react'
import { AlertIcon, OfflineIcon, WarningIcon } from './icons.tsx'

type CalloutTone = 'danger' | 'caution' | 'offline'

const TONE_CLASSES: Record<CalloutTone, string> = {
  danger: 'border-danger-line bg-danger-soft text-danger-ink',
  caution: 'border-caution-line bg-caution-soft text-caution-ink',
  offline: 'border-line bg-surface text-ink-muted',
}

const TONE_ICONS: Record<CalloutTone, ReactNode> = {
  danger: <AlertIcon className="size-5" />,
  caution: <WarningIcon className="size-5" />,
  offline: <OfflineIcon className="size-5" />,
}

export function Callout({
  tone,
  role,
  children,
}: {
  tone: CalloutTone
  role?: 'alert' | 'status'
  children: ReactNode
}) {
  return (
    <div
      role={role}
      className={`flex animate-rise gap-3 rounded-xl border px-4 py-3 text-sm shadow-sm shadow-black/5 ${TONE_CLASSES[tone]}`}
    >
      <span className="mt-px shrink-0">{TONE_ICONS[tone]}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-2">{children}</div>
    </div>
  )
}
