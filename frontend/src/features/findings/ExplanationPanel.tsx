import { Badge, type BadgeTone } from '../../components/Badge.tsx'

const SOURCE_TONES: Partial<Record<string, BadgeTone>> = { llm: 'accent', fallback: 'caution' }

export function ExplanationPanel({
  explanation,
  source,
  promptVersion,
}: {
  explanation: string | null
  source: string | null
  promptVersion: string | null
}) {
  if (explanation === null) {
    return <p className="text-sm text-ink-muted">Explanation not ready yet.</p>
  }
  return (
    <div className="space-y-4">
      <p className="leading-relaxed whitespace-pre-wrap text-ink">{explanation}</p>
      <div className="flex flex-wrap gap-2">
        <Badge tone={SOURCE_TONES[source ?? ''] ?? 'neutral'}>Source: {source}</Badge>
        <Badge tone="neutral">Prompt version: {promptVersion}</Badge>
      </div>
    </div>
  )
}
