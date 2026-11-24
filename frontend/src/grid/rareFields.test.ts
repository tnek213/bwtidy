import { expect, test } from 'vitest'
import type { GridColumn, GridRow } from '../api/grid.ts'
import {
  orderedRareColumns,
  parseRareFieldsMax,
  rareColumns,
  rareEntries,
  rareFieldsHeader,
  rareMatchesSearch,
  rareText,
} from './rareFields.ts'
import { gridColumn } from './testSupport.tsx'

const columns: GridColumn[] = [
  gridColumn('name'),
  gridColumn('notes'),
  gridColumn('type'),
  gridColumn('folder'),
  gridColumn('organization'),
  gridColumn('collections', { multi: true }),
  gridColumn('favorite'),
  gridColumn('reprompt'),
  gridColumn('attachments', { multi: true, readOnly: true }),
  gridColumn('passkeys', { readOnly: true }),
  gridColumn('revisionDate', { readOnly: true }),
  gridColumn('login.username'),
  gridColumn('login.uris', { multi: true }),
  gridColumn('card.brand'),
  gridColumn('fields.vlan'),
  gridColumn('fields.sudo', { secret: true }),
  gridColumn('fields.pin', { secret: true }),
  gridColumn('fields.common'),
  gridColumn('fields.backup-password', { secret: true }),
]

function row(id: string, cells: GridRow['cells'], extra: Partial<GridRow> = {}): GridRow {
  return { id, revisionDate: '', cells, ...extra }
}

// Every item has name, notes, folder and the like, once or twice, never more often than a rare field.
const rows: GridRow[] = [
  row('a', {
    name: 'Router',
    notes: 'n',
    folder: 'Infra',
    organization: 'Family',
    collections: ['Shared'],
    favorite: true,
    reprompt: true,
    attachments: ['a.txt'],
    passkeys: 1,
    revisionDate: '2026-01-01',
    type: 'login',
    'login.username': 'admin',
    'login.uris': ['https://a.example', 'https://b.example'],
    'fields.vlan': '20',
    'fields.sudo': { masked: true },
    'fields.common': 'x',
  }),
  // An empty text field and an empty hidden field have no cell, the row lists them.
  row(
    'b',
    { name: 'Switch', type: 'login', 'fields.common': 'y' },
    { emptyTextFields: ['fields.vlan'], emptySecrets: ['login.password', 'fields.pin'] },
  ),
  // An empty login.username is no cell either, and a built-in slot counts only with a value.
  row('c', { name: 'Card', type: 'card', 'card.brand': 'Visa', 'fields.common': 'z' }),
  row('note', { name: 'bwtidy', type: 'note', 'fields.backup-password': { masked: true }, 'fields.vlan': '1' }, { toolNote: true }),
]

test('N 0 turns Rare fields off', () => {
  expect(rareColumns(columns, rows, 0)).toEqual(new Set())
  expect(rareFieldsHeader(0)).toBe('Rare fields (off)')
  expect(rareFieldsHeader(2)).toBe('Rare fields ≤ 2')
})

test('a column goes in when at most N items use it', () => {
  // fields.vlan is on a and b (empty on b): in at 2, not at 1. The tool note does not count.
  expect(rareColumns(columns, rows, 1)).toEqual(
    new Set(['login.username', 'login.uris', 'card.brand', 'fields.sudo', 'fields.pin']),
  )
  expect(rareColumns(columns, rows, 2)).toEqual(
    new Set(['login.username', 'login.uris', 'card.brand', 'fields.vlan', 'fields.sudo', 'fields.pin']),
  )
  expect(rareColumns(columns, rows, 3)).toEqual(
    new Set(['login.username', 'login.uris', 'card.brand', 'fields.vlan', 'fields.sudo', 'fields.pin', 'fields.common']),
  )
})

test('the item columns, the info columns and a column only the tool note has never go in', () => {
  const rare = rareColumns(columns, rows, 99)
  for (const id of ['name', 'notes', 'type', 'folder', 'organization', 'collections', 'favorite', 'reprompt', 'attachments', 'passkeys', 'revisionDate', 'fields.backup-password']) {
    expect(rare.has(id), id).toBe(false)
  }
})

test('an empty built-in slot does not count', () => {
  const twoLogins = [...rows, row('d', { name: 'Other', type: 'login', 'login.username': '' })]
  expect(rareColumns(columns, twoLogins, 1).has('login.username')).toBe(true)
})

test('an item shows its rare columns as JSON, an empty field (a hidden one too) as "" and a secret as the mask', () => {
  const rare = orderedRareColumns(columns, rareColumns(columns, rows, 2))
  expect(rareText(rareEntries(rows[0], rare))).toBe(
    '{ "username": "admin", "uris": ["https://a.example", "https://b.example"], "vlan": "20", "sudo": •••••••• }',
  )
  expect(rareText(rareEntries(rows[1], rare))).toBe('{ "vlan": "", "pin": "" }')
  expect(rareText(rareEntries(rows[2], rare))).toBe('{ "brand": "Visa" }')
})

test('two columns with the same label keep their full coordinates', () => {
  const both = [gridColumn('login.username'), gridColumn('fields.username'), gridColumn('fields.vlan')]
  const item = row('x', { 'login.username': 'user', 'fields.username': 'k', 'fields.vlan': '3' })
  expect(rareText(rareEntries(item, both))).toBe('{ "login.username": "user", "fields.username": "k", "vlan": "3" }')
})

test('the search finds a shown value, never a secret one', () => {
  const rare = orderedRareColumns(columns, rareColumns(columns, rows, 2))
  const entries = rareEntries(rows[0], rare)
  expect(rareMatchesSearch(entries, ['b.example'])).toBe(true)
  expect(rareMatchesSearch(entries, ['20'])).toBe(true)
  expect(rareMatchesSearch(entries, ['masked'])).toBe(false)
  expect(rareMatchesSearch(entries, ['sudo'])).toBe(false)
})

test('N is a whole number from 0 to 99', () => {
  expect(parseRareFieldsMax('0')).toBe(0)
  expect(parseRareFieldsMax(' 7 ')).toBe(7)
  expect(parseRareFieldsMax('99')).toBe(99)
  for (const text of ['', '-1', '100', '2.5', 'two', '1e1']) {
    expect(parseRareFieldsMax(text), text).toBeNull()
  }
})
