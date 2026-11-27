import { CssBaseline, createTheme, type Theme, ThemeProvider, useMediaQuery } from '@mui/material'
import { red } from '@mui/material/colors'
import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from 'react'
import { type Layout, useLayout } from './layout.tsx'
import { PROSE_WIDTH } from './Prose.tsx'

// MUI's default is 8. The compact layout shrinks the margins so more fits in a small window.
const COMPACT_SPACING = 5

export const COLOR_MODE_COOKIE = 'bwtidy_color_mode'

type ColorMode = 'light' | 'dark'

export type ColorModeSetting = ColorMode | 'system'

const ONE_YEAR = 31536000

// The text color has at least 3:1 against the page, the paper and the app bar in both modes.
function focusRing(theme: Theme, offset: number) {
  return { outline: `2px solid ${theme.palette.text.primary}`, outlineOffset: offset }
}

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

// The theme of the app in a color mode and a layout.
export function appTheme(mode: ColorMode, layout: Layout): Theme {
  return createTheme({
    palette: {
      mode,
      // MUI picks the text color of a contained button by this ratio, 3 by default: AA asks 4.5 for its text.
      contrastThreshold: 4.5,
      // MUI's red of the dark mode has too little contrast as text on the dark paper (4.2:1).
      ...(mode === 'dark' ? { error: { main: red[300] } } : {}),
    },
    spacing: layout === 'compact' ? COMPACT_SPACING : 8,
    components: {
      MuiDialogContentText: { styleOverrides: { root: { maxWidth: PROSE_WIDTH } } },
      // MUI marks keyboard focus with a faint tint only, too weak to see (WCAG 2.4.7 and 1.4.11).
      MuiButtonBase: { styleOverrides: { root: ({ theme }) => ({ '&.Mui-focusVisible': focusRing(theme, 2) }) } },
      // Inside their menu, list or tab bar, whose edge would clip a ring outside.
      MuiMenuItem: { styleOverrides: { root: ({ theme }) => ({ '&.Mui-focusVisible': focusRing(theme, -2) }) } },
      MuiListItemButton: { styleOverrides: { root: ({ theme }) => ({ '&.Mui-focusVisible': focusRing(theme, -2) }) } },
      MuiTab: { styleOverrides: { root: ({ theme }) => ({ '&.Mui-focusVisible': focusRing(theme, -2) }) } },
    },
  })
}

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
  const theme = useMemo(() => appTheme(mode, layout), [mode, layout])

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
