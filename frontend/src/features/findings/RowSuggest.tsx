import { useCallback, useRef, type RefCallback, type RefObject } from 'react'
import {
  useAnySuggestRunning,
  useLatestSuggest,
  useSuggestCorrection,
  type SuggestState,
} from '../../api/corrections.ts'
import type { FindingRow } from '../../api/findings.ts'
import { Badge } from '../../components/Badge.tsx'
import { Button } from '../../components/Button.tsx'
import { SparkIcon } from '../../components/icons.tsx'

const ANOTHER_SUGGEST_RUNNING = 'Another fix is being suggested.'

function useFocusHandOver(target: RefObject<HTMLElement | null>): RefCallback<HTMLElement> {
  return useCallback(
    (node: HTMLElement | null) => {
      if (node === null) {
        return
      }
      return () => {
        if (node.contains(document.activeElement)) {
          target.current?.focus()
        }
      }
    },
    [target],
  )
}

function canSuggest(finding: FindingRow): boolean {
  return finding.status !== 'dismissed' && !finding.has_pending_correction
}

export function RowSuggest({ finding }: { finding: FindingRow }) {
  const suggest = useSuggestCorrection(finding.id)
  const latest = useLatestSuggest(finding.id)
  const ownRunning = latest?.status === 'pending'
  const otherRunning = useAnySuggestRunning() && !ownRunning
  const wrapper = useRef<HTMLDivElement>(null)
  const handOverFocus = useFocusHandOver(wrapper)

  return (
    <div
      ref={wrapper}
      tabIndex={-1}
      className="relative z-10 flex max-w-64 flex-col items-start gap-1.5 outline-none"
    >
      {canSuggest(finding) && (
        <span ref={handOverFocus}>
          <Button disabled={ownRunning || otherRunning} onClick={suggest}>
            <SparkIcon className="size-4 text-accent" />
            Suggest fix
          </Button>
        </span>
      )}
      <div role="status" className="text-xs text-ink-muted">
        {finding.has_pending_correction ? (
          <Badge tone="accent">Fix suggested</Badge>
        ) : (
          <SuggestNote state={latest} otherRunning={otherRunning} />
        )}
      </div>
      {latest?.status === 'error' && latest.error !== null && (
        <p role="alert" className="text-xs text-danger-ink">
          {latest.error.message}
        </p>
      )}
    </div>
  )
}

function SuggestNote({
  state,
  otherRunning,
}: {
  state: SuggestState | undefined
  otherRunning: boolean
}) {
  if (state?.status === 'pending') {
    return state.isPaused ? 'Offline. Will retry.' : 'Asking the AI…'
  }
  if (otherRunning) {
    return ANOTHER_SUGGEST_RUNNING
  }
  const outcome = state?.data
  if (outcome === undefined || outcome.proposal !== null || outcome.reason === null) {
    return null
  }
  return `No fix: ${outcome.reason}`
}
