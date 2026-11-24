import { ApiError } from '../api/api.ts'
import type { GeneratorOptions, PassphraseOptions, PasswordOptions } from '../api/generator.ts'

export const LENGTH_LIMITS = { min: 5, max: 128 }
export const WORD_LIMITS = { min: 3, max: 20 }
export const MINIMUM_LIMITS = { min: 0, max: 9 }

export const DEFAULT_PASSWORD: PasswordOptions = {
  type: 'password',
  length: 24,
  uppercase: true,
  lowercase: true,
  number: true,
  special: true,
  minNumber: 1,
  minSpecial: 1,
  ambiguous: false,
}

export const DEFAULT_PASSPHRASE: PassphraseOptions = {
  type: 'passphrase',
  words: 4,
  separator: '-',
  capitalize: false,
  includeNumber: false,
}

export function refusalOf(error: unknown): string {
  if (error instanceof ApiError && error.detail) {
    return error.detail
  }
  return error instanceof Error ? error.message : String(error)
}

function within(value: number, limits: { min: number; max: number }): boolean {
  return Number.isInteger(value) && value >= limits.min && value <= limits.max
}

function passwordProblem(options: PasswordOptions): string | null {
  if (!within(options.length, LENGTH_LIMITS)) {
    return `The length must be ${LENGTH_LIMITS.min} to ${LENGTH_LIMITS.max}.`
  }
  if (!options.uppercase && !options.lowercase && !options.number && !options.special) {
    return 'Pick at least one of A-Z, a-z, 0-9 and special.'
  }
  if (!within(options.minNumber, MINIMUM_LIMITS) || !within(options.minSpecial, MINIMUM_LIMITS)) {
    return `A minimum must be ${MINIMUM_LIMITS.min} to ${MINIMUM_LIMITS.max}.`
  }
  // As the backend counts it: bw makes the password longer than asked when the minimums do not fit.
  const needed =
    Number(options.uppercase) +
    Number(options.lowercase) +
    (options.number ? Math.max(options.minNumber, 1) : 0) +
    (options.special ? Math.max(options.minSpecial, 1) : 0)
  return needed > options.length ? `The minimums need at least ${needed} characters.` : null
}

// Why the backend would refuse these options, or null. It checks the same limits.
export function optionsProblem(options: GeneratorOptions): string | null {
  if (options.type === 'password') {
    return passwordProblem(options)
  }
  if (!within(options.words, WORD_LIMITS)) {
    return `The number of words must be ${WORD_LIMITS.min} to ${WORD_LIMITS.max}.`
  }
  return [...options.separator].length > 1 ? 'The separator must be one character or none.' : null
}
