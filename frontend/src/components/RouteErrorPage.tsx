import { Callout } from './Callout.tsx'
import { AppShell } from './Layout.tsx'

export function RouteErrorPage() {
  return (
    <AppShell>
      <Callout tone="danger" role="alert">
        <div>
          <p className="font-medium">Something went wrong while showing this page.</p>
          <p className="mt-1">Reload the page. If it keeps happening, check the browser console.</p>
        </div>
      </Callout>
    </AppShell>
  )
}
