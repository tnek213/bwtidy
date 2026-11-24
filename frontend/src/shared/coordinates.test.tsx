import { expect, test } from 'vitest'
import { columnGroup, fieldColumn, fieldName, isFieldColumn, parseFieldColumn } from './coordinates.ts'

test('a field name ending in #N carries its occurrence', () => {
  expect(fieldColumn('user')).toBe('fields.user')
  expect(fieldColumn('key#2')).toBe('fields.key#2#1')
})

test('a field column parses back to its name and occurrence', () => {
  expect(parseFieldColumn('fields.user')).toEqual({ name: 'user', occurrence: 1 })
  expect(parseFieldColumn('fields.WPA key#2')).toEqual({ name: 'WPA key', occurrence: 2 })
  expect(parseFieldColumn('fields.key#2#1')).toEqual({ name: 'key#2', occurrence: 1 })
  expect(parseFieldColumn('login.username')).toBeNull()
  expect(fieldColumn('WPA key', 3)).toBe('fields.WPA key#3')
})

test('a field column is told from a built-in one and gives its name without the occurrence', () => {
  expect(isFieldColumn('fields.user')).toBe(true)
  expect(isFieldColumn('login.username')).toBe(false)
  expect(fieldName('fields.WPA key#2')).toBe('WPA key')
  expect(fieldName('fields.key#2#1')).toBe('key#2')
})

test('a column id belongs to the group before its first dot, or to item', () => {
  expect(columnGroup('fields.user')).toBe('fields')
  expect(columnGroup('login.username')).toBe('login')
  expect(columnGroup('sshKey.privateKey')).toBe('sshKey')
  expect(columnGroup('name')).toBe('item')
  expect(columnGroup('unknown.thing')).toBe('item')
})
