import { Alert, Box, CircularProgress, Typography, useTheme } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import type {
  BodyScrollEvent,
  ColDef,
  ColumnResizedEvent,
  GridApi,
  GetRowIdParams,
  RowClassParams,
  RowSelectionOptions,
  SelectionColumnDef,
  SuppressHeaderKeyboardEventParams,
} from 'ag-grid-community'
import { AgGridReact } from 'ag-grid-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { VISUALLY_HIDDEN } from '../shared/visuallyHidden.ts'
import { cellDescription } from './cellDescription.ts'
import { FieldHeader } from './ColumnHeader.tsx'
import { matchTooltip } from './matches.ts'
import { type Revealers, RevealersContext, revealersOf } from './revealers.ts'
import { markCellEditors } from '../shared/noPasswordManager.ts'
import { useColorMode } from '../theme/colorMode.tsx'
import { capAutosizedColumns } from './autosize.ts'
import {
  buildColumnDefs,
  GRID_THEME_DARK,
  GRID_THEME_LIGHT,
  rareFieldsColumnDef,
  TEMPLATE_CHOOSER_COLUMN,
  templateColumnDef,
} from './columns.tsx'
import GridToolbar from './GridToolbar.tsx'
import CellMenu, { type CellMenuState } from './CellMenu.tsx'
import { cellFormats, type CellFormats } from './formats.ts'
import { markToFill, type ToFill, toFillAction } from './toFill.ts'
import { joinConflictMarks } from '../shared/conflicts.ts'
import FeedbackSnackbar from './FeedbackSnackbar.tsx'
import { gridCellStyles } from './gridStyles.ts'
import HeaderMenu, { type HeaderMenuState } from './HeaderMenu.tsx'
import PageDialog, { type PageDialogState } from './PageDialog.tsx'
import RenamedNoteAlert from './RenamedNoteAlert.tsx'
import SelectionBar from './SelectionBar.tsx'
import type { NormalizeState } from './dialogs/index.tsx'
import { useCellMarks } from './useCellMarks.ts'
import { useGridFilters } from './useGridFilters.ts'
import { isMenuKey, useCellEvents } from './useCellEvents.ts'
import {
  type GridColumn,
  type GridData,
  gridQueryKey,
  type GridRow,
  isToolNote,
  NAME_COLUMN,
  TEMPLATE_COLUMN,
  useGridData,
} from '../api/grid.ts'
import {
  type HeaderActions,
  HeaderContext,
  mergeColumns,
  type MergeConflict,
  mergeConflictsOf,
  nearDuplicatesByColumn,
  useNearDuplicates,
} from './normalize.ts'
import { NameSuffixContext, type NameSuffixState, outOfViewColumns } from './nameSuffix.tsx'
import type { MergeColumnsOperation } from '../api/operations.ts'
import { useWriter } from './writer.ts'
import { buildOverlay, EMPTY_OVERLAY, shownCells, type ShownRow, shownRows, withExtraColumns } from './queued.ts'
import { useHideToolNote, useRareFieldsMax } from './display.ts'
import { orderedRareColumns, RARE_FIELDS_COLUMN, rareColumns } from './rareFields.ts'
import { useAutoCommit } from '../commit/autoCommit.ts'
import { queueQueryKey, useQueue } from '../api/queue.ts'
import { toggled } from '../shared/sets.ts'
import { useTemplateMatches, useTemplates } from '../api/templates.ts'
import { columnCounts, nonEmptyColumns } from './columnValues.ts'

const getRowId = ({ data }: GetRowIdParams<ShownRow>) => data.id

// Ticking is the only way to select: a click on a cell edits as before. The tool note has no checkbox.
const ROW_SELECTION: RowSelectionOptions<ShownRow> = {
  mode: 'multiRow',
  checkboxes: true,
  headerCheckbox: true,
  selectAll: 'filtered',
  enableClickSelection: false,
  hideDisabledCheckboxes: true,
  isRowSelectable: ({ data }) => data !== undefined && !isToolNote(data),
}

// AG Grid's own names of the checkbox column and of a row's checkbox.
const LOCALE_TEXT = { ariaHeaderSelection: 'Tick rows' }

// The menu of a field, folder or Rare fields header opens with the cell menu's keys: a keyboard
// cannot reach the button in the header.
function headerMenuKey(openMenu: (columnId: string, anchor: HTMLElement) => void) {
  return ({ column, event }: SuppressHeaderKeyboardEventParams<ShownRow>) => {
    if (!('getColDef' in column) || column.getColDef().headerComponentParams?.innerHeaderComponent !== FieldHeader) {
      return false
    }
    if (event.type !== 'keydown' || !isMenuKey(event)) {
      return false
    }
    event.preventDefault()
    openMenu(column.getColId(), event.target as HTMLElement)
    return true
  }
}

const SELECTION_COLUMN: SelectionColumnDef = {
  pinned: 'left',
  lockPinned: true,
  width: 48,
  minWidth: 48,
  maxWidth: 48,
  resizable: false,
  suppressMovable: true,
}

export default function GridPage() {
  const grid = useGridData()
  const [showQueued, setShowQueued] = useState(true)
  const queue = useQueue('pending')
  const autoCommit = useAutoCommit()
  const [menu, setMenu] = useState<CellMenuState | null>(null)
  const [openDialog, setOpenDialog] = useState<PageDialogState | null>(null)
  const templatesDoc = useTemplates()
  const matches = useTemplateMatches()
  const templates = useMemo(() => templatesDoc.data?.templates ?? [], [templatesDoc.data])
  const [headerMenu, setHeaderMenu] = useState<HeaderMenuState | null>(null)
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [mergeSelection, setMergeSelection] = useState<Set<string>>(() => new Set())
  const nearDuplicateGroups = useNearDuplicates()
  const gridRef = useRef<AgGridReact<ShownRow>>(null)
  // The same map for the whole life of the page, filled by the rendered cells.
  const [revealers] = useState<Revealers>(() => new Map())
  const gridBox = useRef<HTMLElement | null>(null)
  const gridBoxRef = useCallback((element: HTMLElement) => {
    gridBox.current = element
    const stop = markCellEditors(element)
    return () => {
      gridBox.current = null
      stop()
    }
  }, [])
  const queryClient = useQueryClient()
  const theme = useTheme()
  const { mode } = useColorMode()

  const vaultColumns = useMemo(() => grid.data?.columns ?? [], [grid.data])
  const hideToolNote = useHideToolNote()
  // Taken out here, so every count, list and selection below works on the rows shown.
  const rows = useMemo(
    () => (grid.data?.rows ?? []).filter((row) => !(hideToolNote && isToolNote(row))),
    [grid.data, hideToolNote],
  )
  const toFill = useMemo<ToFill>(() => grid.data?.toFill ?? {}, [grid.data])
  const rareMax = useRareFieldsMax()
  const gridFilters = useGridFilters(rows, matches.data, toFill, rareMax)
  const { visibleRows, hidden, hideEmpty, hideRare } = gridFilters
  // Over every item but the tool note, whether or not the grid shows it, and on the vault values.
  const rare = useMemo(() => rareColumns(vaultColumns, grid.data?.rows ?? [], rareMax), [vaultColumns, grid.data, rareMax])
  const overlay = useMemo(
    () =>
      showQueued && (queue.data || autoCommit.settling.length > 0)
        ? buildOverlay([...(queue.data ?? []), ...autoCommit.settling], vaultColumns)
        : EMPTY_OVERLAY,
    [showQueued, queue.data, autoCommit.settling, vaultColumns],
  )
  const columns = useMemo(() => withExtraColumns(vaultColumns, overlay.extraColumns), [vaultColumns, overlay])
  const queuedOnly = useMemo(() => new Set(overlay.extraColumns.map((column) => column.id)), [overlay])
  const columnsById = useMemo(() => new Map(columns.map((column) => [column.id, column])), [columns])
  const writer = useWriter(rows, autoCommit.clearConflicts)
  const { setFeedback } = writer
  const conflicts = useMemo(() => joinConflictMarks(writer.conflicts, autoCommit.conflicts), [writer.conflicts, autoCommit.conflicts])
  // By the vault's columns, so a change of the queue alone does not redraw the rows. A column the
  // grid does not have counts as secret, so a hover text never says too much.
  const isSecretColumn = useMemo(() => {
    const secret = new Map(vaultColumns.map((column) => [column.id, column.secret]))
    return (columnId: string) => secret.get(columnId) ?? true
  }, [vaultColumns])
  const formats = useMemo<CellFormats>(
    () => cellFormats(matches.data, templates, isSecretColumn),
    [matches.data, templates, isSecretColumn],
  )
  const { marks, rowClassRules } = useCellMarks(gridRef, {
    conflicts,
    failedCells: autoCommit.failure?.errorCells,
    toFill,
    formats,
    searchWords: gridFilters.searchWords,
    commitStatus: autoCommit.status,
  })
  // The rows keep their old styles (the tool note's background) after the theme changes.
  useEffect(() => {
    gridRef.current?.api?.redrawRows()
  }, [mode])
  // Search and filters ran on the vault values above, the rows show the queued ones.
  const rowData = useMemo(() => shownRows(visibleRows, overlay), [visibleRows, overlay])
  // A row no longer shown is never part of an action.
  const selectedRows = useMemo(
    () => rowData.filter((row) => selectedIds.has(row.id) && !isToolNote(row)),
    [rowData, selectedIds],
  )
  // The ticks go when a search or filter changes which rows are shown.
  const shownIds = visibleRows.map((row) => row.id).join('\n')
  useEffect(() => {
    gridRef.current?.api?.deselectAll()
  }, [shownIds])
  const templateHidden = hidden.has(TEMPLATE_COLUMN)
  const templateColumn = useMemo(
    () => templateColumnDef(matches.data, templates, templateHidden, isSecretColumn),
    [matches.data, templates, templateHidden, isSecretColumn],
  )
  const chooserColumns = useMemo(
    () => columns.flatMap((column) => (column.id === NAME_COLUMN ? [column, TEMPLATE_CHOOSER_COLUMN] : [column])),
    [columns],
  )
  const [outOfView, setOutOfView] = useState<ReadonlySet<string>>(() => new Set())
  const suffixState = useMemo<NameSuffixState>(() => ({ outOfView, matches: matches.data }), [outOfView, matches.data])
  // A listed cell has no value yet, and hiding the empty columns must not hide what is to fill in.
  const filled = useMemo(() => {
    const columnIds = nonEmptyColumns(rowData.map((row) => ({ ...row, cells: shownCells(row) })))
    return new Set([...columnIds, ...rowData.flatMap((row) => toFill[row.id] ?? [])])
  }, [rowData, toFill])
  // Over every item of the vault, not the rows a search or filter shows.
  const counts = useMemo(() => columnCounts(rows), [rows])
  const rareFieldsColumn = useMemo(
    () => rareFieldsColumnDef(orderedRareColumns(vaultColumns, rare), rareMax, marks),
    [vaultColumns, rare, rareMax, marks],
  )
  const columnDefs = useMemo(
    () =>
      buildColumnDefs(
        columns,
        (id) => hidden.has(id) || (hideEmpty && !filled.has(id)) || (hideRare && rare.has(id)),
        marks,
        templateColumn,
        queuedOnly,
        counts,
        rareFieldsColumn,
      ),
    [columns, hidden, hideEmpty, filled, hideRare, rare, marks, templateColumn, queuedOnly, counts, rareFieldsColumn],
  )

  // Every scroll event lands here, so the state only changes when the set does.
  function updateOutOfView(api: GridApi<ShownRow>) {
    const next = outOfViewColumns(api)
    setOutOfView((current) =>
      current.size === next.size && [...next].every((id) => current.has(id)) ? current : next,
    )
  }

  function handleColumnResized(event: ColumnResizedEvent<ShownRow>) {
    capAutosizedColumns(event)
    updateOutOfView(event.api)
  }

  function handleBodyScroll(event: BodyScrollEvent<ShownRow>) {
    if (event.direction === 'horizontal') {
      updateOutOfView(event.api)
    }
  }

  function clearSelection() {
    gridRef.current?.api?.deselectAll()
    setSelectedIds(new Set())
  }

  function apply(operations: Parameters<typeof writer.apply>[0]) {
    void writer.apply(operations)
  }

  // The answer is the list with the change, shown at once. The save itself is a queued entry.
  async function changeToFill(row: GridRow, column: GridColumn, mark: boolean) {
    try {
      const next = await markToFill(row.id, column.id, mark)
      queryClient.setQueryData<GridData>(gridQueryKey, (data) => (data ? { ...data, toFill: next } : data))
      void queryClient.invalidateQueries({ queryKey: queueQueryKey })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setFeedback({ severity: 'error', message: `Could not change the to-fill list: ${message}` })
    }
  }

  const { startEdit, gridEvents } = useCellEvents(gridRef, {
    columnsById,
    openCellDialog: (dialog) => setOpenDialog({ kind: 'cell', dialog }),
    showMenu: setMenu,
    onFeedback: setFeedback,
    onApply: apply,
  })

  // The focused cell's marks and tooltip as its description, which a screen reader reads after
  // the value. Set again when the rows or their marks change, as the grid keeps the focus.
  const describedCell = useRef<Element | null>(null)
  function describeFocusedCell() {
    const api = gridRef.current?.api
    const focused = api?.getFocusedCell()
    const data = focused ? api?.getDisplayedRowAtIndex(focused.rowIndex)?.data : undefined
    const columnId = focused?.column.getColId()
    const element =
      focused && columnId
        ? gridBox.current?.querySelector(`.ag-row[row-index="${focused.rowIndex}"] .ag-cell[col-id="${CSS.escape(columnId)}"]`)
        : null
    if (describedCell.current && describedCell.current !== element) {
      describedCell.current.removeAttribute('aria-description')
    }
    describedCell.current = element ?? null
    if (!element || !data || !columnId) {
      return
    }
    const column = columnsById.get(columnId)
    let text: string | undefined
    if (columnId === TEMPLATE_COLUMN) {
      const type = data.cells.type
      text = matchTooltip(matches.data?.[data.id], typeof type === 'string' ? type : undefined, templates, isSecretColumn)
    } else if (column && columnId !== RARE_FIELDS_COLUMN) {
      text = cellDescription(column, data, marks)
    }
    if (text) {
      element.setAttribute('aria-description', text)
    } else {
      element.removeAttribute('aria-description')
    }
  }
  useEffect(describeFocusedCell)

  const headerKeyDown = useMemo(() => headerMenuKey((columnId, anchor) => setHeaderMenu({ columnId, anchor })), [])
  const defaultColDef = useMemo<ColDef<ShownRow>>(
    () => ({ initialWidth: 160, resizable: true, sortable: true, suppressHeaderKeyboardEvent: headerKeyDown }),
    [headerKeyDown],
  )

  const getRowStyle = ({ data }: RowClassParams<ShownRow>) =>
    data && isToolNote(data) ? { backgroundColor: theme.palette.grey[mode === 'dark' ? 800 : 200] } : undefined

  function openNormalize(dialog: NormalizeState) {
    setOpenDialog({ kind: 'normalize', dialog })
  }

  // Sent without resolutions first. The server answers 409 with the items
  // that need a choice, and the same op goes again with the choices.
  async function sendMerge(op: MergeColumnsOperation) {
    let conflicts: MergeConflict[] = []
    const ok = await writer.apply([op], (error) => {
      conflicts = mergeConflictsOf(error) ?? []
      return conflicts.length > 0
    })
    if (conflicts.length > 0) {
      openNormalize({ kind: 'conflicts', op, conflicts })
    } else if (ok) {
      setMergeSelection(new Set())
    }
  }

  function startMerge(sources: string[], target: string) {
    setOpenDialog(null)
    void sendMerge(mergeColumns(rows, sources, target))
  }

  const headerActions = useMemo<HeaderActions>(
    () => ({
      nearDuplicates: nearDuplicatesByColumn(nearDuplicateGroups.data ?? []),
      openMenu: (columnId, anchor) => setHeaderMenu({ columnId, anchor }),
      mergeColumns: (columnIds) => setOpenDialog({ kind: 'normalize', dialog: { kind: 'mergeColumns', columnIds } }),
    }),
    [nearDuplicateGroups.data],
  )

  if (grid.isPending) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', mt: 12 }}>
        <CircularProgress aria-label="Loading vault" />
      </Box>
    )
  }
  if (grid.isError) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        Could not load the vault: {grid.error.message}
      </Alert>
    )
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', flexGrow: 1, minHeight: 0 }}>
      {/* Every other page starts with its name as a heading. */}
      <Typography variant="h5" component="h2" sx={VISUALLY_HIDDEN}>
        Vault
      </Typography>
      <RenamedNoteAlert doc={templatesDoc.data} onFeedback={setFeedback} />
      <GridToolbar
        selected={mergeSelection}
        onSelected={setMergeSelection}
        onMergeSelected={() => openNormalize({ kind: 'mergeColumns', columnIds: [...mergeSelection] })}
        {...gridFilters.toolbar}
        showQueued={showQueued}
        onShowQueued={setShowQueued}
        columns={chooserColumns}
        shown={visibleRows.length}
        total={rows.length}
        templateNames={templates.map((template) => template.name)}
        onNewItem={() => setOpenDialog({ kind: 'create' })}
      />
      <Box ref={gridBoxRef} sx={{ flexGrow: 1, minHeight: 300, ...gridCellStyles(theme) }}>
        <HeaderContext value={headerActions}>
          <NameSuffixContext value={suffixState}>
            <RevealersContext value={revealers}>
            <AgGridReact<ShownRow>
              ref={gridRef}
              theme={mode === 'dark' ? GRID_THEME_DARK : GRID_THEME_LIGHT}
              rowData={rowData}
              columnDefs={columnDefs}
              getRowId={getRowId}
              rowSelection={ROW_SELECTION}
              selectionColumnDef={SELECTION_COLUMN}
              onSelectionChanged={({ api }) =>
                setSelectedIds(new Set(api.getSelectedNodes().flatMap((node) => (node.data ? [node.data.id] : []))))
              }
              rowClassRules={rowClassRules}
              getRowStyle={getRowStyle}
              defaultColDef={defaultColDef}
              // About 70 columns: rendering them all keeps horizontal scrolling smooth
              // and every column reachable in tests (jsdom has no layout).
              suppressColumnVirtualisation
              tooltipShowDelay={500}
              enableCellTextSelection
              // The app queues edits with POST /api/queue (useWriter) and reloads, the grid never changes its own data.
              readOnlyEdit
              onColumnResized={handleColumnResized}
              onGridReady={({ api }) => updateOutOfView(api)}
              onDisplayedColumnsChanged={({ api }) => updateOutOfView(api)}
              onColumnMoved={({ api }) => updateOutOfView(api)}
              onGridSizeChanged={({ api }) => updateOutOfView(api)}
              onBodyScroll={handleBodyScroll}
              {...gridEvents}
              // Tab leaves the grid instead of going through its thousands of cells, the arrow keys move in it.
              tabToNextCell={() => false}
              tabToNextHeader={() => false}
              preventDefaultOnContextMenu
              stopEditingWhenCellsLoseFocus
              onCellFocused={describeFocusedCell}
              localeText={LOCALE_TEXT}
            />
            </RevealersContext>
          </NameSuffixContext>
        </HeaderContext>
      </Box>
      {selectedRows.length > 0 && (
        <SelectionBar
          count={selectedRows.length}
          onTrash={() => setOpenDialog({ kind: 'trashMany', rows: selectedRows })}
          onClear={clearSelection}
        />
      )}
      <CellMenu
        state={menu}
        onClose={() => setMenu(null)}
        onEdit={startEdit}
        onApply={apply}
        onAddField={(row) => setOpenDialog({ kind: 'cell', dialog: { kind: 'addField', row } })}
        onTrash={(row) => setOpenDialog({ kind: 'cell', dialog: { kind: 'trash', row } })}
        onConvert={(row) => setOpenDialog({ kind: 'convert', row })}
        canConvert={(row) => !isToolNote(row)}
        onMergeInto={(row) => openNormalize({ kind: 'mergeItems', row })}
        onGenerate={(column, row) => setOpenDialog({ kind: 'cell', dialog: { kind: 'generate', row, column } })}
        onGeneratorSettings={(row) => setOpenDialog({ kind: 'generator', row })}
        onRenameAttachments={(row) => setOpenDialog({ kind: 'attachments', row })}
        toFillAction={(column, row) => toFillAction(column, row, toFill)}
        onToFill={(row, column, mark) => void changeToFill(row, column, mark)}
        revealers={(row, columnId) => revealersOf(revealers, row.id, columnId)}
      />
      <HeaderMenu
        state={headerMenu}
        selected={mergeSelection}
        nearDuplicates={headerActions.nearDuplicates}
        onClose={() => setHeaderMenu(null)}
        onRename={(columnId) => {
          const column = columnsById.get(columnId)
          if (column) {
            openNormalize({ kind: 'rename', column })
          }
        }}
        onMergeInto={(columnId) =>
          openNormalize({
            kind: 'mergeColumns',
            columnIds: [columnId, ...[...mergeSelection].filter((id) => id !== columnId)],
            fixedSource: columnId,
          })
        }
        onToggleSelected={(columnId) => {
          setMergeSelection(toggled(mergeSelection, columnId, !mergeSelection.has(columnId)))
        }}
        onMergeGroup={(columnIds) => openNormalize({ kind: 'mergeColumns', columnIds })}
        onManageFolders={() => setOpenDialog({ kind: 'folders' })}
        onSetRareFieldsMax={() => setOpenDialog({ kind: 'rareFields' })}
      />
      {openDialog && (
        <PageDialog
          state={openDialog}
          rows={rows}
          columns={columns}
          organizations={grid.data?.organizations}
          folders={grid.data?.folders}
          pending={queue.data}
          templates={templates}
          matches={matches.data}
          formats={formats}
          onClose={() => setOpenDialog(null)}
          onApply={(operations) => {
            if (openDialog.kind === 'trashMany') {
              clearSelection()
            }
            setOpenDialog(null)
            apply(operations)
          }}
          onMerge={startMerge}
          onResolve={(op, resolutions) => {
            setOpenDialog(null)
            void sendMerge({ ...op, resolutions: { ...op.resolutions, ...resolutions } })
          }}
          onQueued={() => {
            setFeedback(null)
            autoCommit.clearConflicts()
          }}
        />
      )}
      <FeedbackSnackbar feedback={writer.feedback} onClose={() => setFeedback(null)} />
    </Box>
  )
}
