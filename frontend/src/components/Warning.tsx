import type { ReactNode } from 'react'
import { Callout } from './Callout.tsx'

export function Warning({ children }: { children: ReactNode }) {
  return (
    <Callout tone="caution" role="alert">
      {children}
    </Callout>
  )
}
