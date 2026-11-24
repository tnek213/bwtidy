import type { GridAttachment, GridRow, VaultFolder } from './grid.ts'

// The write contract of POST /api/operations. Every op names the items it
// touches with the revisionDate the grid showed. A move, a change of the
// collections and a setLeaf of favorite also carry the folder, collections or
// favorite flag the row showed: other clients change those without a new
// revisionDate, and the backend refuses the edit as a conflict when they differ.
export type FieldType = 0 | 1 | 2

export type LeafValue = string | number | boolean | string[]

export type Operation =
  | {
      kind: 'setLeaf'
      itemId: string
      revisionDate: string
      column: string
      value: LeafValue
      fieldType?: FieldType
      shownFavorite?: boolean
    }
  | { kind: 'removeLeaf'; itemId: string; revisionDate: string; column: string }
  | { kind: 'generateLeaf'; itemId: string; revisionDate: string; column: string }
  | {
      kind: 'moveToFolder'
      itemIds: string[]
      revisionDates: Record<string, string>
      folderName: string
      shownFolderIds?: Record<string, string | null>
    }
  | { kind: 'setCollections'; itemId: string; revisionDate: string; collectionNames: string[]; shownCollectionIds?: string[] }
  | {
      kind: 'moveToOrganization'
      itemId: string
      revisionDate: string
      organizationName: string
      collectionNames: string[]
    }
  | { kind: 'moveToPersonal'; itemId: string; revisionDate: string }
  | { kind: 'trashItem'; itemId: string; revisionDate: string }
  | { kind: 'applyTemplate'; itemId: string; revisionDate: string; template: string; mapping: Record<string, string> }
  | { kind: 'renameField'; from: string; to: string; items: ItemRef[] }
  | { kind: 'mergeColumns'; sources: string[]; target: string; items: ItemRef[]; resolutions?: Record<string, string> }
  | {
      kind: 'mergeItems'
      fromItemId: string
      fromRevisionDate: string
      toItemId: string
      toRevisionDate: string
      mapping: Record<string, string>
      append: string[]
    }
  // Only MCP proposes it: the whole URI list of each login, shownUris what the agent was shown.
  | { kind: 'setUris'; items: ItemUris[] }
  | { kind: 'restoreItems'; backup: string; itemIds: string[]; revisionDates: Record<string, string>; changeLogId?: number | null }
  // A folder has no revisionDate, its name as the dialog showed it tells a change made elsewhere.
  | { kind: 'renameFolder'; folderId: string; shownName: string; newName: string }
  | { kind: 'deleteFolder'; folderId: string; shownName: string }
  | { kind: 'restoreFolder'; backup: string; folderId: string; changeLogId?: number | null }
  // Both the UI (Rename attachments in the cell menu, and to restore a change log row) and an
  // agent through MCP queue it.
  | {
      kind: 'renameAttachment'
      itemId: string
      revisionDate: string
      attachmentId: string
      fileName: string
      shownFileName: string
    }
  | {
      kind: 'createItem'
      name: string
      // Exactly one of type and template.
      type?: number
      template?: string
      folderName?: string
      organizationName?: string
      collectionNames?: string[]
      // Non-secret columns only.
      values?: Record<string, LeafValue>
      // Secret columns typed in the New item dialog, never from MCP.
      secretValues?: Record<string, LeafValue>
      generatePassword?: boolean
      // Secret columns bw generates at write time: login.password or hidden fields.
      generateColumns?: string[]
    }

export type MergeColumnsOperation = Extract<Operation, { kind: 'mergeColumns' }>

export interface ItemRef {
  itemId: string
  revisionDate: string
}

// Bitwarden's URI match setting, 0 domain to 5 never, null for the client's default.
interface UriEntry {
  uri: string
  match: 0 | 1 | 2 | 3 | 4 | 5 | null
}

interface ItemUris extends ItemRef {
  shownUris: UriEntry[]
  uris: UriEntry[]
}

export function setLeaf(row: GridRow, column: string, value: LeafValue, fieldType?: FieldType): Operation {
  const op: Operation = { kind: 'setLeaf', itemId: row.id, revisionDate: row.revisionDate, column, value }
  if (fieldType !== undefined) {
    op.fieldType = fieldType
  }
  const favorite = row.cells.favorite
  if (column === 'favorite' && typeof favorite === 'boolean') {
    op.shownFavorite = favorite
  }
  return op
}

export function removeLeaf(row: GridRow, column: string): Operation {
  return { kind: 'removeLeaf', itemId: row.id, revisionDate: row.revisionDate, column }
}

export function generateLeaf(row: GridRow, column: string): Operation {
  return { kind: 'generateLeaf', itemId: row.id, revisionDate: row.revisionDate, column }
}

export function moveToFolder(row: GridRow, folderName: string): Operation {
  const op: Operation = { kind: 'moveToFolder', itemIds: [row.id], revisionDates: { [row.id]: row.revisionDate }, folderName }
  if (row.folderId !== undefined) {
    op.shownFolderIds = { [row.id]: row.folderId }
  }
  return op
}

export function setCollections(row: GridRow, collectionNames: string[]): Operation {
  const op: Operation = { kind: 'setCollections', itemId: row.id, revisionDate: row.revisionDate, collectionNames }
  if (row.collectionIds !== undefined) {
    op.shownCollectionIds = row.collectionIds
  }
  return op
}

export function moveToOrganization(row: GridRow, organizationName: string, collectionNames: string[]): Operation {
  return { kind: 'moveToOrganization', itemId: row.id, revisionDate: row.revisionDate, organizationName, collectionNames }
}

export function moveToPersonal(row: GridRow): Operation {
  return { kind: 'moveToPersonal', itemId: row.id, revisionDate: row.revisionDate }
}

export function trashItem(row: GridRow): Operation {
  return { kind: 'trashItem', itemId: row.id, revisionDate: row.revisionDate }
}

export function renameFolder(folder: VaultFolder, newName: string): Operation {
  return { kind: 'renameFolder', folderId: folder.id, shownName: folder.name, newName }
}

// shownFileName is the name the dialog showed, which tells a rename made elsewhere.
export function renameAttachment(row: GridRow, attachment: GridAttachment, fileName: string): Operation {
  return {
    kind: 'renameAttachment',
    itemId: row.id,
    revisionDate: row.revisionDate,
    attachmentId: attachment.id,
    fileName,
    shownFileName: attachment.fileName,
  }
}

export function deleteFolder(folder: VaultFolder): Operation {
  return { kind: 'deleteFolder', folderId: folder.id, shownName: folder.name }
}

// The backend names the operation a 422 is about, which says nothing when there was only one.
export const FIRST_OPERATION = /^Operation 1 \([A-Za-z]+\): /
