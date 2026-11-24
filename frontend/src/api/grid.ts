import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'

export type ColumnGroup = 'item' | 'login' | 'card' | 'identity' | 'sshKey' | 'fields'

export interface GridColumn {
  id: string
  label: string
  group: ColumnGroup
  secret: boolean
  multi: boolean
  readOnly: boolean
}

export interface Masked {
  masked: true
}

export type CellValue = string | number | boolean | string[] | Masked

export interface GridAttachment {
  id: string
  fileName: string
}

export interface GridRow {
  id: string
  revisionDate: string
  cells: Record<string, CellValue | undefined>
  // The ids behind the folder and collections cells, sent back with a move or a change of the
  // collections so the backend can tell a change made elsewhere (the cells hold the names).
  folderId?: string | null
  collectionIds?: string[]
  // The item's attachments in its order, what a rename names one by (the attachments cell holds
  // the names only), left out when there are none. Never the url or the size.
  attachments?: GridAttachment[]
  // Set on bwtidy's own note, which the grid shows read-only.
  toolNote?: boolean
  // The secret columns the item has but holds no value in, left out when there are none.
  emptySecrets?: string[]
  // The text custom fields the item has without a value, left out when there are none.
  emptyTextFields?: string[]
  // The boolean custom fields the item has, with or without a value, left out when there are none.
  booleanFields?: string[]
  // The linked custom fields the item has (they hold no value of their own), left out when there are none.
  linkedFields?: string[]
  // Set when the item has a generator setting of its own, left out otherwise.
  ownGenerator?: boolean
}

// A custom field name some item has (empty values included) with its field type.
export interface VaultField {
  name: string
  fieldType: 0 | 1 | 2
}

// An organization of the vault with all its collections, also those no item is in.
export interface VaultOrganization {
  name: string
  collections: string[]
}

export const FOLDER_COLUMN = 'folder'

export const ATTACHMENTS_COLUMN = 'attachments'

// A folder of the vault, also one no item is in, with the number of items in it and of items
// in the trash still in it (Manage folders refuses to delete it while either is not 0).
export interface VaultFolder {
  id: string
  name: string
  items: number
  trashedItems: number
}

export interface GridData {
  columns: GridColumn[]
  rows: GridRow[]
  // The id of bwtidy's own note, whatever it is named now.
  toolNoteId?: string | null
  // Every custom field name of the vault's items, the tool note left out.
  fields?: VaultField[]
  organizations?: VaultOrganization[]
  folders?: VaultFolder[]
  // The secret columns still to fill in, by item id.
  toFill?: Record<string, string[]>
}

export const NAME_COLUMN = 'name'

// The grid's template column. Coordinates are fixed paths or fields.<name>, so this id never collides with one.
export const TEMPLATE_COLUMN = 'template'

export const gridQueryKey = ['grid'] as const

// The tool note is found by the id the backend sends, never by its name.
export function isToolNote(row: GridRow): boolean {
  return row.toolNote === true
}

function markToolNote(data: GridData): GridData {
  const id = data.toolNoteId
  if (!id) {
    return data
  }
  return { ...data, rows: data.rows.map((row) => (row.id === id ? { ...row, toolNote: true } : row)) }
}

export function useGridData() {
  return useQuery({
    queryKey: gridQueryKey,
    queryFn: async () => markToolNote(await apiFetch<GridData>('/api/grid')),
  })
}

// Deliberately not a React Query: a revealed value must not outlive the cell
// that shows it.
export async function fetchSecret(itemId: string, column: string): Promise<string> {
  const path = `/api/items/${encodeURIComponent(itemId)}/secret?column=${encodeURIComponent(column)}`
  const { value } = await apiFetch<{ value: string }>(path)
  return value
}

