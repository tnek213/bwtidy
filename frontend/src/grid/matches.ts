import { FIELDS_PREFIX, fieldName, isFieldColumn } from '../shared/coordinates.ts'
import type { GridRow } from '../api/grid.ts'
import type { Operation } from '../api/operations.ts'
import type { FormatScore, Template, TemplateCoordinate, TemplateMatch, TemplateScore } from '../api/templates.ts'
import { coordinateColumn, foldedName, isSecretCoordinate, ITEM_TYPES } from '../shared/templateRules.ts'
import { isMasked } from '../shared/values.ts'
import { hasValue } from './columnValues.ts'
import { NONE } from './filters.ts'
import { columnHeaderLabel } from './labels.ts'

// Whether a field name fits the pattern, the whole name, ignoring case and surrounding spaces.
// The same rule as glob_matches of the backend.
export function fitsPattern(pattern: string, name: string): boolean {
  const text = foldedName(name)
  const [first, ...rest] = foldedName(pattern).split('*')
  const last = rest.length > 0 ? rest.pop()! : null
  if (last === null) {
    return text === first
  }
  if (text.length < first.length + last.length || !text.startsWith(first) || !text.endsWith(last)) {
    return false
  }
  let at = first.length
  const end = text.length - last.length
  for (const part of rest) {
    const found = text.indexOf(part, at)
    if (found < 0 || found + part.length > end) {
      return false
    }
    at = found + part.length
  }
  return true
}

// The text of a pattern before its first *, which a new field's name starts from.
export function patternPrefix(pattern: string): string {
  const text = pattern.trim()
  const star = text.indexOf('*')
  return star < 0 ? text : text.slice(0, star)
}

// The coordinate of the template a grid column belongs to: its own, or else the first pattern the
// field's name fits, unless a field coordinate of the template names it.
export function coordinateOfColumn(template: Template | undefined, column: string): TemplateCoordinate | undefined {
  const coordinates = template?.coordinates ?? []
  const own = coordinates.find((coordinate) => coordinate.kind !== 'fieldPattern' && coordinateColumn(coordinate) === column)
  if (own !== undefined || !isFieldColumn(column)) {
    return own
  }
  const name = foldedName(fieldName(column))
  if (coordinates.some((coordinate) => coordinate.kind === 'field' && foldedName(coordinate.name) === name)) {
    return undefined
  }
  return coordinates.find((coordinate) => coordinate.kind === 'fieldPattern' && fitsPattern(coordinate.pattern, name))
}

function isFullMatch(score: TemplateScore): boolean {
  return score.typeMatches && score.filled === score.total
}

// The best near miss: at least 2 matched (the type counts as one), not a full
// match, the highest score first and a matching type first among equals.
function bestNearMiss(match: TemplateMatch | undefined): TemplateScore | null {
  let best: TemplateScore | null = null
  for (const score of match?.scores ?? []) {
    if (score.filled < 2 || isFullMatch(score)) {
      continue
    }
    if (
      best === null ||
      score.score > best.score ||
      (score.score === best.score && score.typeMatches && !best.typeMatches)
    ) {
      best = score
    }
  }
  return best
}

// The score the row's template column shows: its match, or else its best near miss.
export function shownScore(match: TemplateMatch | undefined): TemplateScore | null {
  if (match?.match) {
    return match.scores.find((score) => score.template === match.match) ?? null
  }
  return bestNearMiss(match)
}

// What the template column sorts by: a match first when descending, a row without a template last.
export function matchOrder(match: TemplateMatch | undefined): number {
  return match?.match ? 1 : (bestNearMiss(match)?.score ?? -1)
}

function fillNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

export function fillLabel(score: TemplateScore): string {
  return `${fillNumber(score.filled)}/${fillNumber(score.total)}`
}

// What the grid's template column shows: the match, or the best near miss with its fill.
export function matchLabel(match: TemplateMatch | undefined): string {
  if (match?.match) {
    return match.match
  }
  const near = bestNearMiss(match)
  return near === null ? '' : `${near.template} ${fillLabel(near)}`
}

// What a near miss lacks: the item type when it is not the template's, then
// every missing column named like the grid's column header.
export function missingList(score: TemplateScore, itemType: string | undefined, template: Template | undefined): string[] {
  const patterns = new Set(
    (template?.coordinates ?? []).filter((coordinate) => coordinate.kind === 'fieldPattern').map(coordinateColumn),
  )
  const lines = score.missing.map((column) =>
    patterns.has(column) ? `a field named like ${columnHeaderLabel(column)}` : columnHeaderLabel(column),
  )
  if (!score.typeMatches) {
    const wanted = template ? (ITEM_TYPES[template.type] ?? `type ${template.type}`) : 'another type'
    lines.unshift(`Type: ${itemType ?? 'none'}, the template wants ${wanted}`)
  }
  return lines
}

export function formatPercent(score: number): string {
  // score has two decimals, and 0.29 * 100 is a hair below 29 in binary
  return `matches format ${Math.round(score * 100)}%`
}

// How a stored value matches, for a hover text. A secret column gets the numbers and positions only.
function formatDetail(format: FormatScore, secret: boolean): string {
  if (format.tooLong) {
    return 'too long to check'
  }
  if (format.score === 1) {
    return 'matches format'
  }
  const percent = formatPercent(format.score)
  const { distance, firstEdit, expected } = format
  if (distance === null || firstEdit === null) {
    return percent
  }
  const position = firstEdit + 1
  const changes = distance === 1 ? `1 change at position ${position}` : `${distance} changes, the first at position ${position}`
  return `${percent} (${changes}${!secret && expected ? `, expected ${expected}` : ''})`
}

export function formatLine(format: FormatScore, secret: boolean): string {
  return `${columnHeaderLabel(format.column)}: ${formatDetail(format, secret)}`
}

// Whether a column of the template is secret, by the grid's column or by the coordinate.
export function isSecretColumnOf(template: Template | undefined, column: string, isSecretColumn: (column: string) => boolean): boolean {
  return isSecretColumn(column) || (template?.coordinates.some((c) => coordinateColumn(c) === column && isSecretCoordinate(c)) ?? false)
}

// The tooltip of the grid's template cell: the match, or for a near miss a bullet list of what it
// lacks and of every format below 1. A column without a value is a missing bullet already. The
// template's description, when it has one, comes under the heading.
export function matchTooltip(
  match: TemplateMatch | undefined,
  itemType: string | undefined,
  templates: readonly Template[],
  isSecretColumn: (column: string) => boolean = () => false,
): string | undefined {
  if (match?.match) {
    const description = templates.find((candidate) => candidate.name === match.match)?.description
    return description ? `${match.match}\n${description}` : match.match
  }
  const near = bestNearMiss(match)
  if (near === null) {
    return undefined
  }
  const template = templates.find((candidate) => candidate.name === near.template)
  const lacking = missingList(near, itemType, template)
  const off = (near.formats ?? [])
    .filter((format) => format.score < 1 && !near.missing.includes(format.column))
    .map((format) => formatLine(format, isSecretColumnOf(template, format.column, isSecretColumn)))
  const heading = lacking.length > 0 ? `Missing for ${near.template}:` : `Off format for ${near.template}:`
  const description = template?.description ? [template.description] : []
  return [heading, ...description, ...[...lacking, ...off].map((line) => `\u2022 ${line}`)].join('\n')
}

// Empty means no filter. A template name filters its matches, NEAR_PREFIX plus
// a name its near misses, NONE the items without a match.
export const NEAR_PREFIX = '\u0000near:'

export function matchesTemplateFilter(match: TemplateMatch | undefined, filter: string): boolean {
  if (filter === '') {
    return true
  }
  if (filter === NONE) {
    return !match?.match
  }
  if (filter.startsWith(NEAR_PREFIX)) {
    return !match?.match && bestNearMiss(match)?.template === filter.slice(NEAR_PREFIX.length)
  }
  return match?.match === filter
}

export const FORMAT_OFF = 'off'

export const FORMAT_PARTIAL = 'partial'

export const FORMAT_FILTER_LABELS: Record<string, string> = { [FORMAT_OFF]: 'Off their format', [FORMAT_PARTIAL]: 'Partial' }

// By the formats of the template the row's template column shows. Empty means no filter.
export function matchesFormatFilter(match: TemplateMatch | undefined, filter: string): boolean {
  if (filter === '') {
    return true
  }
  const formats = shownScore(match)?.formats ?? []
  return formats.some((format) => format.score < 1 && (filter === FORMAT_OFF || format.score > 0))
}

// Columns that are not values an item could hand to a template.
const NOT_SOURCES = new Set([
  'name',
  'type',
  'folder',
  'organization',
  'collections',
  'favorite',
  'reprompt',
  'attachments',
  'passkeys',
  'revisionDate',
])

export interface SourceColumn {
  column: string
  secret: boolean
}

export function sourceColumns(row: GridRow): SourceColumn[] {
  return Object.entries(row.cells)
    .filter(([column, value]) => !NOT_SOURCES.has(column) && hasValue(value))
    .map(([column, value]) => ({ column, secret: isMasked(value) }))
}

// The comparable name of a column: a custom field's name, or a built-in leaf's key.
function plainName(column: string): string {
  const name = isFieldColumn(column) ? column.slice(FIELDS_PREFIX.length) : column.slice(column.lastIndexOf('.') + 1)
  return name.trim().toLocaleLowerCase()
}

export type Mapping = Record<string, string>

// Whether a source column can go into a coordinate: any value into a column, only a field whose
// name fits it into a pattern, which then keeps its name.
export function canMapOnto(coordinate: TemplateCoordinate, source: string): boolean {
  return coordinate.kind !== 'fieldPattern' || (isFieldColumn(source) && fitsPattern(coordinate.pattern, fieldName(source)))
}

// Each template coordinate takes the item column with the same id, else the
// first unused one whose name is equal after case folding. A pattern takes the first unused field
// whose name fits it and that no field coordinate of the template names.
export function defaultMapping(template: Template, sources: SourceColumn[]): Mapping {
  const mapping: Mapping = {}
  const used = new Set<string>()
  const exact = template.coordinates.filter((coordinate) => coordinate.kind !== 'fieldPattern')
  const named = new Set(exact.filter((coordinate) => coordinate.kind === 'field').map((coordinate) => foldedName(coordinate.name)))
  const targets = exact.map(coordinateColumn)
  for (const target of targets) {
    if (sources.some(({ column }) => column === target)) {
      mapping[target] = target
      used.add(target)
    }
  }
  for (const target of targets) {
    if (mapping[target] !== undefined) {
      continue
    }
    const found = sources.find(({ column }) => !used.has(column) && plainName(column) === plainName(target))
    if (found) {
      mapping[target] = found.column
      used.add(found.column)
    }
  }
  for (const coordinate of template.coordinates) {
    if (coordinate.kind !== 'fieldPattern') {
      continue
    }
    const found = sources.find(
      ({ column }) =>
        !used.has(column) && canMapOnto(coordinate, column) && !named.has(foldedName(fieldName(column))),
    )
    if (found) {
      mapping[coordinateColumn(coordinate)] = found.column
      used.add(found.column)
    }
  }
  return mapping
}

// A coordinate mapped onto itself changes nothing and is left out of the request, and so is a
// pattern, whose field keeps its name.
export function applyTemplate(row: GridRow, template: Template, mapping: Mapping): Operation {
  const patterns = new Set(
    template.coordinates.filter((coordinate) => coordinate.kind === 'fieldPattern').map(coordinateColumn),
  )
  const sent: Mapping = {}
  for (const [target, source] of Object.entries(mapping)) {
    if (source !== '' && source !== target && !patterns.has(target)) {
      sent[target] = source
    }
  }
  return { kind: 'applyTemplate', itemId: row.id, revisionDate: row.revisionDate, template: template.name, mapping: sent }
}
