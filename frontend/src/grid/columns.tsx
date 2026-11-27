import {
  CellStyleModule,
  CheckboxEditorModule,
  ColumnAutoSizeModule,
  type ColDef,
  type ColGroupDef,
  ClientSideRowModelModule,
  ColumnApiModule,
  EventApiModule,
  type ITooltipParams,
  RowApiModule,
  RowSelectionModule,
  RowStyleModule,
  LargeTextEditorModule,
  LocaleModule,
  ModuleRegistry,
  RenderApiModule,
  ScrollApiModule,
  TextEditorModule,
  colorSchemeDark,
  colorSchemeLight,
  themeQuartz,
  TooltipModule,
  ValidationModule,
} from 'ag-grid-community'
import { type CellMarks, FAILED_CELL_CLASS } from './cellMarks.ts'
import { ChipsCell, MaskedCell, NameCell, RareFieldsCell, type RareFieldsCellParams, SecretCell } from './cells.tsx'
import { FieldHeader } from './ColumnHeader.tsx'
import { CONFLICT_CELL_CLASS } from '../shared/conflicts.ts'
import {
  editBlocker,
  editKind,
  isBooleanColumn,
  isInline,
  PLAIN_IN_SECRET_CLASS,
  PLAIN_IN_SECRET_TOOLTIP,
  plainInSecretColumn,
  unavailableReason,
} from './editing.ts'
import { hasOwnGeneratorMark, OWN_GENERATOR_CLASS, OWN_GENERATOR_TOOLTIP } from './ownGenerator.ts'
import { FORMAT_CLASSES } from './formats.ts'
import { GROUP_ORDER, isFieldColumn } from '../shared/coordinates.ts'
import { FormatCellEditor, type FormatEditorParams } from './FormatEditor.tsx'
import { type CellValue, FOLDER_COLUMN, type GridColumn, NAME_COLUMN, TEMPLATE_COLUMN } from '../api/grid.ts'
import { SUFFIX_CLASS } from './nameSuffix.tsx'
import { RARE_FIELDS_COLUMN, rareEntries, rareFieldsHeader, rareMatchesSearch, rareText } from './rareFields.ts'
import { QUEUED_CELL_CLASS, queuedCell, queuedTooltip, shownValue, type ShownRow } from './queued.ts'
import { TO_FILL_CELL_CLASS, TO_FILL_TOOLTIP } from './toFill.ts'
import type { Template, TemplateMatches } from '../api/templates.ts'
import { TYPE_LABELS } from '../shared/templateRules.ts'
import { matchLabel, matchOrder, matchTooltip } from './matches.ts'
import { formatValue, isMasked } from '../shared/values.ts'
import { columnTooltip, groupHeaderLabel, shortColumnLabel } from './labels.ts'
import { compareValues } from './columnValues.ts'
import { cellMatchesSearch } from './filters.ts'

ModuleRegistry.registerModules([
  ClientSideRowModelModule,
  CellStyleModule,
  RowStyleModule,
  // The checkbox column, for moving several items to the trash.
  RowSelectionModule,
  // A double click on a column edge fits the column to its content.
  ColumnAutoSizeModule,
  TooltipModule,
  TextEditorModule,
  LargeTextEditorModule,
  CheckboxEditorModule,
  // Names the checkbox column for a screen reader (localeText in GridPage).
  LocaleModule,
  RowApiModule,
  RenderApiModule,
  ColumnApiModule,
  // A revealed secret cell listens to bodyScroll and hides when its row leaves the viewport.
  EventApiModule,
  // Tells the name cell which columns the horizontal scroll hides.
  ScrollApiModule,
  // Explains missing modules and bad options in dev and tests, left out of the production bundle.
  ...(import.meta.env.DEV ? [ValidationModule] : []),
])

type Col = ColDef<ShownRow, CellValue>

const NO_COUNTS: ReadonlyMap<string, number> = new Map()

// A cell that cannot hold a value, its tooltip says why. Subtle, it is no error.
export const GRID_THEME_LIGHT = themeQuartz.withPart(colorSchemeLight)
export const GRID_THEME_DARK = themeQuartz.withPart(colorSchemeDark)

export const UNAVAILABLE_CELL = 'bw-unavailable-cell'

export const SEARCH_HIT_CLASS = 'bw-search-hit'

// The value stays in the hover text of a plain cell, as it is the only way to read a long one.
function valueTooltip(marks: CellMarks, row: ShownRow, column: GridColumn, value: string | null | undefined): string | undefined {
  const plainInSecret = plainInSecretColumn(column, row)
  const lines = [
    !column.secret || plainInSecret ? value : undefined,
    marks.formatOf(row.id, column.id)?.hover,
    plainInSecret ? PLAIN_IN_SECRET_TOOLTIP : undefined,
  ].filter((line) => line)
  return lines.length > 0 ? lines.join('\n') : undefined
}

// A column only a queued change has gets no field header menu: no item has it to rename or merge.
// The counts are how many items of the vault have a value per column, for the header tooltip.
export function columnDef(
  column: GridColumn,
  hide: boolean,
  marks: CellMarks,
  queuedOnly = false,
  counts: ReadonlyMap<string, number> = NO_COUNTS,
): Col {
  // A result is about the vault's value, so a queued one has no mark, nor a cell that cannot hold a value.
  const formatLevel = (data: ShownRow | undefined) =>
    data && !queuedCell(data, column.id) && unavailableReason(column, data) === null
      ? marks.formatOf(data.id, column.id)?.level
      : undefined
  const def: Col = {
    colId: column.id,
    headerName: shortColumnLabel(column),
    headerTooltip: columnTooltip(column.id, counts.get(column.id) ?? 0),
    hide,
    // Coordinates contain dots, which AG Grid would read as a nested path in `field`.
    valueGetter: ({ data }) => shownValue(data, column.id),
    valueFormatter: ({ value }) => formatValue(value ?? undefined),
    comparator: (a, b) => compareValues(a ?? undefined, b ?? undefined),
    cellClassRules: {
      [QUEUED_CELL_CLASS]: ({ data }) => queuedCell(data, column.id) !== undefined,
      [CONFLICT_CELL_CLASS]: ({ data }) => data !== undefined && marks.isConflictCell(data.id, column.id),
      [FAILED_CELL_CLASS]: ({ data }) => data !== undefined && marks.isFailedCell(data.id, column.id),
      [TO_FILL_CELL_CLASS]: ({ data }) => data !== undefined && marks.isToFillCell(data.id, column.id),
      // The vault value, like the search filter, not a queued one.
      [SEARCH_HIT_CLASS]: ({ data }) => data !== undefined && cellMatchesSearch(data.cells[column.id], marks.searchWords()),
      [UNAVAILABLE_CELL]: ({ data }) => data !== undefined && unavailableReason(column, data) !== null,
      [PLAIN_IN_SECRET_CLASS]: ({ data }) => data !== undefined && plainInSecretColumn(column, data),
      [OWN_GENERATOR_CLASS]: ({ data }) => data !== undefined && hasOwnGeneratorMark(column, data),
      [FORMAT_CLASSES.full]: ({ data }) => formatLevel(data) === 'full',
      [FORMAT_CLASSES.partial]: ({ data }) => formatLevel(data) === 'partial',
      [FORMAT_CLASSES.none]: ({ data }) => formatLevel(data) === 'none',
    },
    tooltip: ({ data, valueFormatted }) => {
      const text =
        queuedTooltip(data, column.id, marks.commitNote()) ??
        (data && marks.isToFillCell(data.id, column.id) ? TO_FILL_TOOLTIP : null) ??
        (data ? unavailableReason(column, data) : null) ??
        (data ? valueTooltip(marks, data, column, valueFormatted) : undefined)
      // Last, so it adds to what the cell says about its value instead of replacing it.
      return data && hasOwnGeneratorMark(column, data) ? [text, OWN_GENERATOR_TOOLTIP].filter(Boolean).join('\n') : text
    },
  }
  if (column.id === NAME_COLUMN) {
    const tooltip = def.tooltip as (params: ITooltipParams<ShownRow, CellValue>) => string | undefined
    // AG Grid reads the tooltip when it shows, so a suffix with its own hover text keeps the cell's tooltip away.
    def.tooltip = (params) => (document.querySelector(`.${SUFFIX_CLASS}:hover`) ? undefined : tooltip(params))
    def.cellRenderer = NameCell
  }
  const kind = editKind(column)
  if (isInline(kind)) {
    def.editable = ({ data }) => data !== undefined && editBlocker(column, data) === null
    if (kind === 'longText') {
      def.cellEditor = 'agLargeTextCellEditor'
      def.cellEditorPopup = true
      def.cellEditorParams = { maxLength: 100_000, rows: 12, cols: 60 }
    } else if (kind === 'boolean') {
      def.cellEditor = 'agCheckboxCellEditor'
    } else {
      def.cellEditor = 'agTextCellEditor'
    }
    if (kind === 'text' || kind === 'longText') {
      // A popup, as the live text goes under the input and the row's height has no room for it.
      def.cellEditorSelector = ({ data }) => {
        const datatypeId = data && marks.formatOf(data.id, column.id)?.datatypeId
        const params: FormatEditorParams | undefined = datatypeId ? { datatypeId, multiline: kind === 'longText' } : undefined
        return params && { component: FormatCellEditor, popup: true, params }
      }
    }
  }
  if (isBooleanColumn(column)) {
    // The checkbox editor needs false, not a missing value.
    def.valueGetter = ({ data }) => shownValue(data, column.id) === true
  }
  if (column.id === 'type') {
    def.valueFormatter = ({ value }) => (typeof value === 'string' ? (TYPE_LABELS[value] ?? value) : '')
  }
  if (column.secret) {
    // A queued value is masked and not in the vault yet, so there is nothing to reveal. A text
    // field's value is plain and the secret route does not answer it.
    def.cellRendererSelector = ({ data, value }) => {
      if (queuedCell(data, column.id)) {
        return isMasked(value) ? { component: MaskedCell } : undefined
      }
      return data && plainInSecretColumn(column, data) ? undefined : { component: SecretCell }
    }
    def.initialWidth = 180
  } else if (column.multi) {
    def.cellRenderer = ChipsCell
    def.initialWidth = 240
  }
  // The folder column's menu has Manage folders.
  if ((isFieldColumn(column.id) || column.id === FOLDER_COLUMN) && !queuedOnly) {
    def.headerComponentParams = { innerHeaderComponent: FieldHeader }
  }
  if (column.id === NAME_COLUMN) {
    def.pinned = 'left'
    def.lockPinned = true
    def.initialWidth = 240
    def.initialSort = 'asc'
  }
  return def
}

// The column chooser lists the template column with the item columns, though it is no coordinate.
export const TEMPLATE_CHOOSER_COLUMN: GridColumn = {
  id: TEMPLATE_COLUMN,
  label: TEMPLATE_COLUMN,
  group: 'item',
  secret: false,
  multi: false,
  readOnly: true,
}

export function templateColumnDef(
  matches: TemplateMatches | undefined,
  templates: readonly Template[],
  hide: boolean,
  isSecretColumn: (column: string) => boolean,
): ColDef<ShownRow, string> {
  return {
    colId: TEMPLATE_COLUMN,
    headerName: 'template',
    hide,
    headerTooltip: 'The matching template, or the closest one with how many of its type and coordinates the item has',
    pinned: 'left',
    lockPinned: true,
    initialWidth: 180,
    tooltip: ({ data }) => {
      const type = data?.cells.type
      return data
        ? matchTooltip(matches?.[data.id], typeof type === 'string' ? type : undefined, templates, isSecretColumn)
        : undefined
    },
    valueGetter: ({ data }) => (data ? matchLabel(matches?.[data.id]) : ''),
    comparator: (a, b, nodeA, nodeB) => {
      const order = (node: typeof nodeA) => (node.data ? matchOrder(matches?.[node.data.id]) : -1)
      return order(nodeA) - order(nodeB) || (a ?? '').localeCompare(b ?? '')
    },
  }
}

// The Rare fields column: read-only (no editor, the cell menu has the Row group only), from the
// vault values like the search, sorted by its text. Its header menu sets N.
export function rareFieldsColumnDef(
  rareColumns: readonly GridColumn[],
  max: number,
  marks: CellMarks,
): ColDef<ShownRow, string> {
  const params: RareFieldsCellParams = { rareColumns }
  const text = (data: ShownRow | undefined) => (data ? rareText(rareEntries(data, rareColumns)) : '')
  return {
    colId: RARE_FIELDS_COLUMN,
    headerName: rareFieldsHeader(max),
    headerTooltip: 'The custom fields and built-in slots at most N items use, as JSON. Set N in this menu.',
    headerComponentParams: { innerHeaderComponent: FieldHeader },
    initialWidth: 360,
    valueGetter: ({ data }) => text(data),
    comparator: (a, b) => (a ?? '').localeCompare(b ?? '', undefined, { sensitivity: 'base', numeric: true }),
    tooltip: ({ value }) => (typeof value === 'string' && value !== '' ? value : undefined),
    cellRenderer: RareFieldsCell,
    cellRendererParams: params,
    cellClassRules: {
      [SEARCH_HIT_CLASS]: ({ data }) => data !== undefined && rareMatchesSearch(rareEntries(data, rareColumns), marks.searchWords()),
    },
  }
}

export function buildColumnDefs(
  columns: GridColumn[],
  isHidden: (id: string) => boolean,
  marks: CellMarks,
  templateColumn: ColDef<ShownRow, string>,
  queuedOnly: ReadonlySet<string>,
  counts: ReadonlyMap<string, number>,
  rareFieldsColumn: ColDef<ShownRow, string>,
): (Col | ColDef<ShownRow, string> | ColGroupDef<ShownRow>)[] {
  const name = columns.find((column) => column.id === NAME_COLUMN)
  const defs: (Col | ColDef<ShownRow, string> | ColGroupDef<ShownRow>)[] = name
    ? [columnDef(name, false, marks, false, counts)]
    : []
  defs.push(templateColumn)
  for (const group of GROUP_ORDER) {
    const children = columns
      .filter((column) => column.group === group && column.id !== NAME_COLUMN)
      .map((column) => columnDef(column, isHidden(column.id), marks, queuedOnly.has(column.id), counts))
    if (children.length > 0) {
      defs.push({ groupId: group, headerName: groupHeaderLabel(group), children })
    }
  }
  defs.push(rareFieldsColumn)
  return defs
}
