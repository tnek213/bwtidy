import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'

// GET /api/statistics (docs/spec.md, Statistics).
export interface StatisticsLimits {
  maxPatternLen: number
  maxRepeat: number
  maxStates: number
  maxDepth: number
  maxValueLen: number
}

interface DatatypeStatistics {
  id: string
  name: string
  states: number
  maxRepeat: number
  shortest: number
  valuesScored: number
  valuesTooLong: number
  slowestMs: number
  lastReloadMs: number
}

export interface StatisticsRow {
  itemId: string
  item: string
  column: string
  datatype: string
  states: number
  maxRepeat: number
  shortest: number
  length: number
  distance: number | null
  repeatsUsed: number[]
  steps: number
  ms: number
  tooLong: boolean
}

export interface Statistics {
  limits: StatisticsLimits
  reloads: number
  datatypes: DatatypeStatistics[]
  rows: StatisticsRow[]
  rowCount: number
}

const statisticsQueryKey = ['statistics']

export function useStatistics() {
  return useQuery({
    queryKey: statisticsQueryKey,
    queryFn: () => apiFetch<Statistics>('/api/statistics'),
  })
}
