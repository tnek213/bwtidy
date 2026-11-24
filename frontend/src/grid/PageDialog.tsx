import ItemGeneratorDialog from './ItemGeneratorDialog.tsx'
import ConvertDialog from './ConvertDialog.tsx'
import type { Template, TemplateMatches } from '../api/templates.ts'
import { type DialogState, GridDialog, NormalizeDialog, type NormalizeState } from './dialogs/index.tsx'
import { TrashManyDialog } from './dialogs/TrashManyDialog.tsx'
import type { QueueEntry } from '../api/queue.ts'
import AttachmentsDialog from './AttachmentsDialog.tsx'
import FoldersDialog from './FoldersDialog.tsx'
import type { CellFormats } from './formats.ts'
import type { GridColumn, GridRow, VaultFolder, VaultOrganization } from '../api/grid.ts'
import NewItemDialog from './NewItemDialog.tsx'
import RareFieldsDialog from './RareFieldsDialog.tsx'
import type { MergeColumnsOperation, Operation } from '../api/operations.ts'

// The grid page has at most one dialog open, this says which and for what.
export type PageDialogState =
  | { kind: 'cell'; dialog: DialogState }
  | { kind: 'normalize'; dialog: NormalizeState }
  | { kind: 'convert'; row: GridRow }
  | { kind: 'create' }
  | { kind: 'generator'; row: GridRow }
  | { kind: 'folders' }
  | { kind: 'attachments'; row: GridRow }
  | { kind: 'trashMany'; rows: GridRow[] }
  | { kind: 'rareFields' }

interface PageDialogProps {
  state: PageDialogState
  rows: GridRow[]
  columns: GridColumn[]
  organizations?: readonly VaultOrganization[]
  folders?: readonly VaultFolder[]
  // The pending queue entries, for the folder and attachment changes on their way.
  pending?: readonly QueueEntry[]
  templates: Template[]
  matches: TemplateMatches | undefined
  formats: CellFormats
  onClose: () => void
  // Closes the dialog and queues the operations.
  onApply: (operations: Operation[]) => void
  onMerge: (sources: string[], target: string) => void
  onResolve: (op: MergeColumnsOperation, resolutions: Record<string, string>) => void
  // New item queued its entry.
  onQueued: () => void
}

export default function PageDialog({ state, rows, columns, organizations, templates, onClose, onApply, ...props }: PageDialogProps) {
  switch (state.kind) {
    case 'cell':
      return (
        <GridDialog
          state={state.dialog}
          rows={rows}
          organizations={organizations}
          datatypeOf={(row, column) => props.formats.get(row.id)?.get(column.id)?.datatypeId ?? null}
          onClose={onClose}
          onApply={onApply}
        />
      )
    case 'normalize':
      return (
        <NormalizeDialog
          state={state.dialog}
          rows={rows}
          columns={columns}
          onClose={onClose}
          onApply={onApply}
          onMerge={props.onMerge}
          onResolve={props.onResolve}
        />
      )
    case 'convert':
      return (
        <ConvertDialog
          row={state.row}
          templates={templates}
          match={props.matches?.[state.row.id]}
          onClose={onClose}
          onApply={onApply}
        />
      )
    case 'create':
      return (
        <NewItemDialog
          rows={rows}
          organizations={organizations}
          templates={templates}
          onClose={onClose}
          onQueued={props.onQueued}
        />
      )
    case 'generator':
      return <ItemGeneratorDialog row={state.row} onClose={onClose} />
    case 'trashMany':
      return <TrashManyDialog rows={state.rows} onClose={onClose} onApply={onApply} />
    case 'folders':
      return <FoldersDialog folders={props.folders ?? []} pending={props.pending ?? []} onClose={onClose} onApply={onApply} />
    case 'attachments':
      return <AttachmentsDialog row={state.row} pending={props.pending ?? []} onClose={onClose} onApply={onApply} />
    case 'rareFields':
      return <RareFieldsDialog onClose={onClose} />
  }
}
