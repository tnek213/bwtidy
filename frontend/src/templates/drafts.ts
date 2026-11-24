import type { Datatype, DatatypeChange, DatatypeDraft, StoredDatatype } from '../api/datatypes.ts'
import type { VaultField } from '../api/grid.ts'
import type { Template, TemplateChange, TemplateCoordinate } from '../api/templates.ts'
import { coordinateColumn, foldedName } from '../shared/templateRules.ts'

// What tells coordinates apart: the column, and a pattern by its text ignoring case, never the
// same as a field of that very name.
export function coordinateKey(coordinate: TemplateCoordinate): string {
  return coordinate.kind === 'fieldPattern' ? `\u0000pattern:${foldedName(coordinate.pattern)}` : coordinateColumn(coordinate)
}

export const PATTERN_HINT = '* matches any characters, for example user_*'

const PATTERN_RULE = 'A pattern needs a * and some other character.'

function isValidPattern(pattern: string): boolean {
  const text = pattern.trim()
  return text.includes('*') && text.replaceAll('*', '').trim() !== ''
}

// Text coordinates can, a boolean field cannot.
export function canTakeDatatype(coordinate: TemplateCoordinate): boolean {
  return coordinate.kind === 'path' || coordinate.fieldType !== 2
}

export function withOptional(coordinate: TemplateCoordinate, optional: boolean): TemplateCoordinate {
  const next = { ...coordinate }
  if (optional) {
    next.optional = true
  } else {
    delete next.optional
  }
  return next
}

export function withDatatype(coordinate: TemplateCoordinate, datatype: string | undefined): TemplateCoordinate {
  const next = { ...coordinate }
  if (datatype === undefined) {
    delete next.datatype
  } else {
    next.datatype = datatype
  }
  return next
}

// A free text field name equal to a vault field after case folding and without
// any whitespace is most likely that field misspelled.
function nearFieldKey(name: string): string {
  return name.replace(/\s+/g, '').toLocaleLowerCase()
}

// The vault's fields a typed name is near equal to (its exact name included).
export function nearFields(name: string, fields: readonly VaultField[]): VaultField[] {
  const key = nearFieldKey(name)
  return key === '' ? [] : fields.filter((field) => nearFieldKey(field.name) === key)
}

// A custom field coordinate that no item of the vault has. Built-in leaves
// always exist, and without the vault's fields nothing is unknown.
export function unknownField(coordinate: TemplateCoordinate, fields: readonly VaultField[] | undefined): boolean {
  if (coordinate.kind !== 'field' || coordinate.name === '' || fields === undefined) {
    return false
  }
  return !fields.some((field) => field.name === coordinate.name)
}

export const UNKNOWN_FIELD = 'No item has this field'

// A coordinate row with nothing picked yet.
export const EMPTY_FIELD: TemplateCoordinate = { kind: 'field', name: '', fieldType: 0 }

// What saving a template changes: an edit that renames it renames first, then sets it under the new name.
export function templateChanges(storedName: string | null, template: Template): TemplateChange[] {
  if (storedName === null) {
    return [{ op: 'addTemplate', template }]
  }
  const set: TemplateChange = { op: 'setTemplate', template }
  return storedName === template.name ? [set] : [{ op: 'renameTemplate', name: storedName, to: template.name }, set]
}

export function coordinatesUsing(templates: readonly Template[], id: string): number {
  return templates.flatMap((template) => template.coordinates).filter((coordinate) => coordinate.datatype === id).length
}

export function templatesUsing(templates: readonly Template[], id: string): string[] {
  return templates
    .filter((template) => template.coordinates.some((coordinate) => coordinate.datatype === id))
    .map((template) => template.name)
}

// What scores alike: the type and the set of (column, datatype, optional), a pattern by its text
// ignoring case. An unknown or broken datatype counts as none, and the order and the field types
// change nothing.
function scoreKey(template: Template, datatypes: readonly Datatype[]): string {
  const usable = new Set(datatypes.filter((datatype) => datatype.broken === null).map((datatype) => datatype.id))
  const terms = template.coordinates.map((coordinate) =>
    JSON.stringify([
      coordinateKey(coordinate),
      coordinate.datatype !== undefined && usable.has(coordinate.datatype) ? coordinate.datatype : null,
      coordinate.optional === true,
    ]),
  )
  return JSON.stringify([template.type, [...new Set(terms)].sort()])
}

function duplicateWarning(later: string, earlier: string): string {
  return `${later}: same type and fields as ${earlier}, which is listed first and takes every match.`
}

// One pair per template that scores like one listed before it, the first of them named as earlier.
// The same rule as duplicate_warnings of the backend.
export function duplicatePairs(
  templates: readonly Template[],
  datatypes: readonly Datatype[],
): { later: string; earlier: string }[] {
  const first = new Map<string, string>()
  const pairs: { later: string; earlier: string }[] = []
  for (const template of templates) {
    const key = scoreKey(template, datatypes)
    const earlier = first.get(key)
    if (earlier === undefined) {
      first.set(key, template.name)
    } else if (earlier !== template.name) {
      pairs.push({ later: template.name, earlier })
    }
  }
  return pairs
}

// The warnings of the template being edited, before it is saved: the draft sits where it will be
// listed (position in others, the end for a new one). They name the draft as the later template
// when it copies an earlier one, and as the earlier one when a later template then copies it.
export function draftDuplicateWarnings(
  typed: Template,
  others: readonly Template[],
  position: number,
  datatypes: readonly Datatype[],
): string[] {
  const draft = { ...typed, name: typed.name.trim() }
  if (draft.name === '') {
    return []
  }
  const listed = [...others.slice(0, position), draft, ...others.slice(position)]
  return duplicatePairs(listed, datatypes)
    .filter((pair) => pair.later === draft.name || pair.earlier === draft.name)
    .map((pair) => duplicateWarning(pair.later, pair.earlier))
}

const MAX_DESCRIPTION = 500

// Problems the backend would answer with 422, caught before the request.
export function draftProblem(draft: Template, others: Template[]): string | null {
  const name = draft.name.trim()
  if (name === '') {
    return 'A template needs a name.'
  }
  if (others.some((other) => other.name === name)) {
    return `There is already a template named ${name}.`
  }
  if ((draft.description ?? '').trim().length > MAX_DESCRIPTION) {
    return `A description holds at most ${MAX_DESCRIPTION} characters.`
  }
  if (draft.coordinates.length === 0) {
    return 'A template needs at least one coordinate.'
  }
  if (draft.coordinates.every((coordinate) => coordinate.optional === true)) {
    return 'A template needs at least one required field.'
  }
  const keys = new Set<string>()
  for (const coordinate of draft.coordinates) {
    if (coordinate.kind === 'field' && coordinate.name === '') {
      return 'Every row needs a field. Pick one or remove the row.'
    }
    if (coordinate.kind === 'fieldPattern' && !isValidPattern(coordinate.pattern)) {
      return PATTERN_RULE
    }
    const key = coordinateKey(coordinate)
    if (keys.has(key)) {
      return `${coordinateColumn(coordinate)} is in the template twice.`
    }
    keys.add(key)
  }
  return null
}

// Only what is stored: the backend leaves the facts of a GET out anyway.
function stored(datatype: DatatypeDraft): StoredDatatype {
  const { id, name, pattern, description, ignoreCase, wholeValue, examples, counterExamples } = datatype
  return {
    ...(id ? { id } : {}),
    name,
    pattern,
    description,
    ignoreCase,
    ...(wholeValue ? {} : { wholeValue: false }),
    examples,
    counterExamples,
  }
}

// What saving a datatype changes: one without an id is new.
export function datatypeChange(datatype: DatatypeDraft): DatatypeChange {
  return datatype.id ? { op: 'setDatatype', datatype: stored(datatype) } : { op: 'addDatatype', datatype: stored(datatype) }
}
