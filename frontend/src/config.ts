const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function readCompanyId(): string | null {
  const companyId = import.meta.env.VITE_COMPANY_ID
  if (companyId === undefined || !UUID_PATTERN.test(companyId)) {
    return null
  }
  return companyId
}
