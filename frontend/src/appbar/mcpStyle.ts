import { decomposeColor, getOverlayAlpha, recomposeColor, type SxProps, type Theme } from '@mui/material/styles'

// How much of the error color the app bar takes on while MCP is on.
const ON_MIX = 0.2
// The app bar's elevation, which decides the overlay it gets in dark mode.
const APP_BAR_ELEVATION = 4

// The color of the two mixed, overlay's share a number from 0 to 1.
function mix(base: string, overlay: string, share: number): string {
  const a = decomposeColor(base).values
  const b = decomposeColor(overlay).values
  const values = [0, 1, 2].map((i) => Math.round(a[i] * (1 - share) + b[i] * share))
  return recomposeColor({ type: 'rgb', values: values as [number, number, number] })
}

// The app bar's usual color: primary in light mode, the paper with its elevation overlay in dark mode.
function usualBarColor(theme: Theme): string {
  const { palette } = theme
  if (palette.mode === 'light') {
    return palette.primary.main
  }
  return mix(palette.background.paper, '#fff', getOverlayAlpha(APP_BAR_ELEVATION))
}

// The app bar while MCP is on: its usual color with 20% of the theme's red mixed in.
export function mcpBarSx(theme: Theme, on: boolean): { backgroundColor?: string; backgroundImage?: string } {
  if (!on) {
    return {}
  }
  return {
    backgroundColor: mix(usualBarColor(theme), theme.palette.error.main, ON_MIX),
    // the dark mode overlay would paint over the mixed color
    backgroundImage: 'none',
  }
}

// A white thumb on a green track: on in both color modes, on the blue bar and on the dark one.
export const MCP_SWITCH_SX: SxProps<Theme> = {
  '& .MuiSwitch-switchBase.Mui-checked': { color: 'common.white' },
  '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { backgroundColor: 'success.light', opacity: 1 },
}
