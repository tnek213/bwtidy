import { Alert, DialogContentText, FormControlLabel, Switch } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { FormDialog } from '../shared/FormDialog.tsx'
import { gridQueryKey, type GridRow } from '../api/grid.ts'
import { queueQueryKey } from '../api/queue.ts'
import { deleteItem, type GeneratorOptions, generatorQueryKey, saveItem, useGenerator } from '../api/generator.ts'
import { rowName } from './labels.ts'
import { optionsProblem, refusalOf } from '../shared/generatorRules.ts'
import { describeOptions } from './ownGenerator.ts'
import GeneratorForm from '../shared/GeneratorForm.tsx'

interface ItemGeneratorDialogProps {
  row: GridRow
  onClose: () => void
}

// The item's own generator setting, or the default with the switch on. A refusal stays in the dialog.
export default function ItemGeneratorDialog({ row, onClose }: ItemGeneratorDialogProps) {
  const queryClient = useQueryClient()
  const doc = useGenerator()
  const stored = doc.data?.items[row.id]
  // Null until the user touches one: the switch and the form follow what is stored.
  const [useDefault, setUseDefault] = useState<boolean | null>(null)
  const [draft, setDraft] = useState<GeneratorOptions | null>(null)
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const isDefault = useDefault ?? stored === undefined
  const options = draft ?? stored ?? doc.data?.default

  async function save() {
    setSending(true)
    setRefusal(null)
    try {
      if (isDefault) {
        await deleteItem(row.id)
      } else if (options) {
        await saveItem(row.id, options)
      }
    } catch (error) {
      setRefusal(refusalOf(error))
      setSending(false)
      return
    }
    void queryClient.invalidateQueries({ queryKey: generatorQueryKey })
    void queryClient.invalidateQueries({ queryKey: queueQueryKey })
    // The row's mark for an own setting follows the save.
    void queryClient.invalidateQueries({ queryKey: gridQueryKey })
    onClose()
  }

  const canSubmit = !sending && doc.data !== undefined && (isDefault || (options !== undefined && optionsProblem(options) === null))
  return (
    <FormDialog
      title={`Generator settings for ${rowName(row)}`}
      canSubmit={canSubmit}
      onClose={onClose}
      onSubmit={() => void save()}
    >
      {doc.isError && <Alert severity="error">Could not load the settings: {doc.error.message}</Alert>}
      <FormControlLabel
        label="Use the default"
        control={
          <Switch
            checked={isDefault}
            disabled={doc.data === undefined}
            onChange={(event) => setUseDefault(event.target.checked)}
          />
        }
      />
      {isDefault && doc.data && (
        <DialogContentText>
          A generated value for this item follows the default: {describeOptions(doc.data.default)}.
        </DialogContentText>
      )}
      {!isDefault && options && <GeneratorForm value={options} onChange={setDraft} />}
      {refusal !== null && <Alert severity="error">Nothing was saved: {refusal}</Alert>}
    </FormDialog>
  )
}
