import { Alert, AlertTitle } from '@mui/material'
import { useState } from 'react'
import type { BackupReencryption } from '../api/session.ts'

interface Props {
  result: BackupReencryption | undefined
}

// Shown after a login that re-encrypted the backups with a new master password
// when some files did not decrypt with the stored backup password.
export default function BackupReencryptionAlert({ result }: Props) {
  const [closed, setClosed] = useState(false)
  if (closed || !result || result.failed.length === 0) {
    return null
  }
  return (
    <Alert severity="warning" onClose={() => setClosed(true)} sx={{ borderRadius: 0 }}>
      <AlertTitle>Backups not re-encrypted</AlertTitle>
      The stored backup password did not open the following files, so they were left as they
      are: {result.failed.join(', ')}
    </Alert>
  )
}
