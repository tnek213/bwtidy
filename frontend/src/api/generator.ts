import { useQuery } from '@tanstack/react-query'
import { apiFetch } from './api.ts'
import { FAST_POLL_MS } from './queue.ts'

// The generator section of the tool note (docs/spec.md, The tool note), the options of Bitwarden's generator.
export interface PasswordOptions {
  type: 'password'
  length: number
  uppercase: boolean
  lowercase: boolean
  number: boolean
  special: boolean
  minNumber: number
  minSpecial: number
  ambiguous: boolean
}

export interface PassphraseOptions {
  type: 'passphrase'
  words: number
  // One character, or empty for none.
  separator: string
  capitalize: boolean
  includeNumber: boolean
}

export type GeneratorOptions = PasswordOptions | PassphraseOptions

interface GeneratorDoc {
  default: GeneratorOptions
  items: Record<string, GeneratorOptions | undefined>
  // A save the auto-commit has not written yet.
  saving: boolean
  // The stored settings are not valid and read as none.
  error?: string
}

export const generatorQueryKey = ['generator'] as const

export function useGenerator(enabled = true) {
  return useQuery({
    queryKey: generatorQueryKey,
    queryFn: () => apiFetch<GeneratorDoc>('/api/generator'),
    refetchInterval: (query) => (query.state.data?.saving ? FAST_POLL_MS : false),
    enabled,
  })
}

interface QueuedGenerator {
  entryId: number | null
}

export function saveDefault(options: GeneratorOptions): Promise<QueuedGenerator> {
  return apiFetch<QueuedGenerator>('/api/generator/default', { method: 'PUT', body: options })
}

export function saveItem(itemId: string, options: GeneratorOptions): Promise<QueuedGenerator> {
  return apiFetch<QueuedGenerator>(`/api/generator/items/${encodeURIComponent(itemId)}`, { method: 'PUT', body: options })
}

export function deleteItem(itemId: string): Promise<QueuedGenerator> {
  return apiFetch<QueuedGenerator>(`/api/generator/items/${encodeURIComponent(itemId)}`, { method: 'DELETE' })
}

// bw generates the example, which is shown once and kept nowhere (never in a query cache).
export async function fetchExample(options: GeneratorOptions): Promise<string> {
  const { value } = await apiFetch<{ value: string }>('/api/generator/example', { method: 'POST', body: options })
  return value
}

