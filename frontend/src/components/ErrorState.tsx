import { isUnknownCompany } from '../api/client.ts'
import { Callout } from './Callout.tsx'
import { UnknownCompany } from './UnknownCompany.tsx'

export function ErrorState({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <Callout tone="danger" role="alert">
      {isUnknownCompany(error) ? (
        <UnknownCompany />
      ) : (
        <RetryableError error={error} onRetry={onRetry} />
      )}
    </Callout>
  )
}

function RetryableError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return (
    <>
      <p className="flex-1">{error.message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-lg bg-danger-solid px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger-solid active:translate-y-px"
      >
        Try again
      </button>
    </>
  )
}
