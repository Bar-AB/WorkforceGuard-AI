import { useId } from 'react'
import {
  useAnySuggestRunning,
  useCorrections,
  useLatestSuggest,
  useSuggestCorrection,
  type Correction,
  type SuggestOutcome,
  type SuggestState,
} from '../../api/corrections.ts'
import type { FindingDetail } from '../../api/findings.ts'
import { Button } from '../../components/Button.tsx'
import { Callout } from '../../components/Callout.tsx'
import { Card } from '../../components/Card.tsx'
import { ErrorState } from '../../components/ErrorState.tsx'
import { SparkIcon } from '../../components/icons.tsx'
import { Loading } from '../../components/Loading.tsx'
import { Offline } from '../../components/Offline.tsx'
import { ProposalCard } from './ProposalCard.tsx'

const ANOTHER_SUGGEST_RUNNING = 'Another fix is being suggested.'

function canSuggest(finding: FindingDetail, corrections: Correction[] | undefined): boolean {
  return (
    finding.status !== 'dismissed' &&
    corrections !== undefined &&
    !corrections.some((correction) => correction.status === 'pending')
  )
}

export function CorrectionsPanel({ finding }: { finding: FindingDetail }) {
  const headingId = useId()
  const corrections = useCorrections(finding.id)
  const suggest = useSuggestCorrection(finding.id)
  const latest = useLatestSuggest(finding.id)
  const ownRunning = latest?.status === 'pending'
  const otherRunning = useAnySuggestRunning() && !ownRunning

  return (
    <Card className="p-6">
      <section aria-labelledby={headingId} className="space-y-4">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <h3 id={headingId} className="font-semibold text-ink">
            Suggested fixes
          </h3>
          {canSuggest(finding, corrections.data) && (
            <Button disabled={ownRunning || otherRunning} onClick={suggest}>
              <SparkIcon className="size-4 text-accent" />
              Suggest fix
            </Button>
          )}
        </header>
        {otherRunning && <p className="text-sm text-ink-muted">{ANOTHER_SUGGEST_RUNNING}</p>}
        {latest !== undefined && <SuggestResult state={latest} />}
        {corrections.isPending &&
          (corrections.fetchStatus === 'paused' ? (
            <Offline subject="Suggested fixes" />
          ) : (
            <Loading label="Loading suggested fixes…" />
          ))}
        {corrections.isError && (
          <ErrorState error={corrections.error} onRetry={() => void corrections.refetch()} />
        )}
        {corrections.data !== undefined && <CorrectionList corrections={corrections.data} />}
      </section>
    </Card>
  )
}

function SuggestResult({ state }: { state: SuggestState }) {
  if (state.status === 'pending') {
    return state.isPaused ? (
      <Callout tone="offline" role="status">
        <p>You are offline. The fix will be requested when the connection is back.</p>
      </Callout>
    ) : (
      <p role="status" className="text-sm text-ink-muted">
        Asking the AI for a fix. This can take a few minutes.
      </p>
    )
  }
  if (state.status === 'error' && state.error !== null) {
    return (
      <Callout tone="danger" role="alert">
        <p>{state.error.message}</p>
      </Callout>
    )
  }
  return state.data === undefined ? null : <NoFixReason outcome={state.data} />
}

function NoFixReason({ outcome }: { outcome: SuggestOutcome }) {
  if (outcome.proposal !== null || outcome.reason === null) {
    return null
  }
  return (
    <Callout tone="caution" role="status">
      <p>
        <strong className="font-semibold">No fix suggested:</strong> {outcome.reason}
      </p>
    </Callout>
  )
}

function CorrectionList({ corrections }: { corrections: Correction[] }) {
  if (corrections.length === 0) {
    return <p className="text-sm text-ink-muted">No fixes suggested yet.</p>
  }
  return (
    <div className="space-y-4">
      {corrections.map((correction) => (
        <ProposalCard key={correction.id} correction={correction} />
      ))}
    </div>
  )
}
