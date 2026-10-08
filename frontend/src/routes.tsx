import { Navigate, type RouteObject } from 'react-router'
import { Layout } from './components/Layout.tsx'
import { NotFoundPage } from './components/NotFoundPage.tsx'
import { RouteErrorPage } from './components/RouteErrorPage.tsx'
import { FindingDetailPage } from './features/findings/FindingDetailPage.tsx'
import { FindingsListPage } from './features/findings/FindingsListPage.tsx'

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <Navigate to="/findings" replace /> },
      { path: 'findings', element: <FindingsListPage /> },
      { path: 'findings/:id', element: <FindingDetailPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
