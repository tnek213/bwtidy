import { AppBar as MuiAppBar, Box, ButtonBase, Toolbar, Typography } from '@mui/material'
import type { Account } from '../api/account.ts'
import { mcpBarSx } from './mcpStyle.ts'
import McpToggle from './McpToggle.tsx'
import { useSession } from '../api/session.ts'
import { useLayout } from '../theme/layout.tsx'
import { useVersion } from '../api/version.ts'
import { shortVersion } from './version.ts'
import { accountLabel } from '../shared/account.ts'
import ColorModeButton from './ColorModeButton.tsx'
import LogoutButton from './LogoutButton.tsx'
import MoreMenu from './MoreMenu.tsx'
import NavButton from './NavButton.tsx'
import QueueButton from './QueueButton.tsx'
import UnsavedChanges from './UnsavedChanges.tsx'

const TITLE = 'bwtidy'

export type Page = 'grid' | 'types' | 'queue' | 'changeLog' | 'settings' | 'statistics' | 'about'

interface AppBarProps {
  account: Account | null
  page: Page
  onPageChange: (page: Page) => void
}

// The bar on every page: the page links, the Commit now button, the account and the session actions.
export default function AppBar({ account, page, onPageChange }: AppBarProps) {
  const mcpOn = useSession().data?.mcpEnabled ?? false
  const { layout } = useLayout()
  const version = useVersion()

  return (
  <MuiAppBar
    position="static"
    sx={(theme) => ({
      ...mcpBarSx(theme, mcpOn),
      // The buttons take the bar's text color, so their focus ring stands out from the bar as the text does.
      '& .MuiButton-root.Mui-focusVisible, & .MuiIconButton-root.Mui-focusVisible': { outlineColor: 'currentColor' },
    })}
  >
    <Toolbar variant={layout === 'compact' ? 'dense' : 'regular'}>
      <Typography variant="h6" component="h1" sx={{ mr: 3 }}>
        <ButtonBase
          aria-label={`${TITLE}, go to the Vault`}
          onClick={() => onPageChange('grid')}
          sx={{
            font: 'inherit',
            letterSpacing: 'inherit',
            color: 'inherit',
            textTransform: 'none',
            cursor: 'pointer',
            userSelect: 'none',
            borderRadius: 1,
            '&.Mui-focusVisible': { outline: '2px solid currentColor', outlineOffset: 2 },
          }}
        >
          {TITLE}
        </ButtonBase>
        {version !== undefined && (
          <Box
            component="span"
            title={version}
            // the docs screenshots hide it (backend/tests/support/docs_shots.py), it changes with every commit
            data-shot-hide
            sx={{ ml: 1, fontSize: 11, opacity: 0.35, color: 'inherit', verticalAlign: 'baseline', userSelect: 'text' }}
          >
            {shortVersion(version)}
          </Box>
        )}
      </Typography>
      <Box component="nav" sx={{ flexGrow: 1, display: 'flex', alignSelf: 'stretch', gap: 1 }}>
        <NavButton current={page === 'grid'} onClick={() => onPageChange('grid')}>
          Vault
        </NavButton>
        <NavButton current={page === 'types'} onClick={() => onPageChange('types')}>
          Types
        </NavButton>
        <QueueButton current={page === 'queue'} onClick={() => onPageChange('queue')} />
        <UnsavedChanges />
        <NavButton current={page === 'changeLog'} onClick={() => onPageChange('changeLog')}>
          Change log
        </NavButton>
      </Box>
      {account !== null && (
        <Typography variant="body2" sx={{ mr: 2, whiteSpace: 'nowrap' }}>
          {accountLabel(account)}
        </Typography>
      )}
      <McpToggle />
      <ColorModeButton />
      <LogoutButton />
      <MoreMenu
        account={account}
        onSettings={() => onPageChange('settings')}
        onAbout={() => onPageChange('about')}
      />
    </Toolbar>
  </MuiAppBar>
  )
}
