import { Callout } from './Callout.tsx'

export function Offline({ subject }: { subject: string }) {
  return (
    <Callout tone="offline" role="status">
      <p>You are offline. {subject} will load when the connection is back.</p>
    </Callout>
  )
}
