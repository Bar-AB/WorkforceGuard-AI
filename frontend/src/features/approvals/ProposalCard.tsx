import { useId, useRef } from 'react'
import { useDecideCorrection, type Correction, type Verdict } from '../../api/corrections.ts'
import { Badge, type BadgeTone } from '../../components/Badge.tsx'
import { Button } from '../../components/Button.tsx'
import { Callout } from '../../components/Callout.tsx'
import { ShortId } from '../../components/ShortId.tsx'
import { readUserId } from '../../config.ts'
import { formatDateTime, formatDateTimeSeconds, hasSeconds } from '../../format.ts'

const TABLE_LABELS: Readonly<Record<string, string>> = {
  shifts: 'Shift',
  attendance_events: 'Attendance event',
  access_logs: 'Access log',
  payroll_runs: 'Payroll run',
}

type BadgeSpec = { tone: BadgeTone; label: string }

const STATUS_BADGES: Readonly<Record<string, BadgeSpec>> = {
  pending: { tone: 'caution', label: 'Pending' },
  approved: { tone: 'accent', label: 'Approved' },
  rejected: { tone: 'neutral', label: 'Rejected' },
}

const CHANGE_PHRASES: Readonly<Record<string, string>> = {
  pending: 'changes to',
  approved: 'changed to',
  rejected: 'would have changed to',
}

function judgeBadge({ judge_supported, judged_by }: Correction): BadgeSpec {
  if (judge_supported === true) {
    return { tone: 'accent', label: 'Judge: looks right' }
  }
  if (judge_supported === false) {
    return { tone: 'high', label: 'Judge: looks wrong' }
  }
  return { tone: 'neutral', label: judged_by == null ? 'Not judged' : 'Judge unavailable' }
}

const DRIFT_WARNING =
  'This row changed after the fix was suggested. Approving will be refused. Reject it and ask for a new suggestion.'

const TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T/

function isTimestamp(value: unknown): value is string {
  return typeof value === 'string' && TIMESTAMP_PATTERN.test(value)
}

function formatValue(value: unknown, withSeconds: boolean): string {
  if (isTimestamp(value)) {
    return withSeconds ? formatDateTimeSeconds(value) : formatDateTime(value)
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function sameValue(left: unknown, right: unknown): boolean {
  return (
    JSON.stringify(left) === JSON.stringify(right) ||
    (isTimestamp(left) && isTimestamp(right) && Date.parse(left) === Date.parse(right))
  )
}

function rowDrifted({ status, before, current, patch }: Correction): boolean {
  if (status !== 'pending' || before == null || current === null) {
    return false
  }
  return Object.keys(patch).some(
    (column) => column in before && !sameValue(before[column], current[column]),
  )
}

function oldSnapshot(correction: Correction): {
  values: Record<string, unknown> | null | undefined
  missing: string
} {
  return correction.status === 'pending'
    ? { values: correction.current, missing: 'row no longer exists' }
    : { values: correction.before, missing: 'not recorded' }
}

function Placeholder({ children }: { children: string }) {
  return <span className="text-ink-muted italic">{children}</span>
}

function Value({
  value,
  withSeconds,
  className,
}: {
  value: unknown
  withSeconds: boolean
  className: string
}) {
  return value === null || value === undefined ? (
    <Placeholder>empty</Placeholder>
  ) : (
    <span className={className}>{formatValue(value, withSeconds)}</span>
  )
}

function ChangeRow({ correction, column }: { correction: Correction; column: string }) {
  const { values, missing } = oldSnapshot(correction)
  const recorded = values != null && column in values
  const old = values?.[column]
  const patched = correction.patch[column]
  const withSeconds = [old, patched].some((value) => isTimestamp(value) && hasSeconds(value))

  return (
    <li className="flex flex-wrap items-center gap-2 text-sm">
      <code className="rounded bg-accent-soft px-1.5 py-0.5 text-accent-ink">{column}</code>
      {recorded ? (
        <Value value={old} withSeconds={withSeconds} className="text-ink-muted line-through" />
      ) : (
        <Placeholder>{missing}</Placeholder>
      )}
      <span aria-hidden="true" className="text-ink-muted">
        →
      </span>
      <span className="sr-only">{CHANGE_PHRASES[correction.status] ?? 'changes to'}</span>
      <Value value={patched} withSeconds={withSeconds} className="font-medium text-ink" />
    </li>
  )
}

function JudgeVerdict({ correction }: { correction: Correction }) {
  const { tone, label } = judgeBadge(correction)
  const reason = correction.judge_reason
  return (
    <div role="group" aria-label="Judge verdict" className="space-y-1">
      <Badge tone={tone}>{label}</Badge>
      {typeof reason === 'string' && reason !== '' && (
        <p className="text-sm leading-relaxed break-words whitespace-pre-wrap text-ink-muted">
          {reason}
        </p>
      )}
    </div>
  )
}

export function ProposalCard({ correction }: { correction: Correction }) {
  const headingId = useId()
  const badge = STATUS_BADGES[correction.status] ?? {
    tone: 'neutral',
    label: correction.status,
  }

  return (
    <article
      aria-labelledby={headingId}
      className="space-y-4 rounded-xl border border-line bg-surface-muted p-4"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h4 id={headingId} className="flex items-center gap-2 text-sm font-semibold text-ink">
          {TABLE_LABELS[correction.target_table] ?? correction.target_table}
          <span className="font-normal text-ink-muted">
            <ShortId value={correction.target_id} />
          </span>
        </h4>
        <Badge tone={badge.tone}>{badge.label}</Badge>
      </header>
      <ul className="space-y-2">
        {Object.keys(correction.patch).map((column) => (
          <ChangeRow key={column} correction={correction} column={column} />
        ))}
      </ul>
      <p className="text-sm leading-relaxed break-words whitespace-pre-wrap text-ink">
        {correction.reason}
      </p>
      <JudgeVerdict correction={correction} />
      <p className="text-xs text-ink-muted">
        {`Proposed by ${correction.proposed_by} · ${formatDateTime(correction.created_at)}`}
      </p>
      {rowDrifted(correction) && (
        <Callout tone="caution">
          <p>{DRIFT_WARNING}</p>
        </Callout>
      )}
      {correction.status === 'pending' ? (
        <DecisionControls correction={correction} />
      ) : (
        correction.decided_at !== null && (
          <p className="text-xs text-ink-muted">{`Decided ${formatDateTime(correction.decided_at)}`}</p>
        )
      )}
    </article>
  )
}

function DecisionControls({ correction }: { correction: Correction }) {
  const decide = useDecideCorrection(correction.finding_id)
  const sending = useRef(false)
  const hintId = useId()
  const canDecide = readUserId() !== null
  const disabled = !canDecide || decide.isPending
  const describedBy = canDecide ? undefined : hintId

  function decideAs(verdict: Verdict) {
    if (sending.current) {
      return
    }
    sending.current = true
    decide.mutate(
      { correctionId: correction.id, verdict },
      {
        onSettled: () => {
          sending.current = false
        },
      },
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={disabled} describedBy={describedBy} onClick={() => decideAs('approve')}>
          Approve
        </Button>
        <Button disabled={disabled} describedBy={describedBy} onClick={() => decideAs('reject')}>
          Reject
        </Button>
        {!canDecide && (
          <p id={hintId} className="text-xs text-ink-muted">
            Set VITE_USER_ID to approve or reject.
          </p>
        )}
      </div>
      {decide.isPaused && (
        <Callout tone="offline" role="status">
          <p>You are offline. The decision will be sent when the connection is back.</p>
        </Callout>
      )}
      {decide.isError && (
        <Callout tone="danger" role="alert">
          <p>{decide.error.message}</p>
        </Callout>
      )}
    </div>
  )
}
