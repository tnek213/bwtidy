import { FIELDS_PREFIX, fieldColumn, parseFieldColumn } from '../shared/coordinates.ts'
import { ATTACHMENTS_COLUMN, type GridColumn, type GridRow, isToolNote, type VaultOrganization } from '../api/grid.ts'
import { type Operation, moveToFolder, removeLeaf, setLeaf } from '../api/operations.ts'
import { queuedBlocker, type ShownRow, shownValue } from './queued.ts'
import { isMasked } from '../shared/values.ts'
import { hasValue } from './columnValues.ts'

// How a cell is edited. Inline kinds use an AG Grid editor, the others open a dialog.
type InlineKind = 'text' | 'longText' | 'boolean'
export type DialogKind = 'secret' | 'uris' | 'collections' | 'folder' | 'organization'
type EditKind = InlineKind | DialogKind

export function isInline(kind: EditKind | null): kind is InlineKind {
  return kind === 'text' || kind === 'longText' || kind === 'boolean'
}

const BOOLEAN_COLUMNS = new Set(['favorite', 'reprompt'])

// Built-in leaves of a type's sub-object only exist on items of that type.
const TYPE_GROUPS = new Set(['login', 'card', 'identity', 'sshKey'])

// Changing the type is a conversion with a mapping, which has its own UI.
const NOT_EDITABLE = new Set(['type'])

// A name the item must keep, and organization and collections, which have their own operations.
const NOT_REMOVABLE = new Set(['name', 'type', 'organization', 'collections'])

export function isBooleanColumn(column: GridColumn): boolean {
  return BOOLEAN_COLUMNS.has(column.id)
}

export function editKind(column: GridColumn): EditKind | null {
  if (column.readOnly || NOT_EDITABLE.has(column.id)) {
    return null
  }
  if (column.secret) {
    return 'secret'
  }
  switch (column.id) {
    case 'login.uris':
      return 'uris'
    case 'collections':
      return 'collections'
    case 'folder':
      return 'folder'
    case 'organization':
      return 'organization'
    case 'notes':
      return 'longText'
  }
  if (isBooleanColumn(column)) {
    return 'boolean'
  }
  return 'text'
}

// A field name can be hidden in one item and text in another, and an existing field keeps its type.
export function isTextFieldOfItem(row: GridRow, column: GridColumn): boolean {
  if (column.group !== 'fields' || isBooleanFieldOfItem(row, column.id)) {
    return false
  }
  const value = row.cells[column.id]
  return (row.emptyTextFields ?? []).includes(column.id) || (value !== undefined && !isMasked(value))
}

export const PLAIN_IN_SECRET_CLASS = 'bw-plain-in-secret'
export const PLAIN_IN_SECRET_TOOLTIP = 'Stored as a text field on this item, so an agent can read it through MCP'

// A text field's value in a secret column is plain, as MCP sees it too, so the grid shows it with a
// mark instead of a mask. A queued edit keeps the field text, so it shows the same way.
export function plainInSecretColumn(column: GridColumn, row: ShownRow): boolean {
  if (!column.secret || !isTextFieldOfItem(row, column)) {
    return false
  }
  const value = shownValue(row, column.id)
  return value !== undefined && !isMasked(value)
}

export function isPersonal(row: GridRow): boolean {
  return typeof row.cells.organization !== 'string'
}

export const TOOL_NOTE_BLOCKER = "bwtidy's own note. Templates are edited on the Types page."

const ATTACHMENTS_BLOCKER = 'Rename an attachment from the cell menu.'

// The one read-only column an edit attempt (double click, Enter, typing) answers with a message,
// the others stay silent: its attachments are renamed from the cell menu.
export function pointsToCellMenu(column: GridColumn): boolean {
  return column.id === ATTACHMENTS_COLUMN
}

// Why a cell cannot be edited, or null when it can.
export function editBlocker(column: GridColumn, row: ShownRow): string | null {
  if (isToolNote(row)) {
    return TOOL_NOTE_BLOCKER
  }
  const queued = queuedBlocker(row, column.id)
  if (queued !== null) {
    return queued
  }
  const kind = editKind(column)
  if (kind === null) {
    if (column.id === 'type') {
      return 'The type is changed by converting the item.'
    }
    return column.id === ATTACHMENTS_COLUMN ? ATTACHMENTS_BLOCKER : 'This column is read-only.'
  }
  return unavailableReason(column, row) ?? (isLinkedFieldOfItem(row, column.id) ? LINKED_FIELD_BLOCKER : null)
}

export const LINKED_FIELD_BLOCKER = 'On this item this is a linked field, it has no value of its own.'

// A field name can be boolean or linked on one item and hidden on another, and an existing field keeps its type.
export function isBooleanFieldOfItem(row: GridRow, columnId: string): boolean {
  return (row.booleanFields ?? []).includes(columnId)
}

export function isLinkedFieldOfItem(row: GridRow, columnId: string): boolean {
  return (row.linkedFields ?? []).includes(columnId)
}

// Why a cell cannot hold a value at all (the grid shows it grey), or null
// when it can. Judged on the vault values, like every write.
export function unavailableReason(column: GridColumn, row: GridRow): string | null {
  if (TYPE_GROUPS.has(column.group) && row.cells.type !== column.group) {
    return `Only ${column.group} items have ${column.id}.`
  }
  if (column.id === 'collections' && isPersonal(row)) {
    return 'Only organization items have collections. Move the item to an organization first.'
  }
  return occurrenceBlocker(column, row)
}

// The backend adds a field only as the next occurrence of its name, so
// fields.<name>#3 waits until #2 has a value. A cell that has a value exists
// already: an empty earlier field is not in the row but is in the item.
function occurrenceBlocker(column: GridColumn, row: GridRow): string | null {
  const field = parseFieldColumn(column.id)
  if (field === null || field.occurrence < 2 || row.cells[column.id] !== undefined) {
    return null
  }
  const earlier = fieldColumn(field.name, field.occurrence - 1)
  if (row.cells[earlier] !== undefined) {
    return null
  }
  return `Fill ${earlier.slice(FIELDS_PREFIX.length)} first.`
}

// The operation that empties a cell, or null when the cell cannot be emptied.
export function removeOperation(column: GridColumn, row: ShownRow): Operation | null {
  if (editBlocker(column, row) !== null || NOT_REMOVABLE.has(column.id) || !hasValue(row.cells[column.id])) {
    return null
  }
  if (column.id === 'folder') {
    return moveToFolder(row, '')
  }
  // A setLeaf carries the flag the row showed, a removeLeaf would not.
  if (column.id === 'favorite') {
    return setLeaf(row, column.id, false)
  }
  return removeLeaf(row, column.id)
}

// Whether a generated value fits the cell: login.password of a login, or a hidden custom field. A
// field name can be hidden in one item and text or boolean in another, so a filled cell has to be
// masked itself, and an empty one must be no field of another type on the item.
export function canGenerate(column: GridColumn, row: ShownRow): boolean {
  if (editBlocker(column, row) !== null) {
    return false
  }
  if (column.id === 'login.password') {
    return true
  }
  if (column.group !== 'fields' || !column.secret) {
    return false
  }
  const value = row.cells[column.id]
  if (value !== undefined) {
    return isMasked(value)
  }
  return !(row.emptyTextFields ?? []).includes(column.id) && !isBooleanFieldOfItem(row, column.id)
}

function sortedNames(names: Iterable<string>): string[] {
  return [...names].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
}

// The vault's organizations and collections, then what the rows use: an item
// names an organization or a collection by its id when the listing lacks it.
export function organizationCollections(rows: GridRow[], organizations: readonly VaultOrganization[] = []): Map<string, string[]> {
  const byOrganization = new Map<string, Set<string>>()
  function add(organization: string, collections: readonly string[]) {
    const names = byOrganization.get(organization) ?? new Set<string>()
    byOrganization.set(organization, names)
    for (const name of collections) {
      names.add(name)
    }
  }
  for (const { name, collections } of organizations) {
    add(name, collections)
  }
  for (const { cells } of rows) {
    if (typeof cells.organization === 'string') {
      add(cells.organization, Array.isArray(cells.collections) ? cells.collections : [])
    }
  }
  return new Map(sortedNames(byOrganization.keys()).map((organization) => [organization, sortedNames(byOrganization.get(organization)!)]))
}
