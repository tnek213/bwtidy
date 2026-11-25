import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'
import { FAST_POLL_MS } from './queue.ts'
import type { FieldType } from './operations.ts'

// The template JSON of the tool note (docs/spec.md, Templates). A fieldPattern stands for the
// custom fields whose name fits its pattern, where * matches any characters. Its fieldType is
// text or hidden, the type of a field New item makes for it.
export type TemplateCoordinate = (
  | { kind: 'path'; path: string }
  | { kind: 'field'; name: string; fieldType: FieldType }
  | { kind: 'fieldPattern'; pattern: string; fieldType: FieldType }
) & {
  // A datatype's id (docs/spec.md, Datatypes). Left out without one, also when stored.
  datatype?: string
  // Not needed for a match (docs/spec.md, Templates). Left out when false, also when stored.
  optional?: boolean
}

export interface Template {
  name: string
  // Left out when empty, also when stored (docs/spec.md, Templates).
  description?: string
  type: number
  coordinates: TemplateCoordinate[]
}

export interface TemplatesDoc {
  templates: Template[]
  // The tool note's revisionDate, when the backend sends it along.
  revisionDate?: string
  // The tool note's name now, which is bwtidy unless someone renamed it.
  toolNoteName?: string
  // The name the tool note should have: bwtidy, or a name the user kept with Keep this name.
  toolNoteExpectedName?: string
  // The templates of a save the auto-commit has not written yet.
  saving?: boolean
  // Each coordinate whose datatype is unknown or broken, which counts as none.
  warnings?: string[]
}

// How the value of a coordinate with a datatype matches it (docs/spec.md, Datatypes).
// score has two decimals. distance, firstEdit and expected are null without a value to
// check. firstEdit counts from 0, and expected holds text from the pattern only.
export interface FormatScore {
  column: string
  datatype: string
  score: number
  distance: number | null
  firstEdit: number | null
  expected: string | null
  tooLong: boolean
}

// filled and total count the template's coordinates plus its item type, and one
// term per datatype, so a full match has typeMatches and filled === total.
export interface TemplateScore {
  template: string
  score: number
  typeMatches: boolean
  filled: number
  total: number
  // The template's coordinates the item has no value for, as grid column ids.
  missing: string[]
  // One per coordinate with a datatype, left out when the template has none.
  formats?: FormatScore[]
}

export interface TemplateMatch {
  match: string | null
  scores: TemplateScore[]
}

export type TemplateMatches = Record<string, TemplateMatch | undefined>

export const templatesQueryKey = ['templates'] as const
// Under the grid key, so every grid reload after a write refreshes the matches too.
const templateMatchesQueryKey = ['grid', 'template-matches'] as const

// Followed closely while a save waits for its commit, so the Saving chip goes when it is written.
export function useTemplates() {
  return useQuery({
    queryKey: templatesQueryKey,
    queryFn: () => apiFetch<TemplatesDoc>('/api/templates'),
    refetchInterval: (query) => (query.state.data?.saving ? FAST_POLL_MS : false),
  })
}

export function useTemplateMatches() {
  return useQuery({
    queryKey: templateMatchesQueryKey,
    queryFn: () => apiFetch<TemplateMatches>('/api/template-matches'),
  })
}

interface QueuedTemplates {
  entryId: number
  templates: Template[]
}

// One change of the templates, known by name (docs/spec.md, setToolNoteSection). The backend
// applies it to the templates as they are when it is written, so two tabs never undo each other.
export type TemplateChange =
  | { op: 'addTemplate'; template: Template }
  | { op: 'setTemplate'; template: Template }
  | { op: 'renameTemplate'; name: string; to: string }
  | { op: 'moveTemplate'; name: string; index: number }
  | { op: 'removeTemplate'; name: string }

// Queued as a UI edit of the tool note, which the backend starts to commit at once.
export function saveTemplates(changes: TemplateChange[]): Promise<QueuedTemplates> {
  return apiFetch<QueuedTemplates>('/api/templates', { method: 'PUT', body: { changes } })
}

// The answer of both writes of the tool note's name (docs/spec.md, The tool note).
interface ToolNoteNameWrite {
  toolNoteName: string
  toolNoteExpectedName: string
  revisionDate: string
  backup: string
}

export function renameToolNoteBack(revisionDate: string): Promise<ToolNoteNameWrite> {
  return apiFetch<ToolNoteNameWrite>('/api/tool-note/rename-back', { method: 'POST', body: { revisionDate } })
}

// name is the one the warning showed, so a note renamed again since is refused (409).
export function keepToolNoteName(name: string): Promise<ToolNoteNameWrite> {
  return apiFetch<ToolNoteNameWrite>('/api/tool-note/keep-name', { method: 'POST', body: { name } })
}

