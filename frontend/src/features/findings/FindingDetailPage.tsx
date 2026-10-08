import type { ReactNode } from 'react'
import { useLocation, useParams } from 'react-router'
import { ApiError, isUnknownCompany } from '../../api/client.ts'
import { ruleLabel, useFinding, type FindingDetail } from '../../api/findings.ts'
import { BackLink } from '../../components/BackLink.tsx'
import { Card } from '../../components/Card.tsx'
import { ErrorState } from '../../components/ErrorState.tsx'
import { SparkIcon } from '../../components/icons.tsx'
import { Loading } from '../../components/Loading.tsx'
import { Offline } from '../../components/Offline.tsx'
import { NotFoundState } from '../../components/NotFoundState.tsx'
import { RefreshError } from '../../components/RefreshError.tsx'
import { ShortId } from '../../components/ShortId.tsx'
import { formatDate, formatDateTime } from '../../format.ts'
import { EvidenceList } from './EvidenceList.tsx'
import { ExplanationPanel } from './ExplanationPanel.tsx'
import { SeverityBadge, StatusBadge } from './FindingBadges.tsx'
import { findingsListPath } from './listLocation.ts'

function isMissingFinding(error: Error): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 422 || (error.status === 404 && !isUnknownCompany(error)))
  )
}

export function FindingDetailPage() {
  const { id = '' } = useParams()
  const { state } = useLocation()
  const finding = useFinding(id)

  return (
    <section className="animate-rise space-y-6">
      <BackLink to={findingsListPath(state)}>Back to findings</BackLink>
      {finding.fetchStatus === 'paused' ? (
        <Offline subject="The finding" />
      ) : (
        finding.isPending && <Loading label="Loading finding…" />
      )}
      {finding.isLoadingError &&
        (isMissingFinding(finding.error) ? (
          <NotFoundState
            title="Finding not found"
            description="It may have been removed, or the link is not complete."
          />
        ) : (
          <ErrorState error={finding.error} onRetry={() => void finding.refetch()} />
        ))}
      {finding.isRefetchError && (
        <RefreshError
          subject="finding"
          error={finding.error}
          isRetrying={finding.isFetching}
          onRetry={() => void finding.refetch()}
        />
      )}
      {finding.data !== undefined && <FindingView finding={finding.data} />}
    </section>
  )
}

function FindingView({ finding }: { finding: FindingDetail }) {
  return (
    <article className="space-y-6">
      <Card className="relative overflow-hidden p-6">
        <div
          aria-hidden="true"
          className="absolute inset-x-0 top-0 h-1 bg-linear-to-r from-indigo-500 via-violet-500 to-sky-400"
        />
        <h2 className="text-2xl font-semibold tracking-tight text-ink">
          {ruleLabel(finding.rule_id)}
        </h2>
        <p className="mt-2 text-ink-muted">{finding.summary}</p>
        <dl className="mt-6 grid gap-x-6 gap-y-4 border-t border-line pt-5 sm:grid-cols-3 lg:grid-cols-5">
          <Fact term="Severity">
            <SeverityBadge severity={finding.severity} />
          </Fact>
          <Fact term="Status">
            <StatusBadge status={finding.status} />
          </Fact>
          <Fact term="Occurred">
            <time dateTime={finding.occurred_on}>{formatDate(finding.occurred_on)}</time>
          </Fact>
          <Fact term="Employee">
            <ShortId value={finding.employee_id} />
          </Fact>
          <Fact term="Detected at">
            <time dateTime={finding.detected_at}>{formatDateTime(finding.detected_at)}</time>
          </Fact>
        </dl>
      </Card>
      <div className="grid items-start gap-6 lg:grid-cols-2">
        <Card className="p-6">
          <section className="space-y-4">
            <h3 className="font-semibold text-ink">Evidence</h3>
            <EvidenceList evidence={finding.evidence} />
          </section>
        </Card>
        <Card className="border-l-4 border-l-accent p-6">
          <section className="space-y-4">
            <h3 className="flex items-center gap-2 font-semibold text-ink">
              <SparkIcon className="size-5 text-accent" />
              Explanation
            </h3>
            <ExplanationPanel
              explanation={finding.explanation}
              source={finding.explanation_source}
              promptVersion={finding.explanation_prompt_version}
            />
          </section>
        </Card>
      </div>
    </article>
  )
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium tracking-wide text-ink-muted uppercase">{term}</dt>
      <dd className="mt-1.5 text-sm text-ink">{children}</dd>
    </div>
  )
}
