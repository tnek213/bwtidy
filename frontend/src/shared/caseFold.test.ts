import { expect, test } from 'vitest'
import { caseFold } from './caseFold.ts'

test('names that differ only in case fold to the same text', () => {
  expect(caseFold('Straße.txt')).toBe(caseFold('STRASSE.txt'))
  expect(caseFold('Straße.txt')).toBe('strasse.txt')
  expect(caseFold('STRAẞE')).toBe('strasse')
  expect(caseFold('Work')).toBe(caseFold('wORK'))
})

test('different names fold to different texts', () => {
  expect(caseFold('a.txt')).not.toBe(caseFold('b.txt'))
})

test('the folds that only Python casefold has, or lacks', () => {
  // A final sigma folds like any other sigma, whatever its place in the word.
  expect(caseFold('ΟΔΟΣ')).toBe(caseFold('οδος'))
  expect(caseFold('ΟΔΟΣ')).toBe('οδοσ')
  // The dotless i stays apart from i, as it does in Python.
  expect(caseFold('fıle')).not.toBe(caseFold('file'))
})
