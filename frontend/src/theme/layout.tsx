import { type ReactNode, createContext, useCallback, useContext, useMemo, useState } from 'react'

// One setting per browser that every account shares, next to the color mode (docs/spec.md, Settings).
export const LAYOUT_STORAGE_KEY = 'bwtidy.layout'

export type Layout = 'normal' | 'compact'

// Any value but compact counts as normal, the default, and no storage reads as normal too.
function readLayout(): Layout {
  try {
    return localStorage.getItem(LAYOUT_STORAGE_KEY) === 'compact' ? 'compact' : 'normal'
  } catch {
    return 'normal'
  }
}

// A failed write keeps the choice for this page: the state changes either way.
function writeLayout(layout: Layout) {
  try {
    if (layout === 'compact') {
      localStorage.setItem(LAYOUT_STORAGE_KEY, 'compact')
    } else {
      localStorage.removeItem(LAYOUT_STORAGE_KEY)
    }
  } catch {
    // No storage, so the choice lasts only until the page is left.
  }
}

interface LayoutValue {
  layout: Layout
  setLayout: (layout: Layout) => void
}

const LayoutContext = createContext<LayoutValue>({ layout: 'normal', setLayout: () => {} })

export function LayoutProvider({ children }: { children: ReactNode }) {
  const [layout, setLayoutState] = useState<Layout>(readLayout)

  const setLayout = useCallback((next: Layout) => {
    writeLayout(next)
    setLayoutState(next)
  }, [])

  const value = useMemo(() => ({ layout, setLayout }), [layout, setLayout])

  return <LayoutContext value={value}>{children}</LayoutContext>
}

export function useLayout(): LayoutValue {
  return useContext(LayoutContext)
}
