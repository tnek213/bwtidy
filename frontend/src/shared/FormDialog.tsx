import { Button, Dialog, DialogActions, DialogContent, DialogTitle, Stack } from '@mui/material'
import { type FormEvent, type ReactNode, useId } from 'react'

interface FormDialogProps {
  title: string
  submitLabel?: string
  canSubmit: boolean
  onClose: () => void
  onSubmit: () => void
  children: ReactNode
  maxWidth?: 'xs' | 'sm' | 'md'
}

export function FormDialog(props: FormDialogProps) {
  const { title, submitLabel = 'Save', canSubmit, onClose, onSubmit, children, maxWidth = 'sm' } = props
  const titleId = useId()
  function submit(event: FormEvent) {
    event.preventDefault()
    if (canSubmit) {
      onSubmit()
    }
  }
  return (
    <Dialog open onClose={onClose} aria-labelledby={titleId} fullWidth maxWidth={maxWidth}>
      <form onSubmit={submit} noValidate>
        <DialogTitle id={titleId}>{title}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {children}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={!canSubmit}>
            {submitLabel}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
