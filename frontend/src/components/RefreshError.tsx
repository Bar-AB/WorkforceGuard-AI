import { isUnknownCompany } from '../api/client.ts'
import { Button } from './Button.tsx'
import { UnknownCompany } from './UnknownCompany.tsx'
import { Warning } from './Warning.tsx'

export function RefreshError({
  subject,
  error,
  isRetrying,
  onRetry,
}: {
  subject: string
  error: Error
  isRetrying: boolean
  onRetry: () => void
}) {
  return (
    <Warning>
      {isUnknownCompany(error) ? (
        <UnknownCompany />
      ) : (
        <>
          <p className="flex-1">
            Could not refresh {subject}: {error.message}
          </p>
          <Button disabled={isRetrying} onClick={onRetry}>
            {isRetrying ? 'Retrying…' : 'Try again'}
          </Button>
        </>
      )}
    </Warning>
  )
}
