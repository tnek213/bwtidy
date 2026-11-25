import { expect, test } from 'vitest'
import { lineBreaks } from './lineBreaks.ts'

test('words of equal width make lines of equal word counts, the rest goes on the last line', () => {
  // Three words take 34 of 40, four would take 46.
  expect(lineBreaks([10, 10, 10, 10, 10, 10], 2, 40)).toEqual([3])
  expect(lineBreaks([10, 10, 10, 10, 10, 10, 10], 2, 40)).toEqual([3, 6])
})

test('the breaks even out a line that greedy filling leaves short', () => {
  // Greedy filling gives 30 20 | 20 | 50, slack 0 and then 40. Balanced gives 30 | 20 20 | 50, slack 30 and 10.
  expect(lineBreaks([30, 20, 20, 50], 10, 60)).toEqual([1, 3])
})

test('a word wider than the line sits alone on its line', () => {
  expect(lineBreaks([10, 100, 10], 5, 50)).toEqual([1, 2])
  expect(lineBreaks([100, 10, 10], 5, 50)).toEqual([1])
})

test('no width to break into gives no breaks', () => {
  expect(lineBreaks([10, 10, 10], 2, 0)).toEqual([])
  expect(lineBreaks([10, 10, 10], 2, -5)).toEqual([])
  expect(lineBreaks([0, 0, 0], 0, 100)).toEqual([])
  expect(lineBreaks([], 2, 100)).toEqual([])
})

test('a paragraph that fits on one line has no breaks', () => {
  expect(lineBreaks([10, 20, 30], 5, 100)).toEqual([])
})

test('the last line may be short', () => {
  expect(lineBreaks([40, 40, 40, 40, 5], 10, 90)).toEqual([2, 4])
})
