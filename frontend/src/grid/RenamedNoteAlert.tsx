import { Alert, Box, Button, LinearProgress } from '@mui/material'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { keepToolNoteName, renameToolNoteBack, type TemplatesDoc, templatesQueryKey } from '../api/templates.ts'
import { gridQueryKey } from '../api/grid.ts'
import type { Feedback } from './writer.ts'

const TOOL_NOTE_NAME = 'bwtidy'

// The tool note's name and the name it should have, when the two differ (renamed elsewhere), else null.
function renamedToolNote(doc: TemplatesDoc | undefined): { name: string; expected: string } | null {
  const name = doc?.toolNoteName
  const expected = doc?.toolNoteExpectedName ?? TOOL_NOTE_NAME
  return name !== undefined && name !== expected ? { name, expected } : null
}

interface RenamedNoteAlertProps {
  doc: TemplatesDoc | undefined
  onFeedback: (feedback: Feedback) => void
}

// bwtidy finds its note by id, so a rename elsewhere loses nothing, but the name should say what it is.
// Rename it back gives the note its expected name, Keep this name makes the name it has now the expected one.
// The bar above the alert shows while either runs.
export default function RenamedNoteAlert({ doc, onFeedback }: RenamedNoteAlertProps) {
  const queryClient = useQueryClient()
  const [writing, setWriting] = useState(false)
  const renamed = renamedToolNote(doc)

  async function write(run: () => Promise<{ backup: string }>, done: string, failed: string) {
    setWriting(true)
    try {
      const { backup } = await run()
      onFeedback({ severity: 'success', message: `${done} Backup: ${backup}` })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      onFeedback({ severity: 'error', message: `${failed}: ${message}` })
    }
    try {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: templatesQueryKey }),
        queryClient.invalidateQueries({ queryKey: gridQueryKey }),
      ])
    } finally {
      setWriting(false)
    }
  }

  function renameBack() {
    const revisionDate = doc?.revisionDate
    if (!revisionDate || !renamed || writing) {
      return
    }
    void write(
      () => renameToolNoteBack(revisionDate),
      `bwtidy's note is named "${renamed.expected}" again.`,
      'Could not rename the note',
    )
  }

  function keepName() {
    if (!renamed || writing) {
      return
    }
    void write(
      () => keepToolNoteName(renamed.name),
      `bwtidy's note keeps the name "${renamed.name}".`,
      'Could not keep the name',
    )
  }

  return (
    <>
      <Box sx={{ height: 4 }}>{writing && <LinearProgress aria-label="Saving" />}</Box>
      {renamed !== null && (
        <Alert
          severity="warning"
          sx={{ mx: 1.5, mt: 1 }}
          action={
            <>
              <Button color="inherit" size="small" disabled={writing} onClick={renameBack}>
                Rename it back
              </Button>
              <Button color="inherit" size="small" disabled={writing} onClick={keepName}>
                Keep this name
              </Button>
            </>
          }
        >
          bwtidy's note is named "{renamed.name}" now.
        </Alert>
      )}
    </>
  )
}
