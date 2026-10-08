import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { createQueryClient } from './api/queryClient.ts'
import { ConfigError } from './components/ConfigError.tsx'
import { readCompanyId } from './config.ts'
import { routes } from './routes.tsx'

function ConfiguredApp() {
  const [queryClient] = useState(createQueryClient)
  const [router] = useState(() => createBrowserRouter(routes))
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}

function App() {
  if (readCompanyId() === null) {
    return <ConfigError />
  }
  return <ConfiguredApp />
}

export default App
