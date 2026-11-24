import { Box, Button, CircularProgress, Stack, Typography } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import AboutPage from './about/AboutPage.tsx'
import { onLocked } from './api/api.ts'
import AppBar, { type Page } from './appbar/AppBar.tsx'
import CommitFailureSnackbar from './appbar/CommitFailureSnackbar.tsx'
import BackupReencryptionAlert from './backups/BackupReencryptionAlert.tsx'
import ChangeLogPage from './changelog/ChangeLogPage.tsx'
import GridPage from './grid/GridPage.tsx'
import AutoCommitProvider from './commit/AutoCommitProvider.tsx'
import QueuePage from './queue/QueuePage.tsx'
import { type Account, useAccount } from './api/account.ts'
import AccountPage from './session/AccountPage.tsx'
import BwLoginPage from './session/BwLoginPage.tsx'
import LoginPage from './session/LoginPage.tsx'
import { sessionQueryKey, useSession } from './api/session.ts'
import { useHeartbeat } from './session/useHeartbeat.ts'
import { DateFormatProvider } from './shared/DateFormat.tsx'
import { showLocked } from './shared/session.ts'
import { accountLabel, NO_ACCOUNT, reopenAccount, switchAccount, useOtherTabs } from './shared/account.ts'
import SettingsPage from './settings/SettingsPage.tsx'
import StatisticsPage from './statistics/StatisticsPage.tsx'
import TypesPage from './templates/TypesPage.tsx'

// A 401 locks the page. The gateway's "No account" means the worker is gone (its session
// ended, the gateway restarted), so the account is opened again and the page shows what the
// gateway knows: the unlock or the Bitwarden login, whose click starts the next worker.
function useLockedHandler() {
  const queryClient = useQueryClient()
  useEffect(
    () =>
      onLocked((detail) => {
        showLocked(queryClient)
        if (detail === NO_ACCOUNT) {
          void reopenAccount(queryClient).then((ready) => {
            if (ready) {
              void queryClient.invalidateQueries({ queryKey: sessionQueryKey })
            }
          })
        }
      }),
    [queryClient],
  )
}

function Shell({ account }: { account: Account | null }) {
  const session = useSession()
  const [page, setPage] = useState<Page>('grid')

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <AppBar account={account} page={page} onPageChange={setPage} />
      <BackupReencryptionAlert result={session.data?.backupReencryption} />
      <Box component="main" sx={{ display: 'flex', flexDirection: 'column', flexGrow: 1, minHeight: 0 }}>
        {page === 'grid' && <GridPage />}
        {page === 'types' && <TypesPage onStatistics={() => setPage('statistics')} />}
        {page === 'queue' && <QueuePage />}
        {page === 'changeLog' && <ChangeLogPage />}
        {page === 'settings' && <SettingsPage />}
        {page === 'statistics' && <StatisticsPage />}
        {page === 'about' && <AboutPage />}
      </Box>
      <CommitFailureSnackbar />
    </Box>
  )
}

function Loading() {
  return (
    <Box sx={{ display: 'flex', justifyContent: 'center', mt: 12 }}>
      <CircularProgress aria-label="Loading" />
    </Box>
  )
}

// The account under the unlock and the Bitwarden login, with the way to another one.
function AccountLine({ account }: { account: Account }) {
  const queryClient = useQueryClient()
  return (
    <Stack direction="row" sx={{ justifyContent: 'center', alignItems: 'center', gap: 1, mt: 2 }}>
      <Typography variant="body2" color="text.secondary">
        Account {accountLabel(account)}
      </Typography>
      <Button size="small" onClick={() => void switchAccount(queryClient, false)}>
        Switch account
      </Button>
    </Stack>
  )
}

// What the account's worker shows: the unlock, the Bitwarden login or the app.
function SessionApp({ account }: { account: Account | null }) {
  const session = useSession()
  const unlocked = session.data?.state === 'unlocked'
  useHeartbeat(unlocked)

  if (session.isPending) {
    return <Loading />
  }
  if (unlocked) {
    return (
      <AutoCommitProvider>
        <DateFormatProvider>
          <Shell account={account} />
        </DateFormatProvider>
      </AutoCommitProvider>
    )
  }
  const bwLogin = session.data?.bwLogin
  const page = bwLogin?.loggedIn === false ? <BwLoginPage bwLogin={bwLogin} /> : <LoginPage />
  return (
    <>
      {page}
      {account !== null && <AccountLine account={account} />}
    </>
  )
}

export default function App() {
  const account = useAccount()
  useLockedHandler()
  useOtherTabs()

  if (account.isPending || account.data?.kind === 'busy') {
    return <Loading />
  }
  if (account.isError) {
    return <AccountPage error="Could not reach bwtidy, try again" />
  }
  switch (account.data.kind) {
    case 'none':
      return <AccountPage error={account.data.error} />
    case 'ready':
      return <SessionApp account={account.data.account} />
    default:
      return <SessionApp account={null} />
  }
}
