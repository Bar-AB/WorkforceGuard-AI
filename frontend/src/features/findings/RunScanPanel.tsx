import { ruleLabel } from '../../api/findings.ts'
import { useLatestScan, useRunScan, type ScanResult, type ScanState } from '../../api/scans.ts'
import { Button } from '../../components/Button.tsx'
import { Callout } from '../../components/Callout.tsx'
import { SparkIcon } from '../../components/icons.tsx'
import { countOf } from '../../format.ts'

const FALLBACK_REASON_LABELS: Readonly<Record<string, string>> = {
  llm_unavailable: 'AI model unavailable',
  llm_misconfigured: 'AI model misconfigured',
  invalid_output: 'invalid AI output',
}

const OUTAGE_REASONS: ReadonlySet<string> = new Set(['llm_unavailable', 'llm_misconfigured'])

function buttonLabel(state: ScanState | undefined): string {
  if (state?.status !== 'pending') {
    return 'Run scan'
  }
  return state.isPaused ? 'Waiting for connection…' : 'Scanning…'
}

function usedFallbacks(byReason: Record<string, number>): [string, number][] {
  return Object.entries(byReason).filter(([, count]) => count > 0)
}

function needsAttention(result: ScanResult): boolean {
  return (
    result.rules_skipped.length > 0 ||
    result.explanations_deferred > 0 ||
    usedFallbacks(result.explanations_fallback_by_reason).some(([reason]) =>
      OUTAGE_REASONS.has(reason),
    )
  )
}

function fallbackReasons(byReason: Record<string, number>): string {
  return usedFallbacks(byReason)
    .map(([reason, count]) => `${FALLBACK_REASON_LABELS[reason] ?? reason}: ${count}`)
    .join(', ')
}

export function RunScanPanel() {
  const runScan = useRunScan()
  const latest = useLatestScan()

  return (
    <div className="flex flex-col items-start gap-3 sm:items-end">
      <Button disabled={latest?.status === 'pending'} onClick={runScan}>
        <SparkIcon className="size-4 text-accent" />
        {buttonLabel(latest)}
      </Button>
      {latest !== undefined && <ScanOutcome state={latest} />}
    </div>
  )
}

function ScanOutcome({ state }: { state: ScanState }) {
  if (state.status === 'pending') {
    return state.isPaused ? (
      <Callout tone="offline" role="status">
        <p>You are offline. The scan will start when the connection is back.</p>
      </Callout>
    ) : (
      <p role="status" className="text-sm text-ink-muted">
        Scanning. This can take a few minutes.
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
  return state.data === undefined ? null : <ScanSummary result={state.data} />
}

function ScanSummary({ result }: { result: ScanResult }) {
  return (
    <Callout tone={needsAttention(result) ? 'caution' : 'info'} role="status">
      <div className="space-y-1">
        <p>
          Scan finished: {countOf(result.findings_created, 'new finding')} (
          {result.findings_detected} detected).
        </p>
        {result.rules_skipped.map((skipped) => (
          <p key={skipped.rule_id}>
            Skipped {ruleLabel(skipped.rule_id)}: {skipped.reason}
          </p>
        ))}
        {result.explanations_fallback > 0 && (
          <p>
            Explanations the AI could not write: {result.explanations_fallback} (
            {fallbackReasons(result.explanations_fallback_by_reason)}).
          </p>
        )}
        {result.explanations_deferred > 0 && (
          <p>Explanations waiting for the next scan: {result.explanations_deferred}.</p>
        )}
      </div>
    </Callout>
  )
}
