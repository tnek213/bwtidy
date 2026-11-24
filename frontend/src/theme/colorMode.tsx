import { CssBaseline, createTheme, ThemeProvider, useMediaQuery } from '@mui/material'
import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from 'react'
import { useLayout } from './layout.tsx'
import { PROSE_WIDTH } from './Prose.tsx'

// MUI's default is 8. The compact layout shrinks the margins so more fits in a small window.
const COMPACT_SPACING = 5

export const COLOR_MODE_COOKIE = 'bwtidy_color_mode'

type ColorMode = 'light' | 'dark'

export type ColorModeSetting = ColorMode | 'system'

const ONE_YEAR = 31536000

function isColorMode(value: string | undefined): value is ColorMode {
  return value === 'light' || value === 'dark'
}

// Any value but light and dark counts as no cookie, which is the system setting.
function readSetting(): ColorModeSetting {
  const prefix = `${COLOR_MODE_COOKIE}=`
  const part = document.cookie.split(';').map((entry) => entry.trim()).find((entry) => entry.startsWith(prefix))
  const value = part?.slice(prefix.length)
  return isColorMode(value) ? value : 'system'
}

// Readable by the page on purpose: the inline script in index.html reads it before the first paint.
// System is no cookie at all.
function writeSetting(setting: ColorModeSetting) {
  const secure = location.protocol === 'https:' ? '; Secure' : ''
  if (setting === 'system') {
    document.cookie = `${COLOR_MODE_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax${secure}`
    return
  }
  document.cookie = `${COLOR_MODE_COOKIE}=${setting}; Path=/; Max-Age=${ONE_YEAR}; SameSite=Lax${secure}`
}

interface ColorModeValue {
  // The mode shown: the setting, or the system's scheme for the system setting.
  mode: ColorMode
  setting: ColorModeSetting
  setSetting: (setting: ColorModeSetting) => void
}

const ColorModeContext = createContext<ColorModeValue>({ mode: 'light', setting: 'system', setSetting: () => {} })

export function ColorModeProvider({ children }: { children: ReactNode }) {
  const [setting, setSettingState] = useState<ColorModeSetting>(readSetting)
  const osDark = useMediaQuery('(prefers-color-scheme: dark)', { noSsr: true })
  const mode: ColorMode = setting === 'system' ? (osDark ? 'dark' : 'light') : setting
  const { layout } = useLayout()

  const setSetting = useCallback((next: ColorModeSetting) => {
    writeSetting(next)
    setSettingState(next)
  }, [])

  const value = useMemo(() => ({ mode, setting, setSetting }), [mode, setting, setSetting])
  const theme = useMemo(
    () =>
      createTheme({
        palette: { mode },
        spacing: layout === 'compact' ? COMPACT_SPACING : 8,
        components: { MuiDialogContentText: { styleOverrides: { root: { maxWidth: PROSE_WIDTH } } } },
      }),
    [mode, layout],
  )

  return (
    <ColorModeContext value={value}>
      <ThemeProvider theme={theme}>
        <CssBaseline enableColorScheme />
        {children}
      </ThemeProvider>
    </ColorModeContext>
  )
}

export function useColorMode(): ColorModeValue {
  return useContext(ColorModeContext)
}
