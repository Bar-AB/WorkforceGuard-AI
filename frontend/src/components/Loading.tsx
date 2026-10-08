const SKELETON_WIDTHS = ['w-11/12', 'w-9/12', 'w-10/12', 'w-7/12']

export function Loading({ label }: { label: string }) {
  return (
    <div role="status" className="animate-rise rounded-2xl border border-line bg-surface p-5">
      <p className="text-sm text-ink-muted">{label}</p>
      <div aria-hidden="true" className="mt-4 space-y-3">
        {SKELETON_WIDTHS.map((width) => (
          <div key={width} className={`h-4 animate-shimmer rounded-md bg-shimmer ${width}`} />
        ))}
      </div>
    </div>
  )
}
