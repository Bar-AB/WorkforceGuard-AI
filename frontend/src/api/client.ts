import { readCompanyId } from '../config.ts'

const UNKNOWN_COMPANY = 'Unknown company.'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string, options?: ErrorOptions) {
    super(message, options)
    this.name = 'ApiError'
    this.status = status
  }
}

function detailOf(body: unknown): string | null {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    return typeof body.detail === 'string' && body.detail.trim() !== '' ? body.detail : null
  }
  return null
}

function fallbackMessage(status: number): string {
  if (status >= 500) {
    return `The API is unavailable (${status}). Check that the backend is running.`
  }
  return `Request failed (${status})`
}

async function errorFrom(response: Response): Promise<ApiError> {
  const body: unknown = await response.json().catch(() => null)
  const message = detailOf(body) ?? fallbackMessage(response.status)
  return new ApiError(response.status, message)
}

async function bodyOf(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch (error) {
    throw new ApiError(response.status, 'Unexpected response from the API.', { cause: error })
  }
}

function requireCompanyId(): string {
  const companyId = readCompanyId()
  if (companyId === null) {
    throw new Error('VITE_COMPANY_ID is missing or not a UUID. Set it in frontend/.env.local.')
  }
  return companyId
}

export function isUnknownCompany(error: Error): boolean {
  return error instanceof ApiError && error.status === 404 && error.message === UNKNOWN_COMPANY
}

export async function apiGet<T>(path: string, params?: URLSearchParams): Promise<T> {
  const query = params === undefined || params.size === 0 ? '' : `?${params.toString()}`
  const response = await fetch(`${path}${query}`, {
    headers: { 'X-Company-Id': requireCompanyId() },
  })
  if (!response.ok) {
    throw await errorFrom(response)
  }
  return (await bodyOf(response)) as T
}
