import { Alert, TextField } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { NO_PASSWORD_MANAGER } from '../shared/noPasswordManager.ts'
import { type Display, displayQueryKey, saveDisplay } from '../api/settings.ts'
import { FormDialog } from '../shared/FormDialog.tsx'
import { parseRareFieldsMax, RARE_FIELDS_LIMIT } from './rareFields.ts'
import { useRareFieldsMax } from './display.ts'

const HINT = '0 turns Rare fields off. A field goes in when at most N items use it.'

// Sets the N of the Rare fields column, saved for the account (PUT /api/settings/display).
export default function RareFieldsDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const current = useRareFieldsMax()
  const [text, setText] = useState(String(current))
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const value = parseRareFieldsMax(text)

  async function save() {
    if (value === null) {
      return
    }
    setSaving(true)
    setFailure(null)
    try {
      queryClient.setQueryData<Display>(displayQueryKey, await saveDisplay({ rareFieldsMax: value }))
      onClose()
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error))
      setSaving(false)
    }
  }

  return (
    <FormDialog
      title="Rare fields"
      maxWidth="xs"
      canSubmit={value !== null && !saving}
      onClose={onClose}
      onSubmit={() => void save()}
    >
      {failure && <Alert severity="error">Could not save N: {failure}</Alert>}
      <TextField
        autoFocus
        label="N"
        type="number"
        value={text}
        onChange={(event) => setText(event.target.value)}
        error={value === null}
        helperText={value === null ? `A whole number from 0 to ${RARE_FIELDS_LIMIT}. ${HINT}` : HINT}
        slotProps={{ htmlInput: { ...NO_PASSWORD_MANAGER, min: 0, max: RARE_FIELDS_LIMIT, step: 1 } }}
      />
    </FormDialog>
  )
}
