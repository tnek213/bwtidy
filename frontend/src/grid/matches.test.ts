import { expect, test } from 'vitest'
import type { FormatScore, Template, TemplateScore } from '../api/templates.ts'
import {
  applyTemplate,
  coordinateOfColumn,
  defaultMapping,
  fillLabel,
  fitsPattern,
  FORMAT_OFF,
  FORMAT_PARTIAL,
  matchesFormatFilter,
  matchLabel,
  matchOrder,
  matchTooltip,
  missingList,
  patternPrefix,
} from './matches.ts'

const computer: Template = {
  name: 'Computer',
  type: 1,
  coordinates: [
    { kind: 'path', path: 'login.username' },
    { kind: 'field', name: 'BIOS Password', fieldType: 1 },
    { kind: 'field', name: 'disk encryption', fieldType: 1 },
  ],
}

const nearMiss: TemplateScore = {
  template: 'Computer',
  score: 2 / 4,
  typeMatches: false,
  filled: 2,
  total: 4,
  missing: ['fields.disk encryption'],
}

test('a near miss lists a wrong type first, then the missing columns as the grid headers name them', () => {
  expect(missingList(nearMiss, 'note', computer)).toEqual(['Type: note, the template wants login', 'disk encryption'])
  const sameType = { ...nearMiss, typeMatches: true, filled: 3, missing: ['login.username', 'fields.key#2#1', 'notes'] }
  expect(missingList(sameType, 'login', computer)).toEqual(['username', 'key#2#1', 'notes'])
  expect(missingList(nearMiss, 'note', undefined)).toEqual(['Type: note, the template wants another type', 'disk encryption'])
})

test('the template cell tooltip shows the description of the template under its heading', () => {
  const described = { ...computer, description: 'A laptop or desktop' }
  const full: TemplateScore = { ...nearMiss, typeMatches: true, filled: 4, score: 1, missing: [] }
  expect(matchTooltip({ match: 'Computer', scores: [full] }, 'login', [described])).toBe('Computer\nA laptop or desktop')
  expect(matchTooltip({ match: null, scores: [nearMiss] }, 'note', [described])).toBe(
    'Missing for Computer:\nA laptop or desktop\n\u2022 Type: note, the template wants login\n\u2022 disk encryption',
  )
  expect(matchTooltip({ match: 'Computer', scores: [full] }, 'login', [{ ...computer, description: '' }])).toBe('Computer')
})

test('the template cell tooltip is a bullet list for a near miss and the name for a match', () => {
  const other: TemplateScore = { template: 'Other', score: 0, typeMatches: false, filled: 0, total: 2, missing: ['notes'] }
  expect(matchTooltip({ match: null, scores: [other, nearMiss] }, 'note', [computer])).toBe(
    'Missing for Computer:\n• Type: note, the template wants login\n• disk encryption',
  )
  const full: TemplateScore = { ...nearMiss, typeMatches: true, filled: 4, score: 1, missing: [] }
  expect(matchTooltip({ match: 'Computer', scores: [full] }, 'login', [computer])).toBe('Computer')
  expect(matchTooltip({ match: null, scores: [other] }, 'note', [computer])).toBeUndefined()
  expect(matchTooltip(undefined, 'note', [computer])).toBeUndefined()
})

const pinFormat: FormatScore = {
  column: 'fields.PIN',
  datatype: 'PIN',
  score: 0.8,
  distance: 1,
  firstEdit: 0,
  expected: 'a digit',
  tooLong: false,
}

const withFormats: Template = {
  name: 'Computer',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'PIN', fieldType: 0, datatype: '1f0c9a3e' },
    { kind: 'field', name: 'BIOS password', fieldType: 1, datatype: '1f0c9a3e' },
    { kind: 'field', name: 'Serial', fieldType: 0, datatype: '1f0c9a3e' },
  ],
}

function formatScore(formats: FormatScore[], filled: number, total: number): TemplateScore {
  return { template: 'Computer', score: filled / total, typeMatches: true, filled, total, missing: [], formats }
}

test('the fill shows two decimals when it is not whole and stays as it was when it is', () => {
  const near = formatScore([pinFormat], 4.37, 5)
  expect(fillLabel(near)).toBe('4.37/5')
  expect(fillLabel({ ...near, filled: 4.5 })).toBe('4.50/5')
  expect(fillLabel({ ...near, filled: 3, total: 4 })).toBe('3/4')
  expect(matchLabel({ match: null, scores: [near] })).toBe('Computer 4.37/5')
  expect(matchLabel({ match: null, scores: [{ ...nearMiss, filled: 3 }] })).toBe('Computer 3/4')
})

test('a near miss with a value off its format has one bullet per format below 1', () => {
  const formats = [
    pinFormat,
    { ...pinFormat, column: 'fields.Serial', score: 1, distance: 0, firstEdit: null, expected: null },
    { ...pinFormat, column: 'fields.Long', score: 0, distance: null, firstEdit: null, expected: null, tooLong: true },
    { ...pinFormat, column: 'fields.Wrong', score: 0.5, distance: 3, firstEdit: 2, expected: 'a letter' },
  ]
  expect(matchTooltip({ match: null, scores: [formatScore(formats, 3.3, 5)] }, 'note', [withFormats], () => false)).toBe(
    [
      'Off format for Computer:',
      '• PIN: matches format 80% (1 change at position 1, expected a digit)',
      '• Long: too long to check',
      '• Wrong: matches format 50% (3 changes, the first at position 3, expected a letter)',
    ].join('\n'),
  )
})

test('the bullet of a secret column has only the numbers and positions', () => {
  const secret = { ...pinFormat, column: 'fields.BIOS password', score: 0.29 }
  const scores = [formatScore([secret], 2.29, 3)]
  const line = '• BIOS password: matches format 29% (1 change at position 1)'
  // The template says hidden, and the grid knows nothing.
  expect(matchTooltip({ match: null, scores }, 'note', [withFormats])).toContain(line)
  // A template that says text, on a column the grid has as secret.
  const asText = { ...withFormats, coordinates: [{ kind: 'field', name: 'BIOS password', fieldType: 0 } as const] }
  expect(matchTooltip({ match: null, scores }, 'note', [asText], (column) => column === secret.column)).toContain(line)
})

test('a column without a value has its missing bullet and no format bullet', () => {
  const empty = { ...pinFormat, score: 0, distance: null, firstEdit: null, expected: null }
  const score = { ...formatScore([empty], 2, 3), missing: ['fields.PIN'] }
  expect(matchTooltip({ match: null, scores: [score] }, 'note', [withFormats])).toBe('Missing for Computer:\n• PIN')
})

test('the template column sorts by the score, a match over every near miss and no template last', () => {
  const near = (filled: number) => ({ match: null, scores: [formatScore([pinFormat], filled, 5)] })
  const full = { match: 'Computer', scores: [formatScore([], 5, 5)] }
  expect(matchOrder(near(4.37))).toBeCloseTo(0.874)
  expect(matchOrder(full)).toBe(1)
  expect(matchOrder({ match: null, scores: [] })).toBe(-1)
  expect(matchOrder(near(4.8))).toBeGreaterThan(matchOrder(near(4.37)))
  expect(matchOrder(full)).toBeGreaterThan(matchOrder(near(4.8)))
})

test('the Format filter goes by the template the row shows', () => {
  const off = { match: null, scores: [formatScore([pinFormat], 2.8, 3)] }
  const none = { match: null, scores: [formatScore([{ ...pinFormat, score: 0 }], 2, 3)] }
  const full = { match: 'Computer', scores: [formatScore([{ ...pinFormat, score: 1 }], 3, 3)] }
  const without = { match: 'Other', scores: [{ ...nearMiss, template: 'Other', filled: 4, typeMatches: true }] }
  for (const match of [off, none, full, without, undefined]) {
    expect(matchesFormatFilter(match, '')).toBe(true)
  }
  expect([off, none, full, without, undefined].map((match) => matchesFormatFilter(match, FORMAT_OFF))).toEqual([
    true,
    true,
    false,
    false,
    false,
  ])
  expect([off, none, full, without, undefined].map((match) => matchesFormatFilter(match, FORMAT_PARTIAL))).toEqual([
    true,
    false,
    false,
    false,
    false,
  ])
})

const shared: Template = {
  name: 'Shared',
  type: 2,
  coordinates: [
    { kind: 'field', name: 'user_admin', fieldType: 0 },
    { kind: 'fieldPattern', pattern: 'user_*', fieldType: 0, datatype: 'pin' },
    { kind: 'fieldPattern', pattern: '*_dev', fieldType: 0 },
  ],
}

test('a pattern fits a whole name, * any characters, ignoring case and surrounding spaces', () => {
  expect(fitsPattern('user_*', 'user_dev')).toBe(true)
  expect(fitsPattern('user_*', 'user_')).toBe(true)
  expect(fitsPattern(' User_* ', '  USER_dev ')).toBe(true)
  expect(fitsPattern('user_*', 'a user_dev')).toBe(false)
  expect(fitsPattern('a*b*c', 'axbyc')).toBe(true)
  expect(fitsPattern('a*b*c', 'acb')).toBe(false)
  expect(fitsPattern('a*a', 'a')).toBe(false)
  expect(fitsPattern('u.ser*', 'uxser')).toBe(false)
  expect(patternPrefix('user_*_pw')).toBe('user_')
})

test('a grid column belongs to its own coordinate, else to the first pattern it fits', () => {
  expect(coordinateOfColumn(shared, 'fields.user_admin')).toBe(shared.coordinates[0])
  expect(coordinateOfColumn(shared, 'fields.user_dev')).toBe(shared.coordinates[1])
  expect(coordinateOfColumn(shared, 'fields.youth_dev')).toBe(shared.coordinates[2])
  expect(coordinateOfColumn(shared, 'fields.User_Admin')).toBeUndefined()
  expect(coordinateOfColumn(shared, 'notes')).toBeUndefined()
})

test('a missing pattern says it wants a field named like it', () => {
  const score = { ...nearMiss, typeMatches: true, missing: ['fields.user_*'] }
  expect(missingList(score, 'note', shared)).toEqual(['a field named like user_*'])
})

test('convert maps a field onto a pattern it fits and sends nothing for it', () => {
  const sources = [
    { column: 'fields.user_admin', secret: false },
    { column: 'fields.user_dev', secret: false },
    { column: 'fields.other', secret: false },
  ]
  const mapping = defaultMapping(shared, sources)
  expect(mapping).toEqual({ 'fields.user_admin': 'fields.user_admin', 'fields.user_*': 'fields.user_dev' })
  const row = { id: 'i', revisionDate: 'r', cells: {} }
  expect(applyTemplate(row, shared, { ...mapping, 'fields.*_dev': 'fields.other' })).toMatchObject({ mapping: {} })
})
