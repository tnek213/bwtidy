import { createContext, useContext, useEffect } from 'react'

// The eye of a secret in a grid cell, for the cell menu: a keyboard cannot reach a button in a cell.
export interface Revealer {
  // What the eye reveals: VALUE for the cell's own value, a coordinate in Rare fields.
  label: string
  shown: boolean
  toggle: () => void
}

// The secret's own value in a secret cell, as opposed to one of the secrets of a Rare fields cell.
export const VALUE = 'value'

// The eyes of the rendered cells by item and grid column, each under its label.
export type Revealers = Map<string, Map<string, Revealer>>

export const RevealersContext = createContext<Revealers | null>(null)

function cellKey(itemId: string, columnId: string): string {
  return `${itemId}\u0000${columnId}`
}

// Registers the eye while the cell shows it, with its state of now.
export function useRevealer(itemId: string, columnId: string, revealer: Revealer) {
  const revealers = useContext(RevealersContext)
  const { label, shown, toggle } = revealer
  useEffect(() => {
    if (!revealers) {
      return
    }
    const key = cellKey(itemId, columnId)
    const cell = revealers.get(key) ?? new Map<string, Revealer>()
    cell.set(label, { label, shown, toggle })
    revealers.set(key, cell)
    return () => {
      cell.delete(label)
      if (cell.size === 0 && revealers.get(key) === cell) {
        revealers.delete(key)
      }
    }
  }, [revealers, itemId, columnId, label, shown, toggle])
}

export function revealersOf(revealers: Revealers, itemId: string, columnId: string): Revealer[] {
  return [...(revealers.get(cellKey(itemId, columnId))?.values() ?? [])]
}

// The cell menu's entry for an eye.
export function revealerText({ label, shown }: Pick<Revealer, 'label' | 'shown'>): string {
  const verb = shown ? 'Hide' : 'Reveal'
  return label === VALUE ? `${verb} value` : `${verb} ${label}`
}
