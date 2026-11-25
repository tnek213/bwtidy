import type { Theme } from '@mui/material'
import { canGenerate } from './editing.ts'
import type { GeneratorOptions } from '../api/generator.ts'
import type { GridColumn, GridRow } from '../api/grid.ts'
import type { ShownRow } from './queued.ts'
import { isMasked } from '../shared/values.ts'

export const OWN_GENERATOR_CLASS = 'bw-own-generator-cell'

export const OWN_GENERATOR_TOOLTIP = 'Own generator setting'

export function ownGeneratorCount(rows: GridRow[]): number {
  return rows.filter((row) => row.ownGenerator === true).length
}

export function ownGeneratorRows<Row extends GridRow>(rows: Row[]): Row[] {
  return rows.filter((row) => row.ownGenerator === true)
}

// The cells the cell menu offers "Generator settings for this item" on, of an item that has its own
// setting, where the item has the secret itself (the menu also offers it on a column the item has no cell in).
export function hasOwnGeneratorMark(column: GridColumn, row: ShownRow): boolean {
  return (
    row.ownGenerator === true &&
    canGenerate(column, row) &&
    (isMasked(row.cells[column.id]) || (row.emptySecrets ?? []).includes(column.id))
  )
}

// Material "tune", drawn as a background image so the cell's pseudo elements stay with the format and plain-value marks.
const TUNE_PATH =
  'M3 17v2h6v-2zM3 5v2h10V5zm10 16v-2h8v-2h-8v-2h-2v6zM7 9v2H3v2h4v2h2V9zm14 4v-2H11v2zm-6-4h2V7h4V5h-4V3h-2z'

// A small icon at the bottom left in the theme's secondary text color, so it follows the color mode.
export const ownGeneratorMarkStyles = ({ palette }: Theme) => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="${palette.text.secondary}" d="${TUNE_PATH}"/></svg>`
  return {
    [`& .${OWN_GENERATOR_CLASS}`]: {
      backgroundImage: `url("data:image/svg+xml,${encodeURIComponent(svg)}")`,
      backgroundRepeat: 'no-repeat',
      backgroundPosition: 'left 3px bottom 2px',
      backgroundSize: '10px 10px',
    },
  }
}

// A few words for the New item dialog.
export function describeOptions(options: GeneratorOptions): string {
  return options.type === 'password' ? `${options.length} characters` : `${options.words} words`
}
