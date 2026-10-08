import { Callout } from './Callout.tsx'

export function ConfigError() {
  return (
    <main className="mx-auto max-w-xl px-6 py-16">
      <h1 className="text-xl font-semibold text-ink">Configuration error</h1>
      <div className="mt-4">
        <Callout tone="caution">
          <p>
            Set <code className="font-mono font-semibold">VITE_COMPANY_ID</code> to a company UUID
            in <code className="font-mono font-semibold">frontend/.env.local</code>, then restart
            the dev server.
          </p>
        </Callout>
      </div>
    </main>
  )
}
