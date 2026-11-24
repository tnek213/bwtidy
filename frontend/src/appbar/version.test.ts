import { expect, test } from 'vitest'
import { shortVersion } from '../appbar/version.ts'

test('shortVersion gives the hash of the commit in parentheses', () => {
  expect(shortVersion('0.1.0-alpha.1 (261116 d475d7f)')).toBe('d475d7f')
})

test('shortVersion keeps -dirty', () => {
  expect(shortVersion('0.1.0-alpha.1 (261116 d475d7f-dirty)')).toBe('d475d7f-dirty')
})

test('shortVersion gives the whole text without a commit', () => {
  expect(shortVersion('0.1.0-alpha.1')).toBe('0.1.0-alpha.1')
})
