import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'

interface Health {
  status: string
  version: string
}

// The version and the commit the running image was built from, which do not change while the page is open.
export function useVersion() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => apiFetch<Health>('/api/health'),
    staleTime: Infinity,
  }).data?.version
}

