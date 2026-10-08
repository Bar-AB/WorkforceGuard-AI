function evidenceText(value: unknown): string {
  return typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)
}

export function EvidenceList({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence)
  if (entries.length === 0) {
    return <p className="text-sm text-ink-muted">No evidence recorded.</p>
  }
  return (
    <dl className="divide-y divide-line text-sm">
      {entries.map(([key, value]) => (
        <div
          key={key}
          className="grid grid-cols-[minmax(0,11rem)_1fr] gap-4 py-2.5 first:pt-0 last:pb-0"
        >
          <dt className="font-mono text-xs leading-5 break-words text-ink-muted">{key}</dt>
          <dd className="font-mono leading-5 break-all text-ink">{evidenceText(value)}</dd>
        </div>
      ))}
    </dl>
  )
}
