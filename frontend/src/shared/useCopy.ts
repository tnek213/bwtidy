import { useState } from 'react'

type CopyState = 'copied' | 'failed' | null

// What a copy button says under or beside it, a space for none so the layout holds.
export function copyFeedback(state: CopyState): string {
  return state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy, select it by hand' : ' '
}

// Writes text to the clipboard and remembers whether that worked.
export function useCopy(): { state: CopyState; copy: (text: string) => Promise<void> } {
  const [state, setState] = useState<CopyState>(null)

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
  }

  return { state, copy }
}
