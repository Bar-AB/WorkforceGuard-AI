import { Badge, type BadgeTone } from '../../components/Badge.tsx'

const SEVERITY_TONES: Partial<Record<string, BadgeTone>> = {
  low: 'low',
  medium: 'medium',
  high: 'high',
}

const STATUS_TONES: Partial<Record<string, BadgeTone>> = { open: 'accent' }

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={SEVERITY_TONES[severity] ?? 'neutral'}>{severity}</Badge>
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONES[status] ?? 'neutral'}>{status}</Badge>
}
