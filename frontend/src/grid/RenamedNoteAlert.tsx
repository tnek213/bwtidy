import { Alert, Box, Button, LinearProgress } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { renameToolNoteBack, type TemplatesDoc, templatesQueryKey } from '../api/templates.ts'
import { gridQueryKey } from '../api/grid.ts'
import type { Feedback } from './writer.ts'

const TOOL_NOTE_NAME = 'bwtidy'

// The name the tool note has now, when it is not bwtidy (renamed elsewhere), else null.
function renamedToolNote(doc: TemplatesDoc | undefined): string | null {
  const name = doc?.toolNoteName
  return name !== undefined && name !== TOOL_NOTE_NAME ? name : null
}

interface RenamedNoteAlertProps {
  doc: TemplatesDoc | undefined
  onFeedback: (feedback: Feedback) => void
}

// bwtidy finds its note by id, so a rename elsewhere loses nothing, but the name should say what it is.
// The bar above the alert shows while the rename back runs.
export default function RenamedNoteAlert({ doc, onFeedback }: RenamedNoteAlertProps) {
  const queryClient = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const name = renamedToolNote(doc)

  async function renameBack() {
    const revisionDate = doc?.revisionDate
    if (!revisionDate || renaming) {
      return
    }
    setRenaming(true)
    try {
      const { backup } = await renameToolNoteBack(revisionDate)
      onFeedback({ severity: 'success', message: `bwtidy's note is named bwtidy again. Backup: ${backup}` })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      onFeedback({ severity: 'error', message: `Could not rename the note: ${message}` })
    }
    try {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: templatesQueryKey }),
        queryClient.invalidateQueries({ queryKey: gridQueryKey }),
      ])
    } finally {
      setRenaming(false)
    }
  }

  return (
    <>
      <Box sx={{ height: 4 }}>{renaming && <LinearProgress aria-label="Saving" />}</Box>
      {name !== null && (
        <Alert
          severity="warning"
          sx={{ mx: 1.5, mt: 1 }}
          action={
            <Button color="inherit" size="small" disabled={renaming} onClick={() => void renameBack()}>
              Rename it back
            </Button>
          }
        >
          bwtidy's note is named "{name}" now.
        </Alert>
      )}
    </>
  )
}
