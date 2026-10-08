export function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <header>
      <h2 className="text-2xl font-semibold tracking-tight text-ink">{title}</h2>
      <p className="mt-1 max-w-2xl text-sm text-ink-muted">{description}</p>
    </header>
  )
}
