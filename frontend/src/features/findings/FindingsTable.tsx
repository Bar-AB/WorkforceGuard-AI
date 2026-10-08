import { Link, useLocation } from 'react-router'
import { ruleLabel, type FindingSummary } from '../../api/findings.ts'
import { Card } from '../../components/Card.tsx'
import { ChevronRightIcon, ListIcon } from '../../components/icons.tsx'
import { formatDate } from '../../format.ts'
import { SeverityBadge, StatusBadge } from './FindingBadges.tsx'
import { listLocationState } from './listLocation.ts'

const HEADERS = ['Occurred on', 'Type', 'Severity', 'Status', 'Summary']

const STAGGER_MS = 30
const MAX_STAGGER_STEPS = 10

function rowDelay(index: number): string {
  return `${Math.min(index, MAX_STAGGER_STEPS) * STAGGER_MS}ms`
}

function findingCount(count: number): string {
  return count === 1 ? 'Showing 1 finding' : `Showing ${count} findings`
}

export function FindingsTable({ findings }: { findings: FindingSummary[] }) {
  const { search } = useLocation()
  return (
    <Card className="overflow-hidden">
      <p className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm text-ink-muted">
        <ListIcon className="size-4" />
        {findingCount(findings.length)}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <caption className="sr-only">Findings</caption>
          <thead className="bg-surface-muted text-xs tracking-wide text-ink-muted uppercase">
            <tr>
              {HEADERS.map((header) => (
                <th key={header} scope="col" className="px-4 py-3 font-semibold">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {findings.map((finding, index) => (
              <tr
                key={finding.id}
                style={{ animationDelay: rowDelay(index) }}
                className="group relative animate-rise border-t border-line transition-colors focus-within:bg-accent-soft/60 hover:bg-accent-soft/60"
              >
                <td className="px-4 py-3 whitespace-nowrap text-ink-muted">
                  <time dateTime={finding.occurred_on}>{formatDate(finding.occurred_on)}</time>
                </td>
                <td className="px-4 py-3 font-medium whitespace-nowrap">
                  {ruleLabel(finding.rule_id)}
                </td>
                <td className="px-4 py-3">
                  <SeverityBadge severity={finding.severity} />
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={finding.status} />
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <Link
                      to={`/findings/${finding.id}`}
                      state={listLocationState(search)}
                      className="text-ink after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-accent"
                    >
                      {finding.summary}
                    </Link>
                    <ChevronRightIcon className="size-4 shrink-0 text-ink-muted transition group-hover:translate-x-0.5 group-hover:text-accent" />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
