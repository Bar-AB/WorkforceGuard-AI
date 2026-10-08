import { shortId } from '../format.ts'

export function ShortId({ value }: { value: string }) {
  return (
    <span title={value} className="font-mono">
      <span aria-hidden="true">{shortId(value)}</span>
      <span className="sr-only">{value}</span>
    </span>
  )
}
