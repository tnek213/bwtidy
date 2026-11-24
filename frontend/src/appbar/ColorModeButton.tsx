import { IconButton, ListItemIcon, Menu, MenuItem, Tooltip } from '@mui/material'
import { type ReactNode, useState } from 'react'
import { type ColorModeSetting, useColorMode } from '../theme/colorMode.tsx'
import { MoonIcon, SunIcon, SystemIcon } from './icons.tsx'

const COLOR_MODE_CHOICES: { setting: ColorModeSetting; label: string; icon: ReactNode }[] = [
  { setting: 'system', label: 'System', icon: <SystemIcon /> },
  { setting: 'light', label: 'Light', icon: <SunIcon /> },
  { setting: 'dark', label: 'Dark', icon: <MoonIcon /> },
]

export default function ColorModeButton() {
  const { setting, setSetting } = useColorMode()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const current = COLOR_MODE_CHOICES.find((choice) => choice.setting === setting) ?? COLOR_MODE_CHOICES[0]
  const label = `Color mode: ${current.label}`
  return (
    <>
      <Tooltip title={label}>
        <IconButton color="inherit" aria-label={label} onClick={(event) => setAnchor(event.currentTarget)}>
          {current.icon}
        </IconButton>
      </Tooltip>
      <Menu anchorEl={anchor} open={anchor !== null} onClose={() => setAnchor(null)}>
        {COLOR_MODE_CHOICES.map((choice) => (
          <MenuItem
            key={choice.setting}
            role="menuitemradio"
            aria-checked={choice.setting === setting}
            selected={choice.setting === setting}
            onClick={() => {
              setAnchor(null)
              setSetting(choice.setting)
            }}
          >
            <ListItemIcon>{choice.icon}</ListItemIcon>
            {choice.label}
          </MenuItem>
        ))}
      </Menu>
    </>
  )
}
