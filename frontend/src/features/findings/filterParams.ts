import { RULE_LABELS, SEVERITIES, type FindingFilters, type Severity } from '../../api/findings.ts'

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

export function toSeverity(value: string | null): Severity | undefined {
  return SEVERITIES.find((severity) => severity === value)
}

export function toRuleId(value: string | null): string | undefined {
  return value !== null && Object.hasOwn(RULE_LABELS, value) ? value : undefined
}

function isCalendarDate(value: string): boolean {
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  return (
    year >= 1 &&
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  )
}

export function toIsoDate(value: string | null): string | undefined {
  return value !== null && ISO_DATE.test(value) && isCalendarDate(value) ? value : undefined
}

export function isInvertedRange({ occurredFrom, occurredTo }: FindingFilters): boolean {
  return occurredFrom !== undefined && occurredTo !== undefined && occurredFrom > occurredTo
}

function withoutUnset(filters: FindingFilters): FindingFilters {
  return Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== undefined))
}

export function filtersFromSearchParams(params: URLSearchParams): FindingFilters {
  return withoutUnset({
    ruleId: toRuleId(params.get('rule_id')),
    severity: toSeverity(params.get('severity')),
    occurredFrom: toIsoDate(params.get('occurred_from')),
    occurredTo: toIsoDate(params.get('occurred_to')),
  })
}
