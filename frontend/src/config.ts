const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function uuidOrNull(value: string | undefined): string | null {
  return value === undefined || !UUID_PATTERN.test(value) ? null : value
}

export function readCompanyId(): string | null {
  return uuidOrNull(import.meta.env.VITE_COMPANY_ID)
}

export function readUserId(): string | null {
  return uuidOrNull(import.meta.env.VITE_USER_ID)
}
