import { screen, waitFor } from '@testing-library/react'

export function findStatus(text: string): Promise<HTMLElement> {
  return waitFor(() => {
    const status = screen.getAllByRole('status').find((s) => s.textContent?.includes(text))
    if (status === undefined) {
      throw new Error(`No status says "${text}"`)
    }
    return status
  })
}
