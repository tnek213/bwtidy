import { type ReactNode, createContext, useCallback, useContext } from 'react'
import { type DateFormat, useDisplay } from '../api/settings.ts'
import { formatTime } from './format.ts'

// System outside the provider and until the setting has loaded (or when it cannot be loaded).
const DateFormatContext = createContext<DateFormat>('system')

// Gives every page the chosen format, so they all follow a change at once.
export function DateFormatProvider({ children }: { children: ReactNode }) {
  const display = useDisplay()
  return <DateFormatContext.Provider value={display.data?.dateFormat ?? 'system'}>{children}</DateFormatContext.Provider>
}

export function useDateFormat(): DateFormat {
  return useContext(DateFormatContext)
}

export function useFormatTime(): (at: string | null) => string {
  const format = useDateFormat()
  return useCallback((at: string | null) => formatTime(at, format), [format])
}
