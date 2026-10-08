import { RULE_LABELS, SEVERITIES, type FindingFilters } from '../../api/findings.ts'
import { Button } from '../../components/Button.tsx'
import { Card } from '../../components/Card.tsx'
import { toIsoDate, toRuleId, toSeverity } from './filterParams.ts'

const LATEST_DATE = '9999-12-31'

const FIELD_CLASSES =
  'mt-1.5 block w-full rounded-lg border border-field-line bg-surface px-3 py-2 text-sm text-ink shadow-sm shadow-black/5 transition hover:border-accent/40 focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent'

const LABEL_CLASSES = 'block text-xs font-medium text-ink-muted'

export function FindingFiltersForm({
  filters,
  onChange,
}: {
  filters: FindingFilters
  onChange: (next: FindingFilters) => void
}) {
  const update = (patch: FindingFilters) => onChange({ ...filters, ...patch })

  return (
    <Card className="p-4 sm:p-5">
      <form
        aria-label="Filter findings"
        className="grid items-end gap-4 sm:grid-cols-2 lg:grid-cols-[repeat(4,minmax(0,1fr))_auto]"
        onSubmit={(event) => event.preventDefault()}
      >
        <label className={LABEL_CLASSES}>
          Type
          <select
            className={FIELD_CLASSES}
            value={filters.ruleId ?? ''}
            onChange={(event) => update({ ruleId: toRuleId(event.target.value) })}
          >
            <option value="">All types</option>
            {Object.entries(RULE_LABELS).map(([ruleId, label]) => (
              <option key={ruleId} value={ruleId}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className={LABEL_CLASSES}>
          Severity
          <select
            className={FIELD_CLASSES}
            value={filters.severity ?? ''}
            onChange={(event) => update({ severity: toSeverity(event.target.value) })}
          >
            <option value="">All severities</option>
            {SEVERITIES.map((severity) => (
              <option key={severity} value={severity}>
                {severity}
              </option>
            ))}
          </select>
        </label>
        <label className={LABEL_CLASSES}>
          Occurred from
          <input
            type="date"
            className={FIELD_CLASSES}
            value={filters.occurredFrom ?? ''}
            max={filters.occurredTo ?? LATEST_DATE}
            onChange={(event) => update({ occurredFrom: toIsoDate(event.target.value) })}
          />
        </label>
        <label className={LABEL_CLASSES}>
          Occurred to
          <input
            type="date"
            className={FIELD_CLASSES}
            value={filters.occurredTo ?? ''}
            min={filters.occurredFrom}
            max={LATEST_DATE}
            onChange={(event) => update({ occurredTo: toIsoDate(event.target.value) })}
          />
        </label>
        <Button onClick={() => onChange({})}>Clear filters</Button>
      </form>
    </Card>
  )
}
