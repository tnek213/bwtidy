import { Box } from '@mui/material'
import { useEffect, useRef, useState } from 'react'
import { isUiEntry, useAutoCommit } from '../commit/autoCommit.ts'
import { useQueue } from '../api/queue.ts'
import { VISUALLY_HIDDEN } from '../shared/visuallyHidden.ts'

interface Watched {
  committing: boolean
  // Null until the pending list first came, so the proposals already there are not news.
  proposals: number | null
}

export const SAVING_TEXT = 'Saving the changes.'
export const SAVED_TEXT = 'The write is done.'

// What changed without a click and only shows, said in words: a write that starts or ends
// (a failure has its own alert) and new MCP proposals.
export function announcement(before: Watched, now: Watched): string | null {
  const lines: string[] = []
  if (!before.committing && now.committing) {
    lines.push(SAVING_TEXT)
  } else if (before.committing && !now.committing) {
    lines.push(SAVED_TEXT)
  }
  const added = before.proposals !== null && now.proposals !== null ? now.proposals - before.proposals : 0
  if (added > 0) {
    lines.push(added === 1 ? '1 new MCP proposal to review.' : `${added} new MCP proposals to review.`)
  }
  return lines.length > 0 ? lines.join(' ') : null
}

// A polite live region on every page of the app.
export default function WriteAnnouncer() {
  const { committing } = useAutoCommit()
  const pending = useQueue('pending').data
  const proposals = pending ? pending.filter((entry) => !isUiEntry(entry)).length : null
  const watched = useRef<Watched>({ committing, proposals })
  const [said, setSaid] = useState({ text: '', count: 0 })

  useEffect(() => {
    const now = { committing, proposals }
    const text = announcement(watched.current, now)
    watched.current = now
    if (text !== null) {
      setSaid((old) => ({ text, count: old.count + 1 }))
    }
  }, [committing, proposals])

  return (
    <Box role="status" data-testid="announcer" sx={VISUALLY_HIDDEN}>
      {/* A no-break space every other time, so the same words are read out again. */}
      {said.text}
      {said.count % 2 === 1 ? ' ' : ''}
    </Box>
  )
}
