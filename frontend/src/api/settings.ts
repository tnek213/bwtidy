import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'

// How dates and times are shown (docs/spec.md, Settings), the values of GET /api/settings/display.
export type DateFormat = 'system' | 'iso' | 'europe' | 'europe-dots' | 'usa'

// The display settings of the account (docs/spec.md, Settings), kept by the backend so they follow it to every browser.
export interface Display {
  dateFormat: DateFormat
  hideToolNote: boolean
  // The N of the grid's Rare fields column, 0 turns it off.
  rareFieldsMax: number
}

export const displayQueryKey = ['settings', 'display'] as const

export function useDisplay() {
  return useQuery({
    queryKey: displayQueryKey,
    queryFn: () => apiFetch<Display>('/api/settings/display'),
  })
}

// Only the fields that changed, so changing one never resets the other.
export function saveDisplay(display: Partial<Display>): Promise<Display> {
  return apiFetch<Display>('/api/settings/display', { method: 'PUT', body: display })
}

