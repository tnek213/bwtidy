import { useMemo, useState } from 'react'
import type { TemplateMatches } from '../api/templates.ts'
import type { GridRow } from '../api/grid.ts'
import type { ToolbarProps } from './GridToolbar.tsx'
import { ownGeneratorCount, ownGeneratorRows } from './ownGenerator.ts'
import { type ToFill, toFillCount, toFillRows } from './toFill.ts'
import { matchesFormatFilter, matchesTemplateFilter } from './matches.ts'
import { filterOptions, filterRows, type Filters, noFilters, searchWords } from './filters.ts'

type FilterToolbarProps = Pick<
  ToolbarProps,
  | 'filters'
  | 'options'
  | 'onFilters'
  | 'hideEmpty'
  | 'onHideEmpty'
  | 'hideRare'
  | 'onHideRare'
  | 'rareOff'
  | 'hidden'
  | 'onHidden'
  | 'templateFilter'
  | 'onTemplateFilter'
  | 'formatFilter'
  | 'onFormatFilter'
  | 'toFillCount'
  | 'toFillOnly'
  | 'onToFillOnly'
  | 'ownGeneratorCount'
  | 'ownGeneratorOnly'
  | 'onOwnGeneratorOnly'
>

interface GridFilters {
  // The rows every filter keeps, with their vault values (the queued overlay comes after).
  visibleRows: GridRow[]
  hideEmpty: boolean
  // Hide rare field columns, never while Rare fields is off.
  hideRare: boolean
  // The columns hidden in the column chooser.
  hidden: ReadonlySet<string>
  searchWords: string[]
  toolbar: FilterToolbarProps
}

// Owns the grid page's filters: the toolbar's filters and search text, the template and format
// filters, the To fill in and Own generator settings chips, Hide empty columns, Hide rare field
// columns and the columns hidden in the column chooser. It filters the rows by them and hands the
// toolbar their values and setters. rareMax is the N of Rare fields, 0 until it has loaded.
export function useGridFilters(
  rows: GridRow[],
  matches: TemplateMatches | undefined,
  toFill: ToFill,
  rareMax: number,
): GridFilters {
  const [filters, setFilters] = useState<Filters>(noFilters)
  const [hideEmpty, setHideEmpty] = useState(false)
  const [hideRareChoice, setHideRareChoice] = useState(false)
  const [lastRareMax, setLastRareMax] = useState(rareMax)
  // Turned on when Rare fields goes from off to on, which is also the page load with N above 0
  // (N is 0 until the setting has loaded). Not saved, like Hide empty columns.
  if (rareMax !== lastRareMax) {
    setLastRareMax(rareMax)
    if (lastRareMax === 0 && rareMax > 0) {
      setHideRareChoice(true)
    }
  }
  const rareOff = rareMax <= 0
  const hideRare = hideRareChoice && !rareOff
  const [hidden, setHidden] = useState<Set<string>>(() => new Set())
  const [templateFilter, setTemplateFilter] = useState('')
  const [formatFilter, setFormatFilter] = useState('')
  const [toFillFilter, setToFillFilter] = useState(false)
  const [ownGeneratorFilter, setOwnGeneratorFilter] = useState(false)
  const toFillTotal = toFillCount(toFill)
  // A chip's filter goes with the chip once nothing is left to count.
  const toFillOnly = toFillFilter && toFillTotal > 0
  const ownGeneratorTotal = useMemo(() => ownGeneratorCount(rows), [rows])
  const ownGeneratorOnly = ownGeneratorFilter && ownGeneratorTotal > 0
  const options = useMemo(() => filterOptions(rows), [rows])
  const visibleRows = useMemo(() => {
    const filtered = filterRows(rows, filters).filter(
      (row) => matchesTemplateFilter(matches?.[row.id], templateFilter) && matchesFormatFilter(matches?.[row.id], formatFilter),
    )
    const toFillShown = toFillOnly ? toFillRows(filtered, toFill) : filtered
    return ownGeneratorOnly ? ownGeneratorRows(toFillShown) : toFillShown
  }, [rows, filters, matches, templateFilter, formatFilter, toFillOnly, toFill, ownGeneratorOnly])
  const words = useMemo(() => searchWords(filters.text), [filters.text])
  return {
    visibleRows,
    hideEmpty,
    hideRare,
    hidden,
    searchWords: words,
    toolbar: {
      filters,
      options,
      onFilters: setFilters,
      hideEmpty,
      onHideEmpty: setHideEmpty,
      hideRare,
      onHideRare: setHideRareChoice,
      rareOff,
      hidden,
      onHidden: setHidden,
      templateFilter,
      onTemplateFilter: setTemplateFilter,
      formatFilter,
      onFormatFilter: setFormatFilter,
      toFillCount: toFillTotal,
      toFillOnly,
      onToFillOnly: setToFillFilter,
      ownGeneratorCount: ownGeneratorTotal,
      ownGeneratorOnly,
      onOwnGeneratorOnly: setOwnGeneratorFilter,
    },
  }
}
