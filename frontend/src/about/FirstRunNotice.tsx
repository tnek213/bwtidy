import { Alert, AlertTitle } from '@mui/material'
import { useId } from 'react'
import { useFirstRun } from '../api/account.ts'
import External from './External.tsx'
import { CAUTION_URL, WORKS_FOR_ME_URL } from './links.ts'

// Above the account form, the Bitwarden login and the unlock while no account ever got past its
// first login (docs/spec.md, The account form). A note, not an alert: nothing went wrong, and
// it must not be read out as an error nor block the form.
export default function FirstRunNotice() {
  const firstRun = useFirstRun()
  const titleId = useId()
  if (!firstRun) {
    return null
  }
  return (
    <Alert severity="info" role="note" aria-labelledby={titleId} sx={{ mb: 2 }}>
      <AlertTitle id={titleId}>Before you start</AlertTitle>
      bwtidy is beta software made with AI, with very few users so far. Read{' '}
      <External href={CAUTION_URL}>the caution on GitHub</External> before you use it on your vault. If bwtidy works
      for you, a thumbs up in <External href={WORKS_FOR_ME_URL}>Works for me</External> on GitHub helps others decide.
    </Alert>
  )
}
