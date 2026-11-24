import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'
import { FAST_POLL_MS } from './queue.ts'

// One datatype as it is stored in the tool note (docs/spec.md, Datatypes). A new one has no id yet.
export interface DatatypeDraft {
  id?: string
  name: string
  pattern: string
  description: string
  ignoreCase: boolean
  // False lets the pattern match any part of a value.
  wholeValue: boolean
  examples: string[]
  counterExamples: string[]
}

// As GET /api/datatypes lists it: the facts are null and broken says why when it fails its checks.
export interface Datatype extends DatatypeDraft {
  id: string
  states: number | null
  maxRepeat: number | null
  shortest: number | null
  broken: string | null
}

interface DatatypesDoc {
  datatypes: Datatype[]
  // The datatypes of a save the auto-commit has not written yet.
  saving: boolean
  // The section cannot be read.
  error?: string
}

export interface CheckRequest {
  pattern: string
  ignoreCase: boolean
  wholeValue: boolean
  examples: string[]
  counterExamples: string[]
  // The Try box's lines, never saved.
  samples: string[]
}

// How one value compares to the pattern. Holds no character of the value.
export interface FormatResult {
  distance: number | null
  score: number
  firstEdit: number | null
  expected: string | null
  tooLong: boolean
}

export interface PatternProblem {
  message: string
  // 1-based, null when no column fits.
  column: number | null
}

export interface CheckFacts {
  states: number
  maxRepeat: number
  shortest: number
  examples: FormatResult[]
  counterExamples: FormatResult[]
  samples: FormatResult[]
}

export type CheckAnswer = CheckFacts | { error: PatternProblem }

export const datatypesQueryKey = ['datatypes'] as const

// Followed closely while a save waits for its commit, so the Saving chip goes when it is written.
export function useDatatypes(enabled = true) {
  return useQuery({
    enabled,
    queryKey: datatypesQueryKey,
    queryFn: () => apiFetch<DatatypesDoc>('/api/datatypes'),
    refetchInterval: (query) => (query.state.data?.saving ? FAST_POLL_MS : false),
  })
}

// A datatype as a save sends it: wholeValue only when false, as a missing one means true.
export type StoredDatatype = Omit<DatatypeDraft, 'wholeValue'> & { wholeValue?: false }

// One change of the datatypes, known by id (docs/spec.md, setToolNoteSection). A new one has
// no id: the backend gives it one when it queues the change.
export type DatatypeChange =
  | { op: 'addDatatype'; datatype: StoredDatatype }
  | { op: 'setDatatype'; datatype: StoredDatatype }
  | { op: 'removeDatatype'; id: string }

// Queued as a UI edit of the tool note, which the backend starts to commit at once.
export function saveDatatypes(changes: DatatypeChange[]): Promise<unknown> {
  return apiFetch('/api/datatypes', { method: 'PUT', body: { changes } })
}

// What is checked can be a secret being typed, so this never goes through the query cache.
export function checkPattern(request: CheckRequest): Promise<CheckAnswer> {
  return apiFetch<CheckAnswer>('/api/datatypes/check', { method: 'POST', body: request })
}

