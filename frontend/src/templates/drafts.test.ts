import { expect, test } from 'vitest'
import type { Template } from '../api/templates.ts'
import { draftDuplicateWarnings, draftProblem, duplicatePairs } from './drafts.ts'

const computer: Template = {
  name: 'Computer',
  type: 1,
  coordinates: [
    { kind: 'path', path: 'login.username' },
    { kind: 'field', name: 'BIOS Password', fieldType: 1 },
    { kind: 'field', name: 'disk encryption', fieldType: 1 },
  ],
}

test('a template with the same type and fields in another order is a duplicate of the first', () => {
  const copy: Template = { name: 'Copy', type: 1, coordinates: [...computer.coordinates].reverse() }
  const again: Template = { ...copy, name: 'Again' }
  expect(duplicatePairs([computer, copy, again], [])).toEqual([
    { later: 'Copy', earlier: 'Computer' },
    { later: 'Again', earlier: 'Computer' },
  ])
})

test('another type, optional flag or datatype is no duplicate, an unknown datatype counts as none', () => {
  const [first, ...rest] = computer.coordinates
  const other = (coordinates: Template['coordinates'], type = 1): Template => ({ name: 'Other', type, coordinates })
  expect(duplicatePairs([computer, other(computer.coordinates, 2)], [])).toEqual([])
  expect(duplicatePairs([computer, other([{ ...first, optional: true }, ...rest])], [])).toEqual([])
  expect(duplicatePairs([computer, other([{ ...first, datatype: 'aaaa' }, ...rest])], [])).toHaveLength(1)
})

test('a draft is compared in the place it will be listed', () => {
  const copy: Template = { ...computer, name: ' Copy ' }
  expect(draftDuplicateWarnings(copy, [computer], 1, [])).toEqual([
    'Copy: same type and fields as Computer, which is listed first and takes every match.',
  ])
  expect(draftDuplicateWarnings(copy, [computer], 0, [])).toEqual([
    'Computer: same type and fields as Copy, which is listed first and takes every match.',
  ])
  expect(draftDuplicateWarnings({ ...copy, name: '' }, [computer], 1, [])).toEqual([])
})

test('a pattern compares by its text ignoring case, never equal to a field of that name', () => {
  const pattern = (name: string, text: string): Template => ({
    name,
    type: 2,
    coordinates: [{ kind: 'fieldPattern', pattern: text, fieldType: 0 }],
  })
  const literal: Template = { name: 'Literal', type: 2, coordinates: [{ kind: 'field', name: 'user_*', fieldType: 0 }] }
  expect(duplicatePairs([pattern('A', 'user_*'), pattern('B', 'USER_*'), pattern('C', 'pc_*'), literal], [])).toEqual([
    { later: 'B', earlier: 'A' },
  ])
  const twice = { ...pattern('D', 'user_*'), coordinates: [...pattern('D', 'user_*').coordinates, ...pattern('D', 'User_*').coordinates] }
  expect(draftProblem(twice, [])).toBe('fields.User_* is in the template twice.')
  expect(draftProblem(pattern('E', '**'), [])).toBe('A pattern needs a * and some other character.')
  expect(draftProblem(pattern('F', 'user'), [])).toBe('A pattern needs a * and some other character.')
})

test('a name another template has ignoring case and surrounding spaces is taken', () => {
  const named = (name: string): Template => ({ ...computer, name })
  expect(draftProblem(named('work'), [named('Work')])).toBe('There is already a template named Work.')
  expect(draftProblem(named('STRASSE'), [named('Straße')])).toBe('There is already a template named Straße.')
  expect(draftProblem(named('Work '), [named('Work')])).toBe('There is already a template named Work.')
  expect(draftProblem(named('work'), [named(' Work ')])).toBe('There is already a template named Work.')
  expect(draftProblem(named('Workshop'), [named('Work')])).toBeNull()
})

test('a template keeps its own name in another case, as the editor compares it with the others only', () => {
  const work: Template = { ...computer, name: 'Work' }
  const home: Template = { ...computer, name: 'Home' }
  const templates = [work, home]
  const others = templates.filter((template) => template !== work)
  expect(draftProblem({ ...work, name: 'WORK' }, others)).toBeNull()
})
