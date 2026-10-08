interface ListLocationState {
  listSearch: string
}

export function listLocationState(search: string): ListLocationState {
  return { listSearch: search }
}

export function findingsListPath(state: unknown): string {
  const search =
    typeof state === 'object' &&
    state !== null &&
    'listSearch' in state &&
    typeof state.listSearch === 'string' &&
    state.listSearch.startsWith('?')
      ? state.listSearch
      : ''
  return `/findings${search}`
}
