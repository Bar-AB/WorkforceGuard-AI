import { SearchOffIcon } from './icons.tsx'

export function NotFoundState({ title, description }: { title: string; description: string }) {
  return (
    <div className="flex animate-rise flex-col items-center rounded-2xl border border-line bg-surface px-6 py-14 text-center shadow-sm shadow-black/5">
      <span className="grid size-12 place-items-center rounded-full bg-neutral-soft text-neutral-ink">
        <SearchOffIcon className="size-6" />
      </span>
      <h2 className="mt-4 text-lg font-semibold text-ink">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-ink-muted">{description}</p>
    </div>
  )
}
