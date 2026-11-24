import { IconButton, Menu, MenuItem } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { Account } from '../api/account.ts'
import { MoreIcon } from './icons.tsx'
import { switchAccount } from '../shared/account.ts'

interface MoreMenuProps {
  account: Account | null
  onSettings: () => void
  onAbout: () => void
}

export default function MoreMenu({ account, onSettings, onAbout }: MoreMenuProps) {
  const queryClient = useQueryClient()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)

  return (
    <>
      <IconButton color="inherit" aria-label="More actions" onClick={(event) => setAnchor(event.currentTarget)}>
        <MoreIcon />
      </IconButton>
      <Menu anchorEl={anchor} open={anchor !== null} onClose={() => setAnchor(null)}>
        <MenuItem
          onClick={() => {
            setAnchor(null)
            onSettings()
          }}
        >
          Settings
        </MenuItem>
        {account !== null && (
          <MenuItem
            onClick={() => {
              setAnchor(null)
              void switchAccount(queryClient, true)
            }}
          >
            Switch account
          </MenuItem>
        )}
        <MenuItem
          onClick={() => {
            setAnchor(null)
            onAbout()
          }}
        >
          About bwtidy
        </MenuItem>
      </Menu>
    </>
  )
}
