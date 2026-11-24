import type { GridColumn, GridRow } from '../../api/grid.ts'
import type { MergeColumnsOperation, Operation } from '../../api/operations.ts'
import { type DialogKind, isBooleanFieldOfItem } from '../editing.ts'
import type { MergeConflict } from '../normalize.ts'
import { AddFieldDialog } from './AddFieldDialog.tsx'
import { BooleanFieldDialog } from './BooleanFieldDialog.tsx'
import { CollectionsDialog } from './CollectionsDialog.tsx'
import { FolderDialog } from './FolderDialog.tsx'
import { GenerateDialog } from './GenerateDialog.tsx'
import { MergeColumnsDialog } from './MergeColumnsDialog.tsx'
import { MergeConflictDialog } from './MergeConflictDialog.tsx'
import { MergeItemsDialog } from './MergeItemsDialog.tsx'
import { OrganizationDialog } from './OrganizationDialog.tsx'
import { RenameFieldDialog } from './RenameFieldDialog.tsx'
import { SecretDialog } from './SecretDialog.tsx'
import { TrashDialog } from './TrashDialog.tsx'
import type { DialogProps, NormalizeDialogProps } from './types.ts'
import { UrisDialog } from './UrisDialog.tsx'

export type DialogState =
  | { kind: DialogKind; row: GridRow; column: GridColumn }
  | { kind: 'addField'; row: GridRow }
  | { kind: 'trash'; row: GridRow }
  | { kind: 'generate'; row: GridRow; column: GridColumn }

export function GridDialog({ state, ...props }: Omit<DialogProps, 'row'> & { state: DialogState }) {
  const { row } = state
  switch (state.kind) {
    case 'secret':
      return isBooleanFieldOfItem(row, state.column.id) ? (
        <BooleanFieldDialog row={row} column={state.column} {...props} />
      ) : (
        <SecretDialog row={row} column={state.column} {...props} />
      )
    case 'uris':
      return <UrisDialog row={row} column={state.column} {...props} />
    case 'collections':
      return <CollectionsDialog row={row} column={state.column} {...props} />
    case 'folder':
      return <FolderDialog row={row} {...props} />
    case 'organization':
      return <OrganizationDialog row={row} {...props} />
    case 'addField':
      return <AddFieldDialog row={row} {...props} />
    case 'trash':
      return <TrashDialog row={row} {...props} />
    case 'generate':
      return <GenerateDialog row={row} column={state.column} {...props} />
  }
}

export type NormalizeState =
  | { kind: 'rename'; column: GridColumn }
  | { kind: 'mergeColumns'; columnIds: string[]; fixedSource?: string }
  | { kind: 'conflicts'; op: MergeColumnsOperation; conflicts: MergeConflict[] }
  | { kind: 'mergeItems'; row: GridRow }

interface OpenNormalizeDialogProps extends NormalizeDialogProps {
  state: NormalizeState
  onApply: (operations: Operation[]) => void
  onMerge: (sources: string[], target: string) => void
  onResolve: (op: MergeColumnsOperation, resolutions: Record<string, string>) => void
}

export function NormalizeDialog({ state, onApply, onMerge, onResolve, ...props }: OpenNormalizeDialogProps) {
  switch (state.kind) {
    case 'rename':
      return <RenameFieldDialog column={state.column} onApply={onApply} onMerge={onMerge} {...props} />
    case 'mergeColumns':
      return <MergeColumnsDialog columnIds={state.columnIds} fixedSource={state.fixedSource} onMerge={onMerge} {...props} />
    case 'conflicts':
      return (
        <MergeConflictDialog
          conflicts={state.conflicts}
          onResolve={(resolutions) => onResolve(state.op, resolutions)}
          {...props}
        />
      )
    case 'mergeItems':
      return <MergeItemsDialog row={state.row} onApply={onApply} {...props} />
  }
}
