import { useSearchParams } from 'react-router'
import { filtersToSearchParams, useFindings, type FindingFilters } from '../../api/findings.ts'
import { Button } from '../../components/Button.tsx'
import { EmptyState } from '../../components/EmptyState.tsx'
import { ErrorState } from '../../components/ErrorState.tsx'
import { Loading } from '../../components/Loading.tsx'
import { Offline } from '../../components/Offline.tsx'
import { PageHeader } from '../../components/PageHeader.tsx'
import { RefreshError } from '../../components/RefreshError.tsx'
import { Warning } from '../../components/Warning.tsx'
import { filtersFromSearchParams, isInvertedRange } from './filterParams.ts'
import { FindingFiltersForm } from './FindingFiltersForm.tsx'
import { FindingsTable } from './FindingsTable.tsx'

export function FindingsListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = filtersFromSearchParams(searchParams)
  const applyFilters = (next: FindingFilters) =>
    setSearchParams(filtersToSearchParams(next), { replace: true })
  const clearFilters = () => applyFilters({})
  const clearDates = () =>
    applyFilters({ ...filters, occurredFrom: undefined, occurredTo: undefined })

  return (
    <section className="animate-rise space-y-6">
      <PageHeader
        title="Findings"
        description="Anomalies the rules found in attendance, access and payroll data. Open a finding to see its evidence and explanation."
      />
      <FindingFiltersForm filters={filters} onChange={applyFilters} />
      {isInvertedRange(filters) ? (
        <InvertedRange onClear={clearDates} />
      ) : (
        <FindingsResults filters={filters} onClear={clearFilters} />
      )}
    </section>
  )
}

function FindingsResults({ filters, onClear }: { filters: FindingFilters; onClear: () => void }) {
  const findings = useFindings(filters)

  if (findings.data === undefined) {
    return <FindingsPlaceholder findings={findings} />
  }

  const items = findings.data.pages.flatMap((page) => page.items)
  return (
    <div className="space-y-4">
      <ResultsStatus findings={findings} />
      {findings.isRefetchError && (
        <RefreshError
          subject="findings"
          error={findings.error}
          isRetrying={findings.isFetching}
          onRetry={() => void findings.refetch()}
        />
      )}
      {items.length === 0 ? (
        <NoFindings filtered={Object.keys(filters).length > 0} onClear={onClear} />
      ) : (
        <FindingsTable findings={items} />
      )}
      <LoadMore findings={findings} />
    </div>
  )
}

function ResultsStatus({ findings }: { findings: ReturnType<typeof useFindings> }) {
  if (findings.fetchStatus === 'paused') {
    return <Offline subject="Findings" />
  }
  if (findings.isPlaceholderData) {
    return (
      <p
        role="status"
        className="inline-flex items-center gap-2 rounded-full bg-accent-soft px-3 py-1 text-xs font-medium text-accent-ink"
      >
        <span aria-hidden="true" className="size-1.5 animate-pulse rounded-full bg-accent" />
        Updating findings…
      </p>
    )
  }
  return null
}

function LoadMore({ findings }: { findings: ReturnType<typeof useFindings> }) {
  if (findings.isFetchNextPageError) {
    return <ErrorState error={findings.error} onRetry={() => void findings.fetchNextPage()} />
  }
  if (!findings.hasNextPage || findings.isRefetchError || findings.isPlaceholderData) {
    return null
  }
  return (
    <div className="flex justify-center">
      <Button disabled={findings.isFetchingNextPage} onClick={() => void findings.fetchNextPage()}>
        {findings.isFetchingNextPage ? 'Loading…' : 'Load more'}
      </Button>
    </div>
  )
}

function FindingsPlaceholder({ findings }: { findings: ReturnType<typeof useFindings> }) {
  if (findings.isError) {
    return <ErrorState error={findings.error} onRetry={() => void findings.refetch()} />
  }
  if (findings.fetchStatus === 'paused') {
    return <Offline subject="Findings" />
  }
  return <Loading label="Loading findings…" />
}

function InvertedRange({ onClear }: { onClear: () => void }) {
  return (
    <Warning>
      <p className="flex-1">Occurred from must be on or before Occurred to.</p>
      <Button onClick={onClear}>Clear dates</Button>
    </Warning>
  )
}

function NoFindings({ filtered, onClear }: { filtered: boolean; onClear: () => void }) {
  if (!filtered) {
    return <EmptyState title="No findings yet." />
  }
  return (
    <EmptyState
      title="No findings match these filters."
      action={<Button onClick={onClear}>Clear filters</Button>}
    />
  )
}
