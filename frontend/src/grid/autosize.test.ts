import { expect, test } from 'vitest'
import { autosizeCap, cappedWidth, MAX_AUTOSIZE_CHARS } from './autosize.ts'

test('the cap is 50 characters plus the cell padding', () => {
  expect(MAX_AUTOSIZE_CHARS).toBe(50)
  expect(autosizeCap(8, 36)).toBe(436)
})

test('a width under the cap is kept', () => {
  expect(cappedWidth(120, 8, 36)).toBe(120)
  expect(cappedWidth(436, 8, 36)).toBe(436)
})

test('a width over the cap is cut to the cap', () => {
  expect(cappedWidth(2000, 8, 36)).toBe(436)
})

test('a fractional character width rounds the cap up', () => {
  expect(cappedWidth(2000, 7.5, 36)).toBe(411)
})
